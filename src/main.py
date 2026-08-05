import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.admin_routes import router as admin_router
from src.api.health_routes import router as health_router
from src.api.openai_routes import router as openai_router
from src.api.routes import router as stream_router
from src.core.config import settings
from src.core.request_id import RequestIdMiddleware
from src.db.database import run_migrations
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import ClientRegistry, TurnRegistry
from src.services.reconnection import ConnectionState, to_wire_state
from src.services.session_registry import SessionRegistry
from src.services.tts_reconnecting import ReconnectingTTSClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    logger.info("BD inicializada")

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

    tts = ReconnectingTTSClient(
        url=settings.TTS_WS_URL,
        token=settings.TTS_TOKEN,
        initial_backoff=settings.TTS_RECONNECT_INITIAL_BACKOFF,
        max_backoff=settings.TTS_RECONNECT_MAX_BACKOFF,
    )

    app.state.openclaw = openclaw
    app.state.tts = tts
    app.state.turn_registry = turn_registry
    app.state.client_registry = client_registry
    app.state.session_registry = SessionRegistry()

    yield

    await openclaw.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets.",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(RequestIdMiddleware)

app.include_router(stream_router)  # WS /ws/stream
app.include_router(openai_router)  # HTTP /v1/*
app.include_router(health_router)  # HTTP /healthz, /ready
app.include_router(admin_router)  # HTTP /admin/*

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8004)
