import json
import uuid
import logging
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response

from src.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


@router.get("/models")
async def list_models(request: Request):
    return JSONResponse({
        "object": "list",
        "data": [{"id": "openclaw", "object": "model", "created": 0, "owned_by": "openclaw"}]
    })


async def _llm_forward(body: dict, stream: bool) -> Response:
    """Forward al LLM configurado (OpenRouter > OpenAI), preservando el contexto completo del caller."""
    if settings.OPENROUTER_API_KEY:
        base_url = "https://openrouter.ai/api/v1"
        api_key = settings.OPENROUTER_API_KEY
        body = dict(body)
        body["model"] = settings.LLM_MODEL
    else:
        base_url = "https://api.openai.com/v1"
        api_key = settings.OPENAI_API_KEY

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    if stream:
        async def generate():
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                async with client.stream("POST", f"{base_url}/chat/completions",
                                          json=body, headers=headers) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        return StreamingResponse(generate(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post(f"{base_url}/chat/completions", json=body, headers=headers)
        return JSONResponse(resp.json())


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    if settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY:
        return await _llm_forward(body, stream)

    # Legacy: route through OpenClaw orchestrator
    text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            text = msg.get("content", "")
            break

    registry = request.app.state.orchestrators
    orchestrator = registry.default()
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if stream:
        async def generate():
            async for event in orchestrator.stream_response(text=text, user_id="ha"):
                if event.type == "token":
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {"content": event.content}, "index": 0, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
            final = {"id": completion_id, "object": "chat.completion.chunk",
                     "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(generate(), media_type="text/event-stream")

    tokens = []
    async for event in orchestrator.stream_response(text=text, user_id="ha"):
        if event.type == "token":
            tokens.append(event.content)
    content = "".join(tokens)
    return JSONResponse({
        "id": completion_id, "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": content}, "index": 0, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": len(tokens), "total_tokens": len(tokens)},
    })
