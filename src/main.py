import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.api.config_routes import router as config_router
from src.api.conversation_routes import router as conversation_router
from src.api.models_routes import router as models_router
from src.api.health_routes import router as health_router
from src.api.openai_routes import router as openai_router
from src.services.db_client import db_client
from src.services.orchestrators.registry import build_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_client.connect()
    registry = build_registry()
    await registry.connect_all()
    app.state.orchestrators = registry
    yield
    await registry.close_all()
    await db_client.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets. Titiritero del Ecosistema IA.",
    version="2.0.0",
    lifespan=lifespan,
)

# WebSocket
app.include_router(stream_router)

# OpenAI-compatible REST (no prefix — /v1/ is in the router itself)
app.include_router(openai_router)

# REST API
app.include_router(config_router, prefix="/api")
app.include_router(conversation_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(health_router, prefix="/api")


@app.get("/health")
def healthcheck():
    """Endpoint simple para probar que el Gateway está arriba"""
    return {
        "status": "online",
        "service": "JotaGateway BFF"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
