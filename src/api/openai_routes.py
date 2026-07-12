import asyncio
import json
import uuid
import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from src.core.exceptions import ClientInactive, ClientNotFound
from src.core.network import is_trusted_origin, resolve_client_ip
from src.core.session_key import make_session_key
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client
from src.services.orchestration import call_orchestrator
from src.services.pipeline_tracker import PipelineTracker, _NullWS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


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
        client_key = auth[len("Bearer "):]
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
    return JSONResponse({
        "object": "list",
        "data": [{"id": "jota-gateway", "object": "model", "created": 0, "owned_by": "jota-gateway"}],
    })


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
    default_agent = orchestrator.gateway_info.default_agent_id if orchestrator.gateway_info else "main"

    if caller.client is not None:
        client_id = caller.client.id
        system_prompt_extra = caller.config.system_prompt_extra
    else:
        client_id = "ha"
        system_prompt_extra = None

    session_key = make_session_key(default_agent, client_id)

    tracker = _make_http_tracker(f"http:{completion_id}", session_registry, client_id)
    session_registry.register(tracker)

    if stream:
        async def generate():
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def _on_token(t: str):
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": t}, "index": 0, "finish_reason": None}],
                }
                await queue.put(f"data: {json.dumps(chunk)}\n\n")

            async def _run():
                try:
                    await call_orchestrator(
                        orchestrator, text, session_key, client_id,
                        system_prompt_extra=system_prompt_extra,
                        tracker=tracker, on_token=_on_token,
                    )
                except RuntimeError as e:
                    logger.error(f"HTTP orchestrator error: {e}")
                finally:
                    await queue.put(None)  # sentinel siempre primero — garantiza que el generador puede avanzar
                    try:
                        await tracker.close()
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
            orchestrator, text, session_key, client_id,
            system_prompt_extra=system_prompt_extra,
            tracker=tracker, on_token=_on_token,
        )
    except RuntimeError as e:
        logger.error(f"HTTP orchestrator error: {e}")
        orchestrator_error = e
    finally:
        await tracker.close()

    if orchestrator_error:
        return JSONResponse({"error": str(orchestrator_error)}, status_code=502)

    content = "".join(tokens)
    return JSONResponse({
        "id": completion_id,
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": content},
                     "index": 0, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": len(tokens), "total_tokens": len(tokens)},
    })
