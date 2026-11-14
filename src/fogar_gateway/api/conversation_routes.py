"""
conversation_routes.py
~~~~~~~~~~~~~~~~~~~~~~
/admin/conversations — las conversaciones de todos los clientes (M19). Auth: X-Admin-Token.

  GET  /admin/conversations                — lista (filtro `client_id`), la última en hablarse
                                             primero, sin las del día vacías; el título, de OpenClaw
                                             (caché de TITLES_TTL_S; si no responde, null)
  GET  /admin/conversations/{id}/messages  — lo dicho, de `chat.history` (`limit`, 200 por defecto);
                                             503 `orchestrator_unavailable` sin OpenClaw
  POST /admin/conversations                — conversación nueva del panel web (agente validado
                                             como en el handshake)
  GET  /admin/conversations/agents         — los agentes con los que el panel puede empezar una
                                             y el que tocaría sin elegir (para su selector)

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
    ConversationAgents,
    ConversationCreate,
    ConversationIdPath,
    ConversationMessage,
    ConversationMessages,
    ConversationResponse,
    HistoryLimit,
)
from fogar_gateway.services.conversations import (
    ConversationClientMissing,
    ConversationRow,
    history_messages,
)
from fogar_gateway.services.openclaw.client import (
    ORCHESTRATOR_UNAVAILABLE_ERROR,
    OrchestratorRequestError,
)

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


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    request: Request, client_id: ClientIdFilter = None
) -> list[ConversationResponse]:
    rows = await request.app.state.conversations.list_rows(client_id)
    cache = request.app.state.conversation_titles
    titles = await cache.get(request.app.state.openclaw) if rows else {}
    return [_response(row, titles.get(row.record.session_key)) for row in rows]


@router.get("/agents", response_model=ConversationAgents)
async def get_conversation_agents(request: Request) -> ConversationAgents:
    """Los del roster de OpenClaw que el panel puede usar (`allowed_agents`) y el de la cascada;
    sin roster (OpenClaw sin conectar), solo el de la cascada. Mismas reglas que el POST."""
    try:
        _, config = await panel_session()
    except (ClientNotFound, ClientInactive):
        raise HTTPException(status_code=409, detail="panel_client_inactive") from None
    info = request.app.state.openclaw.gateway_info
    allowed = config.allowed_agents
    agents = [a for a in (info.agents if info is not None else []) if allowed is None or a in allowed]
    try:
        default: str | None = resolve_agent(requested=None, client_config=config, gateway_info=info)
    except AgentPolicyError:
        default = None  # el de la cascada no está permitido: hay que elegir
    return ConversationAgents(agents=agents, default=default)


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
    try:
        conversation = await service.create(client.id, agent)
    except ConversationClientMissing:  # el panel, borrado entre medias
        raise HTTPException(status_code=409, detail="panel_client_inactive") from None
    row = await service.row(conversation.id)
    if row is None:  # borrada entre medias (el panel, con sus conversaciones)
        raise HTTPException(status_code=409, detail="panel_client_inactive")
    return _response(row)
