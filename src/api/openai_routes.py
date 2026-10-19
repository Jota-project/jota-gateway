import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from src.core.agent_policy import MODEL_ALIAS, AgentPolicyError, agent_from_model, resolve_agent
from src.core.exceptions import ClientInactive, ClientNotFound
from src.core.network import is_trusted_origin, resolve_client_ip
from src.core.session_key import make_session_key
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client
from src.services.openclaw.client import _traceback_no_message
from src.services.openclaw.registry import TURN_IN_PROGRESS_ERROR
from src.services.orchestration import OrchestratorError, call_orchestrator
from src.services.pipeline_tracker import PipelineTracker, _NullWS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


def _log_unexpected_error(exc: BaseException) -> None:
    """Solo el tipo a ERROR; traceback sin mensaje ni líneas de código a DEBUG (patrón de
    #226): el mensaje de la excepción puede llevar texto de usuario o secretos."""
    logger.error("HTTP orchestrator unexpected error (%s)", type(exc).__name__)
    logger.debug("HTTP orchestrator error traceback (no message): %s", _traceback_no_message(exc))


class _ChatMessage(BaseModel):
    role: str
    content: str = ""


class _ChatCompletionRequest(BaseModel):
    messages: list[_ChatMessage] = []
    stream: bool = False
    model: str = ""


@dataclass
class HACaller:
    """Identidad resuelta para un caller de /v1/*.

    client/config son None en el path legacy (origen de red confiable,
    sin Authorization) — preserva el comportamiento histórico fijo "ha".
    """

    client: Client | None
    config: ClientConfig | None


async def resolve_ha_caller(request: Request) -> HACaller:
    """Dependency de auth para /v1/*.

    - Authorization: Bearer <client_key> presente -> valida contra la BD
      (igual que el handshake WS). Aplica siempre, sin importar el origen.
    - Sin Authorization -> exige que el origen (ver resolve_client_ip) sea
      confiable (loopback y/o TRUSTED_NETWORKS); si no, 401.
    """
    auth = request.headers.get("authorization")
    if auth:
        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication required")
        client_key = auth[len("Bearer ") :]
        try:
            client, config = await db_client.get_session(client_key)
        except (ClientNotFound, ClientInactive):
            raise HTTPException(status_code=401, detail="authentication required")
        return HACaller(client=client, config=config)

    ip = resolve_client_ip(request)
    if is_trusted_origin(ip):
        return HACaller(client=None, config=None)

    raise HTTPException(status_code=401, detail="authentication required")


@router.get("/models")
async def list_models(request: Request, caller: HACaller = Depends(resolve_ha_caller)):
    # Alias first, then the roster agents (#234). The roster is whatever OpenClaw announced
    # (`agents.list`); without it only the alias is advertised. An authenticated client with
    # an explicit `allowed_agents` list only sees the agents it may actually use.
    models = [{"id": MODEL_ALIAS, "object": "model", "created": 0, "owned_by": "jota-gateway"}]
    orchestrator = getattr(request.app.state, "openclaw", None)
    # The legacy trusted-origin caller (no Bearer, no config) gets only the alias: the legacy
    # chat path ignores `model` and always uses the default agent, so advertising agents it
    # cannot actually pick would be misleading.
    gateway_info = getattr(orchestrator, "gateway_info", None) if caller.config else None
    allowed = caller.config.allowed_agents if caller.config is not None else None
    for agent_id in gateway_info.agents if gateway_info is not None else ():
        if agent_id == MODEL_ALIAS or (allowed is not None and agent_id not in allowed):
            continue
        models.append({"id": agent_id, "object": "model", "created": 0, "owned_by": "openclaw"})
    return JSONResponse({"object": "list", "data": models})


def _extract_last_user_message(messages: list[_ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return msg.content
    return ""


def _make_http_tracker(session_id: str, registry, client_id: str) -> PipelineTracker:
    return PipelineTracker(
        session_id=session_id,
        client_id=client_id,
        input_mode="text",
        output_mode=[],
        client_ws=_NullWS(),
        registry=registry,
    )


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: _ChatCompletionRequest,
    caller: HACaller = Depends(resolve_ha_caller),
):
    text = _extract_last_user_message(body.messages)
    stream = body.stream
    orchestrator = request.app.state.openclaw
    session_registry = request.app.state.session_registry
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    # body.model is the requested agent (analogous to handshake.agent in WS), except the
    # advertised model name `jota-gateway`, which means "no explicit agent" (#217, G-36).
    # resolve_agent() normalizes empty/whitespace-only to None so the cascade
    # falls through to client_config.default_agent, then
    # gateway_info.default_agent_id, then the legacy "main" fallback. Issue #105.
    #
    # Trusted-origin legacy path (caller.config is None): skip policy checks,
    # use gateway default directly. This preserves the historical behaviour where
    # body.model was ignored for loopback/trusted-network callers.
    if caller.config is not None:
        try:
            resolved_agent = resolve_agent(
                requested=agent_from_model(body.model),
                client_config=caller.config,
                gateway_info=orchestrator.gateway_info,
            )
        except AgentPolicyError as e:
            return JSONResponse(
                {
                    "error": "forbidden",
                    "reason": (
                        "agent_not_permitted"
                        if e.kind == "not_permitted"
                        else "agent_not_available"
                    ),
                    "message": str(e),
                },
                status_code=403,
            )
    else:
        # Legacy trusted-origin path: no policy checks, use gateway default.
        resolved_agent = (
            orchestrator.gateway_info.default_agent_id if orchestrator.gateway_info else "main"
        )

    client_id = caller.client.id if caller.client else "ha"
    session_key = make_session_key(resolved_agent, client_id)

    tracker = _make_http_tracker(f"http:{completion_id}", session_registry, client_id)
    session_registry.register(tracker)

    if stream:

        async def generate():
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            orchestrator_error: Exception | None = None
            error_message = ""

            async def _on_token(t: str):
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": t}, "index": 0, "finish_reason": None}],
                }
                await queue.put(f"data: {json.dumps(chunk)}\n\n")

            async def _run():
                nonlocal orchestrator_error, error_message
                try:
                    await call_orchestrator(
                        orchestrator,
                        text,
                        session_key,
                        client_id,
                        tracker=tracker,
                        on_token=_on_token,
                    )
                except OrchestratorError as e:
                    # Stable code (orchestrator_error, turn_timeout...): safe to relay (#228).
                    logger.error("HTTP orchestrator error (%s)", e)
                    orchestrator_error = e
                    error_message = str(e)
                except Exception as e:
                    # Cualquier otro fallo no es un éxito truncado (#217, G-37): se cierra como
                    # error sin filtrar el detalle interno al cliente.
                    _log_unexpected_error(e)
                    orchestrator_error = e
                    error_message = "internal_error"
                finally:
                    await queue.put(
                        None
                    )  # sentinel siempre primero — garantiza que el generador puede avanzar
                    try:
                        await tracker.close(
                            status="error" if orchestrator_error is not None else "completed"
                        )
                    except Exception:
                        pass

            task = asyncio.create_task(_run())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    yield item
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

            if orchestrator_error is not None:
                error = {
                    "error": {
                        "message": error_message,
                        "type": "server_error",
                    },
                }
                yield f"data: {json.dumps(error)}\n\n"
                final = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {}, "index": 0, "finish_reason": "error"}],
                }
                yield f"data: {json.dumps(final)}\n\n"
                yield "data: [DONE]\n\n"
                return

            final = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    tokens: list[str] = []

    async def _on_token(t: str):
        tokens.append(t)

    orchestrator_error: Exception | None = None
    try:
        await call_orchestrator(
            orchestrator,
            text,
            session_key,
            client_id,
            tracker=tracker,
            on_token=_on_token,
        )
    except OrchestratorError as e:
        logger.error("HTTP orchestrator error (%s)", e)
        orchestrator_error = e
    except Exception as e:
        # Any other exception (RuntimeError included) may carry raw text: never relayed.
        _log_unexpected_error(e)
        orchestrator_error = RuntimeError("internal_error")
    finally:
        await tracker.close()

    if orchestrator_error:
        if str(orchestrator_error) == TURN_IN_PROGRESS_ERROR:
            return JSONResponse({"error": str(orchestrator_error)}, status_code=409)
        return JSONResponse({"error": str(orchestrator_error)}, status_code=502)

    content = "".join(tokens)
    return JSONResponse(
        {
            "id": completion_id,
            "object": "chat.completion",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "index": 0,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": len(tokens),
                "total_tokens": len(tokens),
            },
        }
    )
