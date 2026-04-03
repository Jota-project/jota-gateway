"""
conversation_routes.py
~~~~~~~~~~~~~~~~~~~~~~
GET /api/conversations                      — listar conversaciones
GET /api/conversations/{id}/messages        — mensajes de una conversación
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_verified_client
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client

router = APIRouter()


def _handle_db_error(e: Exception) -> None:
    if isinstance(e, httpx.HTTPStatusError):
        raise HTTPException(status_code=e.response.status_code)
    if isinstance(e, httpx.RequestError):
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    raise HTTPException(status_code=502, detail="Unexpected error")


@router.get("/conversations")
async def get_conversations(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    client, _ = auth
    try:
        return await db_client.get_conversations(client.id)
    except Exception as e:
        _handle_db_error(e)


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    client, _ = auth
    try:
        return await db_client.get_messages(client.id, conversation_id)
    except Exception as e:
        _handle_db_error(e)
