"""
conversation_routes.py
~~~~~~~~~~~~~~~~~~~~~~
GET    /api/conversations                      — listar conversaciones
GET    /api/conversations/{id}/messages        — mensajes de una conversación
DELETE /api/conversations/{id}                 — archivar conversación
"""
from fastapi import APIRouter, Depends, status

from src.api.deps import get_verified_client, handle_db_error
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client

router = APIRouter()


@router.get("/conversations")
async def get_conversations(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    client, _ = auth
    try:
        return await db_client.get_conversations(client.id)
    except Exception as e:
        handle_db_error(e)


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    client, _ = auth
    try:
        return await db_client.get_messages(client.id, conversation_id)
    except Exception as e:
        handle_db_error(e)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> None:
    client, _ = auth
    try:
        await db_client.archive_conversation(client.id, conversation_id)
    except Exception as e:
        handle_db_error(e)
