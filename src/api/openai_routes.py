import json
import uuid
import logging
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


@router.get("/models")
async def list_models(request: Request):
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": "openclaw",
                "object": "model",
                "created": 0,
                "owned_by": "openclaw",
            }
        ]
    })


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    # Extract last user message as prompt
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
                        "choices": [{
                            "delta": {"content": event.content},
                            "index": 0,
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
            # Final chunk
            final_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    else:
        tokens = []
        async for event in orchestrator.stream_response(text=text, user_id="ha"):
            if event.type == "token":
                tokens.append(event.content)

        content = "".join(tokens)
        return JSONResponse({
            "id": completion_id,
            "object": "chat.completion",
            "choices": [{
                "message": {"role": "assistant", "content": content},
                "index": 0,
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(tokens), "total_tokens": len(tokens)},
        })
