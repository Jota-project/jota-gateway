import json
import uuid
import logging
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from src.core.config import settings
from src.core.session_key import make_session_key
from src.services.orchestration import call_orchestrator
from src.services.pipeline_tracker import PipelineTracker, _NullWS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


@router.get("/models")
async def list_models(request: Request):
    return JSONResponse({
        "object": "list",
        "data": [{"id": "openclaw", "object": "model", "created": 0, "owned_by": "openclaw"}]
    })


def _extract_last_user_message(messages: list) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


def _make_http_tracker(session_id: str, registry) -> PipelineTracker:
    return PipelineTracker(
        session_id=session_id,
        client_id="ha",
        input_mode="text",
        output_mode=[],
        client_ws=_NullWS(),
        registry=registry,
    )


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    text = _extract_last_user_message(messages)
    orchestrator = request.app.state.orchestrators.default()
    session_registry = request.app.state.session_registry
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    session_key = make_session_key(settings.OPENCLAW_DEFAULT_AGENT, "ha")

    tracker = _make_http_tracker(f"http:{completion_id}", session_registry)
    session_registry.register(tracker)

    if stream:
        async def generate():
            try:
                chunks = []

                async def _on_token(t: str):
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {"content": t}, "index": 0, "finish_reason": None}],
                    }
                    chunks.append(f"data: {json.dumps(chunk)}\n\n")

                await call_orchestrator(
                    orchestrator, text, session_key, "ha",
                    tracker=tracker, on_token=_on_token,
                )
                for chunk in chunks:
                    yield chunk
            except RuntimeError as e:
                logger.error(f"HTTP orchestrator error: {e}")
            finally:
                await tracker.close()
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

    try:
        await call_orchestrator(
            orchestrator, text, session_key, "ha",
            tracker=tracker, on_token=_on_token,
        )
    except RuntimeError as e:
        logger.error(f"HTTP orchestrator error: {e}")
    finally:
        await tracker.close()

    content = "".join(tokens)
    return JSONResponse({
        "id": completion_id,
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": content},
                     "index": 0, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": len(tokens), "total_tokens": len(tokens)},
    })
