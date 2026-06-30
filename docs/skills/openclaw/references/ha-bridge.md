# OpenClaw — Home Assistant Bridge (jota-gateway)

## Why a Bridge?

Home Assistant's "OpenAI Conversation" integration expects:
- `POST /v1/chat/completions` (OpenAI REST)
- `GET /v1/models`

OpenClaw **does expose these endpoints** at `:18789` with `Authorization: Bearer <token>`.

The bridge in `jota-gateway` adds value beyond simple proxying:
- Injects voice context (`[voice]` prefix → short responses, no markdown)
- Maps HA's model name to OpenClaw's model
- Handles auth without exposing the token to HA
- Optional: pre/post-processing, logging, rate limiting

## Architecture

```
HA (worker-01)
  POST http://green-house/api/gateway/v1/chat/completions
       │ (no auth needed from HA — LAN is trusted)
       ▼
nginx :80 → /api/gateway/ → jota-gateway :8004
       │ adds Authorization: Bearer <openclaw_token>
       │ optionally prepends [voice] to user message
       ▼
OpenClaw :18789
  POST /v1/chat/completions
  Authorization: Bearer <token>
       │
       ▼
OpenClaw agent responds (with memory, skills, SOUL.md)
       │
       ▼ (streaming SSE or JSON)
HA receives OpenAI-compatible response
```

## Implementation in jota-gateway

Add a new router: `src/api/openclaw_routes.py`

```python
"""
openclaw_routes.py
~~~~~~~~~~~~~~~~~~
OpenAI-compatible proxy to OpenClaw gateway.
POST /v1/chat/completions → OpenClaw :18789/v1/chat/completions
GET  /v1/models           → OpenClaw :18789/v1/models
"""
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from src.core.config import settings

router = APIRouter(prefix="/v1")

OPENCLAW_BASE = f"http://127.0.0.1:{settings.OPENCLAW_PORT}"
OPENCLAW_HEADERS = {
    "Authorization": f"Bearer {settings.OPENCLAW_TOKEN}",
    "Content-Type": "application/json",
}


@router.get("/models")
async def list_models():
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{OPENCLAW_BASE}/v1/models", headers=OPENCLAW_HEADERS)
        return JSONResponse(content=r.json(), status_code=r.status_code)


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()

    # Inject voice context if flagged
    messages = body.get("messages", [])
    if messages and messages[-1].get("role") == "user":
        content = messages[-1].get("content", "")
        if not content.startswith("[voice]"):
            messages[-1]["content"] = f"[voice] {content}"

    stream = body.get("stream", False)

    async def _proxy_stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{OPENCLAW_BASE}/v1/chat/completions",
                headers=OPENCLAW_HEADERS,
                json=body,
            ) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk

    if stream:
        return StreamingResponse(_proxy_stream(), media_type="text/event-stream")
    else:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{OPENCLAW_BASE}/v1/chat/completions",
                headers=OPENCLAW_HEADERS,
                json=body,
            )
            return JSONResponse(content=r.json(), status_code=r.status_code)
```

## Config (.env additions)

```bash
OPENCLAW_PORT=18789
OPENCLAW_TOKEN=<value from ~/.openclaw/openclaw.json → gateway.auth.token>
```

## Register in main.py

```python
from src.api.openclaw_routes import router as openclaw_router
app.include_router(openclaw_router)   # no prefix — /v1/ is in the router itself
```

nginx already has `location /api/gateway/` → `:8004`.  
HA uses: `http://green-house/api/gateway/v1/chat/completions`

## HA Configuration

1. Settings → Devices & Services → Add Integration → **"OpenAI Conversation"**
2. API Key: any string (jota-gateway doesn't verify it for this endpoint, or use a simple env token)
3. Base URL: `http://green-house/api/gateway/v1`
4. Model: `default` (or whatever OpenClaw's `/v1/models` returns)

Then: Settings → Voice Assistants → New Assistant:
- Name: `OpenClaw`
- STT: wyoming-openai (proxy to `/api/stt/`)
- Conversation: OpenClaw (the integration above)
- TTS: jota-speaker or built-in
- Assign to: Huawei P8 Lite satellite

## Voice Context Skill (install in OpenClaw)

Create `~/.openclaw/workspace/skills/voice-context/SKILL.md`:

```markdown
---
name: voice-context
description: >
  When the user message starts with [voice], respond for a voice interface:
  short answers (1-2 sentences), no markdown, no bullet points, no emoji.
  Remove [voice] from your processing — it is a system flag, not user text.
---

# Voice Context

If the message starts with [voice]:
- Strip [voice] from the actual user content
- Respond in 1-2 short sentences maximum
- No markdown formatting (no **, no -, no #)
- No emoji
- Natural spoken language only
```

## Integration Depth

| Capability | Available |
|------------|-----------|
| Natural language with OpenClaw memory | ✅ |
| Personality / SOUL.md | ✅ |
| OpenClaw skills (web, tools…) | ✅ |
| Short voice-friendly responses | ✅ via voice-context skill |
| HA device control (lights, etc.) | ✅ via HA skill in OpenClaw |
| Streaming TTS | ✅ once jota-speaker is running |

## HA Control Skill (future — Fase 3)

OpenClaw can control HA directly via REST API:

```markdown
---
name: home-assistant
description: Use to control Home Assistant devices (lights, switches, climate, etc.)
---

HA URL: http://worker-01.local:8123
Token: <long-lived-access-token from HA Profile>

Use fetch/browser to call:
- GET /api/states — list all entities
- POST /api/services/<domain>/<service> — call a service
  e.g. {"entity_id": "light.living_room"} to /api/services/light/turn_on
```
