"""
health_routes.py
~~~~~~~~~~~~~~~~
GET /healthz  — liveness: always 200 if the process is running
GET /ready    — readiness: pings OpenClaw (critical), TTS and transcriber (non-critical)

OpenClaw down → 503 "unavailable"
TTS or transcriber down → 200 "degraded"
All ok → 200 "ok"
"""

import asyncio

from fastapi import APIRouter, Request, Response

from src.core.config import settings
from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


async def _ping_orchestrator(request: Request) -> str:
    try:
        ok = await request.app.state.openclaw.ping()
        return "ok" if ok else "unavailable"
    except Exception:
        return "unavailable"


async def _ping_transcriber() -> str:
    ok = await TranscriberClient.ping(settings.TRANSCRIBER_WS_URL)
    return "ok" if ok else "unavailable"


async def _ping_tts() -> str:
    ok = await TTSClient.ping(settings.TTS_WS_URL)
    return "ok" if ok else "unavailable"


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict:
    results = await asyncio.gather(
        _ping_orchestrator(request),
        _ping_transcriber(),
        _ping_tts(),
        return_exceptions=True,
    )

    def _resolve(r) -> str:
        return "unavailable" if isinstance(r, Exception) else r

    services = {
        "orchestrator": _resolve(results[0]),
        "transcriber": _resolve(results[1]),
        "tts": _resolve(results[2]),
    }

    if services["orchestrator"] == "unavailable":
        status = "unavailable"
        response.status_code = 503
    elif any(v == "unavailable" for v in services.values()):
        status = "degraded"
    else:
        status = "ok"

    return {"status": status, "services": services}
