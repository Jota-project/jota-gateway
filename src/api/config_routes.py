"""
config_routes.py
~~~~~~~~~~~~~~~~
GET /api/config        — leer configuración del cliente
PUT /api/config        — actualizar campos (patch parcial)
POST /api/config/reset — restaurar defaults
"""
from typing import Any

from fastapi import APIRouter, Body, Depends

from src.api.deps import get_verified_client, handle_db_error
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client

router = APIRouter()


@router.get("/config", response_model=ClientConfig)
async def get_config(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> ClientConfig:
    client, _ = auth
    try:
        return await db_client.get_config(client.id)
    except Exception as e:
        handle_db_error(e)


@router.put("/config", response_model=ClientConfig)
async def update_config(
    body: dict[str, Any] = Body(...),
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> ClientConfig:
    client, _ = auth
    try:
        return await db_client.update_config(client.id, body)
    except Exception as e:
        handle_db_error(e)


@router.post("/config/reset", response_model=ClientConfig)
async def reset_config(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> ClientConfig:
    client, _ = auth
    try:
        return await db_client.reset_config(client.id)
    except Exception as e:
        handle_db_error(e)
