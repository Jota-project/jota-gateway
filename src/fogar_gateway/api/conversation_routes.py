"""
conversation_routes.py
~~~~~~~~~~~~~~~~~~~~~~
/admin/conversations — las conversaciones de todos los clientes (M19). Auth: X-Admin-Token.

  GET  /admin/conversations                — lista (filtro `client_id`), la última en hablarse
                                             primero; el título, de OpenClaw si responde (si no, null)
  GET  /admin/conversations/{id}/messages  — lo dicho, de `chat.history` (`limit`, 200 por defecto);
                                             503 `orchestrator_unavailable` sin OpenClaw
  POST /admin/conversations                — conversación nueva del panel web (agente validado
                                             como en el handshake)

El gateway no guarda texto: títulos y mensajes se leen de OpenClaw en cada petición y nunca se
registran en el log.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from fogar_gateway.api.admin_routes import panel_session
from fogar_gateway.api.deps import get_admin_auth
from fogar_gateway.core.agent_policy import AgentPolicyError, resolve_agent
from fogar_gateway.core.exceptions import ClientInactive, ClientNotFound
from fogar_gateway.models.admin_schemas import (
    ClientIdFilter,
    ConversationCreate,
    ConversationIdPath,
    ConversationMessage,
    ConversationMessages,
    ConversationResponse,
    HistoryLimit,
)
from fogar_gateway.services.conversations import (
    ConversationRow,
    history_messages,
    list_openclaw_sessions,
    session_title,
)
from fogar_gateway.services.openclaw.client import (
    ORCHESTRATOR_UNAVAILABLE_ERROR,
    OrchestratorRequestError,
)
from fogar_gateway.services.reconnection import describe_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/conversations", dependencies=[Depends(get_admin_auth)])


def _response(row: ConversationRow, title: str | None = None) -> ConversationResponse:
    r = row.record
    return ConversationResponse(
        id=r.id,
        client_id=r.client_id,
        client_name=row.client_name,
        agent=r.agent,
        kind=r.kind,
        day=r.day,
        created_at=r.created_at,
        last_turn_at=r.last_turn_at,
        turn_count=r.turn_count,
        title=title,
    )


async def _titles(orchestrator) -> dict[str, str]:
    """`sessionKey` → título, de `sessions.list`. Sin OpenClaw, ninguno: la lista no falla."""
    try:
        sessions = await list_openclaw_sessions(orchestrator)
    except Exception as e:  # noqa: BLE001 — frontera: sin OpenClaw la lista sale sin títulos
        logger.info("Conversaciones sin títulos: OpenClaw no responde (%s)", describe_error(e))
        return {}
    return {row["key"]: title for row in sessions if (title := session_title(row)) is not None}


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    request: Request, client_id: ClientIdFilter = None
) -> list[ConversationResponse]:
    rows = await request.app.state.conversations.list_rows(client_id)
    titles = await _titles(request.app.state.openclaw) if rows else {}
    return [_response(row, titles.get(row.record.session_key)) for row in rows]


@router.get("/{conversation_id}/messages", response_model=ConversationMessages)
async def get_conversation_messages(
    conversation_id: ConversationIdPath, request: Request, limit: HistoryLimit = 200
) -> ConversationMessages:
    conversation = await request.app.state.conversations.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        payload = await request.app.state.openclaw.request(
            "chat.history", {"sessionKey": conversation.session_key, "limit": limit}
        )
    except OrchestratorRequestError as e:
        code = str(e)  # estable: orchestrator_unavailable | orchestrator_error
        unavailable = code == ORCHESTRATOR_UNAVAILABLE_ERROR
        raise HTTPException(status_code=503 if unavailable else 502, detail=code) from None
    return ConversationMessages(
        conversation_id=conversation.id,
        messages=[ConversationMessage(**m) for m in history_messages(payload.get("messages"))],
        has_more=bool(payload.get("hasMore")),
    )


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(body: ConversationCreate, request: Request) -> ConversationResponse:
    try:
        client, config = await panel_session()
    except (ClientNotFound, ClientInactive):
        raise HTTPException(status_code=409, detail="panel_client_inactive") from None
    try:
        agent = resolve_agent(
            requested=body.agent,
            client_config=config,
            gateway_info=request.app.state.openclaw.gateway_info,
        )
    except AgentPolicyError as e:
        raise HTTPException(status_code=422, detail=f"agent_{e.kind}") from None
    service = request.app.state.conversations
    conversation = await service.create(client.id, agent)
    row = await service.row(conversation.id)
    if row is None:  # borrada entre medias (el panel, con sus conversaciones)
        raise HTTPException(status_code=409, detail="panel_client_inactive")
    return _response(row)
