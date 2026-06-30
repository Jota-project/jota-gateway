import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.api.health_routes import router as health_router
from src.api.openai_routes import router as openai_router
from src.api.orchestrator_routes import router as orchestrator_router
from src.api.sessions_routes import router as sessions_router
from src.core.config import settings
from src.services.db_client import db_client
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import TurnRegistry, ClientRegistry
from src.services.session_registry import SessionRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_client.connect()

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

    app.state.openclaw = openclaw
    app.state.turn_registry = turn_registry
    app.state.client_registry = client_registry
    app.state.session_registry = SessionRegistry()

    yield

    await openclaw.close()
    await db_client.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets.",
    version="3.0.0",
    lifespan=lifespan,
)

# WebSocket
app.include_router(stream_router)

# OpenAI-compatible REST
app.include_router(openai_router)

# Health probes
app.include_router(health_router)

# Observability (moved to admin in Task 4)
app.include_router(orchestrator_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
