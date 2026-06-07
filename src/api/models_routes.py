"""
models_routes.py
~~~~~~~~~~~~~~~~
GET /api/models — lista de modelos disponibles (proxy a jota-db)
"""
from fastapi import APIRouter, Depends

from src.api.deps import get_verified_client, handle_db_error
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client

router = APIRouter()


@router.get("/models")
async def get_models(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    try:
        return await db_client.get_models()
    except Exception as e:
        handle_db_error(e)
