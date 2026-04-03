import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.api.routes import router as stream_router
from src.services.db_client import db_client

# Configuración Base de Logs para el Gateway
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_client.connect()
    yield
    await db_client.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets. Titiritero del Ecosistema IA.",
    version="1.0.0",
    lifespan=lifespan,
)

# Incluir routers
app.include_router(stream_router)


@app.get("/health")
def healthcheck():
    """Endpoint simple para probar que el Gateway está arriba"""
    return {
        "status": "online",
        "service": "JotaGateway BFF"
    }

if __name__ == "__main__":
    import uvicorn
    # Punto de entrada para tests rápidos, normalmente se levanta por Docker uvicorn directo.
    uvicorn.run(app, host="0.0.0.0", port=8004)
