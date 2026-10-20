import asyncio
import logging
import math
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.admin_routes import router as admin_router
from src.api.health_routes import router as health_router
from src.api.openai_routes import router as openai_router
from src.api.routes import router as stream_router
from src.core.config import settings
from src.core.network import warn_if_open_proxy_config
from src.core.request_id import RequestIdMiddleware
from src.core.startup_checks import (
    acquire_single_process_lock,
    check_speech_settings,
    release_single_process_lock,
    warn_insecure_tokens,
)
from src.core.transport import TransportCaptureMiddleware
from src.db.database import dispose_engine, run_migrations
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import ClientRegistry, TurnRegistry
from src.services.reconnection import ConnectionState, describe_error, to_wire_state
from src.services.session_registry import SessionRegistry
from src.services.speech.factory import build_stt_provider, build_tts_provider

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # M6f (#218): un solo proceso por base de datos. El estado vivo (sesiones, cachés,
    # ClientRegistry) es por proceso, así que varios workers sobre la misma BD no están
    # soportados. El lock se toma antes que nada y se suelta al final del apagado; si el
    # arranque falla a medias, también (finally). El SO lo libera si el proceso muere.
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
    # Fatal en arranque: un proveedor de pago mal configurado no debe esperar al primer uso.
    check_speech_settings(
        settings, stt_provider=settings.STT_PROVIDER, tts_provider=settings.TTS_PROVIDER
    )

    turn_registry = TurnRegistry()
    client_registry = ClientRegistry()
    dispatcher = FrameDispatcher(turn_registry, client_registry)
    inner = OpenClawClient(
        host=settings.OPENCLAW_HOST,
        port=settings.OPENCLAW_PORT,
        token=settings.OPENCLAW_TOKEN,
        turn_registry=turn_registry,
        dispatcher=dispatcher,
    )
    openclaw = ReconnectingOpenClawClient(
        inner,
        name="openclaw",
        initial_backoff=settings.ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF,
        max_backoff=settings.ORCHESTRATOR_RECONNECT_MAX_BACKOFF,
        max_duration=settings.ORCHESTRATOR_RECONNECT_MAX_DURATION,
    )
    try:
        await openclaw.connect()
    except Exception as e:
        logger.error(f"Initial OpenClaw connect failed: {e}")

    # Wired *after* the initial connect on purpose: on_state_change firing
    # CONNECTED on a normal first-time success would send a spurious
    # "restored" notice to every connected-but-idle client, even though
    # nothing was ever broken.
    # `_notification_tasks` holds a strong reference to each fire-and-forget
    # broadcast task so the event loop's weak reference doesn't let it get
    # GC'd mid-flight; discarded once the task completes.
    _notification_tasks: set[asyncio.Task] = set()

    def _on_orchestrator_state_change(state: ConnectionState) -> None:
        task = asyncio.create_task(
            client_registry.broadcast_status("orchestrator", to_wire_state(state))
        )
        _notification_tasks.add(task)
        task.add_done_callback(_notification_tasks.discard)

    openclaw.on_state_change = _on_orchestrator_state_change

    # Proveedores de voz singleton (STT_PROVIDER / TTS_PROVIDER), inyectados al bridge.
    stt = build_stt_provider(settings)
    tts = build_tts_provider(settings)

    # Unlike openclaw/transcriber, TTS has no lifespan-level initial connect to
    # wire "after" — ReconnectingTTSClient starts CONNECTED by construction and
    # only fires on_state_change on an actual transition (issue #117), so
    # wiring it immediately can't produce a spurious first-time "restored".
    def _on_tts_state_change(state: ConnectionState) -> None:
        task = asyncio.create_task(client_registry.broadcast_status("tts", to_wire_state(state)))
        _notification_tasks.add(task)
        task.add_done_callback(_notification_tasks.discard)

    tts.on_state_change = _on_tts_state_change

    def _on_stt_state_change(state: ConnectionState) -> None:
        task = asyncio.create_task(
            client_registry.broadcast_status("transcriber", to_wire_state(state))
        )
        _notification_tasks.add(task)
        task.add_done_callback(_notification_tasks.discard)

    # Only the orchestrator is fatal: a provider whose start() fails degrades, it must
    # not abort startup with OpenClaw already connected and never closed (#110).
    for provider in (stt, tts):
        try:
            await provider.start()
        except Exception as e:
            logger.error("Speech provider %s failed to start (%s)", provider.name, describe_error(e))
    # Wired after start() on purpose (same reason as the orchestrator): a first-time
    # success must not broadcast a spurious "restored".
    stt.on_state_change = _on_stt_state_change

    app.state.openclaw = openclaw
    app.state.stt = stt
    app.state.tts = tts
    app.state.turn_registry = turn_registry
    app.state.client_registry = client_registry
    app.state.session_registry = SessionRegistry()

    try:
        yield
    finally:
        # 1. Drain active sessions first. Each bridge's close_all() has its
        #    own inner bound on the _active_turn wait (SHUTDOWN_DRAIN_S, see
        #    JotaBridge.close_all) — but the outer wait_for here uses the
        #    same duration and starts at roughly the same time, so for a
        #    genuinely stuck turn THIS bound is the one that actually fires:
        #    close_all_sessions cancels a bridge's close_all() coroutine
        #    while it's still suspended inside its own inner wait_for,
        #    before transcriber.close()/tracker.close()/_closed=True ever
        #    run for that session. Acceptable here — the process is exiting
        #    right after regardless — but not something to rely on for a
        #    mid-lifetime call to close_all_sessions, should one ever exist.
        await client_registry.close_all_sessions(
            status="shutdown", timeout=settings.SHUTDOWN_DRAIN_S
        )

        # 1b. Close the speech providers once the sessions that used them are gone.
        for provider in (stt, tts):
            try:
                await provider.aclose()
            except Exception:
                logger.exception("Error closing speech provider during shutdown")

        # 2. Close OpenClaw now that active turns have drained.
        try:
            await openclaw.close()
        except Exception:
            logger.exception("Error closing OpenClaw client during shutdown")

        # 3. Cancel/await the orchestrator/TTS state-change broadcast tasks
        #    created via on_state_change above. Done *after* closing
        #    OpenClaw (not before): closing it cancels the only background
        #    loop that could still schedule a new one
        #    (ReconnectingOpenClawClient's own _reconnect_task, via
        #    _set_state()) — so nothing can add to _notification_tasks
        #    between here and the gather below.
        for task in list(_notification_tasks):
            if not task.done():
                task.cancel()
        if _notification_tasks:
            await asyncio.gather(*_notification_tasks, return_exceptions=True)

        # 4. Dispose the DB engine last — nothing above touches the DB.
        dispose_engine()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets.",
    version="3.0.0",
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

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8004)
