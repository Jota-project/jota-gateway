import asyncio
import logging
import math
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fogar_gateway import paths
from fogar_gateway.api.admin_routes import router as admin_router
from fogar_gateway.api.conversation_routes import router as conversation_router
from fogar_gateway.api.health_routes import router as health_router
from fogar_gateway.api.openai_routes import router as openai_router
from fogar_gateway.api.pairing_routes import admin_router as pairing_admin_router
from fogar_gateway.api.pairing_routes import public_router as pairing_public_router
from fogar_gateway.api.routes import router as stream_router
from fogar_gateway.api.web_routes import WEB_DIR, web_available
from fogar_gateway.api.web_routes import router as web_router
from fogar_gateway.core.config import settings
from fogar_gateway.core.logging import configure_logging
from fogar_gateway.core.network import warn_if_open_proxy_config
from fogar_gateway.core.request_id import RequestIdMiddleware
from fogar_gateway.core.startup_checks import (
    acquire_single_process_lock,
    check_speech_settings,
    release_single_process_lock,
    warn_deprecated_settings,
    warn_insecure_tokens,
)
from fogar_gateway.core.transport import TransportCaptureMiddleware
from fogar_gateway.db.database import dispose_engine, require_database, run_migrations
from fogar_gateway.services.conversations import ConversationService, LegacyImport, TitleCache
from fogar_gateway.services.openclaw.client import OpenClawClient
from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
from fogar_gateway.services.openclaw.models import OrchestratorAccess
from fogar_gateway.services.openclaw.reconnecting import ReconnectingOpenClawClient
from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry
from fogar_gateway.services.pairing import PairingStore
from fogar_gateway.services.reconnection import (
    ConnectionState,
    describe_error,
    to_wire_state,
    traceback_no_message,
)
from fogar_gateway.services.session_registry import SessionRegistry
from fogar_gateway.services.speech.factory import build_stt_provider, build_tts_provider
from fogar_gateway.services.speech.registry import SpeechProviders
from fogar_gateway.services.teardown import Teardown

configure_logging(settings.LOG_FORMAT)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # M6f (#218): un solo proceso por base de datos. El estado vivo (sesiones, cachés,
    # ClientRegistry) es por proceso, así que varios workers sobre la misma BD no están
    # soportados. El lock se toma antes que nada y se suelta al final del apagado; si el
    # arranque falla a medias, también (finally). El SO lo libera si el proceso muere.
    # Regla 5 de las rutas (M14c): dónde está cada cosa, antes de tocar nada. De la URL de una BD
    # que no es un fichero solo sale el esquema (podría llevar contraseña).
    config = paths.config_file()
    logger.info(
        "Rutas: base %s · configuración %s%s · BD %s", paths.home(), config,
        "" if config.is_file() else " (no existe)",
        paths.sqlite_file(settings.DATABASE_URL) or settings.DATABASE_URL.split(":", 1)[0],
    )
    if not web_available():
        logger.warning("Sin la web compilada en %s: / no sirve el panel (la API va igual)", WEB_DIR)
    # Decisión 5 de M14c: sin BD se para aquí, antes de que el candado cree data/ y gateway.lock.
    require_database()
    lock_handle = None
    if settings.SINGLE_PROCESS_LOCK:
        lock_handle = acquire_single_process_lock(settings.DATABASE_URL)
    try:
        async with _lifespan_body(app):
            yield
    finally:
        release_single_process_lock(lock_handle)


@asynccontextmanager
async def _lifespan_body(app: FastAPI):
    run_migrations()
    logger.info("BD inicializada")
    warn_if_open_proxy_config()
    warn_insecure_tokens(settings)
    warn_deprecated_settings(settings)
    # Fatal en arranque: un proveedor de pago mal configurado no debe esperar al primer uso.
    check_speech_settings(
        settings, stt_provider=settings.STT_PROVIDER, tts_provider=settings.TTS_PROVIDER
    )

    # Conversaciones (M19): el índice sessionKey → conversación, cargado de la BD, reparte el push.
    conversations = ConversationService()
    await conversations.load()
    turn_registry = TurnRegistry()
    client_registry = ClientRegistry(conversations.index)
    dispatcher = FrameDispatcher(turn_registry, client_registry)
    # Acceso a OpenClaw (permisos, emparejamiento): lo actualiza el cliente, lo lee /admin/state.
    openclaw_access = OrchestratorAccess()
    inner = OpenClawClient(
        host=settings.OPENCLAW_HOST,
        port=settings.OPENCLAW_PORT,
        tls=settings.OPENCLAW_TLS,
        token=settings.OPENCLAW_TOKEN,
        turn_registry=turn_registry,
        dispatcher=dispatcher,
        access=openclaw_access,
    )
    openclaw = ReconnectingOpenClawClient(
        inner,
        name="openclaw",
        initial_backoff=settings.ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF,
        max_backoff=settings.ORCHESTRATOR_RECONNECT_MAX_BACKOFF,
    )
    # Never raises: a failed first connect is logged and retried in the background.
    await openclaw.connect()

    # Wired *after* the initial connect on purpose: on_state_change firing
    # CONNECTED on a normal first-time success would send a spurious
    # "restored" notice to every connected-but-idle client, even though
    # nothing was ever broken.
    # `_notification_tasks` holds a strong reference to each fire-and-forget
    # broadcast task so the event loop's weak reference doesn't let it get
    # GC'd mid-flight; discarded (and its failure logged) once it completes.
    _notification_tasks: set[asyncio.Task] = set()

    def _notification_done(task: asyncio.Task) -> None:
        _notification_tasks.discard(task)
        if not task.cancelled() and (e := task.exception()) is not None:
            logger.warning("Aviso de estado a los clientes falló (%s)", describe_error(e))
            logger.debug("aviso de estado: %s", traceback_no_message(e))

    def _broadcaster(service: str) -> Callable[[ConnectionState], None]:
        def _on_state_change(state: ConnectionState) -> None:
            task = asyncio.create_task(
                client_registry.broadcast_status(service, to_wire_state(state))
            )
            _notification_tasks.add(task)
            task.add_done_callback(_notification_done)

        return _on_state_change

    # Las sesiones de antes de M19 se importan una vez, con OpenClaw conectado (no al arrancar:
    # puede estar caído); si falla, se reintenta en la siguiente conexión.
    legacy_import = LegacyImport(conversations, openclaw)
    broadcast_orchestrator = _broadcaster("orchestrator")

    def _on_orchestrator_state(state: ConnectionState) -> None:
        broadcast_orchestrator(state)
        if state == ConnectionState.CONNECTED:
            legacy_import.kick()

    openclaw.on_state_change = _on_orchestrator_state
    if openclaw.status().state == ConnectionState.CONNECTED:
        legacy_import.kick()

    # Proveedores de voz singleton (STT_PROVIDER / TTS_PROVIDER), inyectados al bridge.
    stt = build_stt_provider(settings)
    tts = build_tts_provider(settings)

    # Only the orchestrator is fatal: a provider whose start() fails degrades, it must
    # not abort startup with OpenClaw already connected and never closed (#110).
    for provider in (stt, tts):
        try:
            await provider.start()
        except Exception as e:  # noqa: BLE001 — frontera: solo OpenClaw es fatal (#110)
            logger.error("Speech provider %s failed to start (%s)", provider.name, describe_error(e))
    # Wired after start() on purpose (same reason as the orchestrator): a first-time
    # success must not broadcast a spurious "restored".
    stt.on_state_change = _broadcaster("transcriber")
    tts.on_state_change = _broadcaster("tts")
    speech = SpeechProviders(stt, tts)

    app.state.openclaw = openclaw
    app.state.openclaw_access = openclaw_access
    app.state.speech = speech
    app.state.turn_registry = turn_registry  # read by routes.py for queue depths (#219)
    app.state.client_registry = client_registry
    app.state.conversations = conversations
    app.state.conversation_titles = TitleCache()  # títulos de /admin/conversations
    app.state.legacy_import = legacy_import
    app.state.session_registry = SessionRegistry()
    app.state.pairing = PairingStore()  # códigos de emparejamiento, en memoria (#317)

    try:
        yield
    finally:
        # uvicorn closes every WebSocket (1012) and waits for its handler before this
        # runs, so each session has already closed itself (close_all) by now.
        # 1. Unhook the state-change broadcasts first (G-68): nothing closing below
        #    may schedule a new notification task.
        openclaw.on_state_change = None
        stt.on_state_change = None
        tts.on_state_change = None
        # Every step runs even if the lifespan is cancelled; failures go to DEBUG (#221).
        async with Teardown("apagado") as td:
            # 2. Speech providers, then OpenClaw.
            for provider in (speech.stt, speech.tts):
                await td.step(provider.aclose(), f"cierre del proveedor {provider.name}")
            await td.step(legacy_import.aclose(), "importación de conversaciones")
            await td.step(app.state.conversation_titles.aclose(), "títulos de conversaciones")
            await td.step(openclaw.close(), "cierre de OpenClaw")
            # 3. Notification tasks still in flight.
            for task in list(_notification_tasks):
                await td.cancel(task, "aviso de estado")
            # 4. Dispose the DB engine last — nothing above touches the DB.
            dispose_engine()


app = FastAPI(
    title="Fogar Gateway",
    description="Puerta de entrada de Fogar en casa: aparatos, OpenClaw, voz y administración.",
    lifespan=lifespan,
)



def _json_safe(value):
    """Sustituye NaN/Infinity por su texto: no son JSON válido y romperían el 422."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    # Mismo cuerpo que el handler por defecto de FastAPI ({"detail": [...]}), pero un
    # `NaN`/`Infinity` en la entrada (el 'input' del error) ya no convierte el 422 en 500 (#217).
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": _json_safe(exc.errors())}),
    )


app.add_middleware(RequestIdMiddleware)
# Captures uvicorn's transport per WebSocket so a slow client can be dropped (issue #130 S2).
app.add_middleware(TransportCaptureMiddleware)

app.include_router(stream_router)  # WS /ws/stream
app.include_router(openai_router)  # HTTP /v1/*
app.include_router(health_router)  # HTTP /healthz, /ready
app.include_router(admin_router)  # HTTP /admin/*
app.include_router(conversation_router)  # HTTP /admin/conversations
app.include_router(pairing_public_router)  # HTTP /.well-known/fogar-gateway, /pair
app.include_router(pairing_admin_router)  # HTTP /admin/pairing
# La web va la última: su GET /{path} recoge lo que no es de la API (rutas de Vue).
app.include_router(web_router)  # HTTP / (panel)
