"""
health_routes.py
~~~~~~~~~~~~~~~~
GET /api/health — estado de los servicios internos (sin auth, uso de operador)

Siempre devuelve 200. Los valores por servicio son "ok" o "unavailable".
"""
import asyncio
from fastapi import APIRouter, Request

from src.core.config import settings
from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient

router = APIRouter()


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


@router.get("/health")
async def health(request: Request) -> dict:
    results = await asyncio.gather(
        _ping_orchestrator(request),
        _ping_transcriber(),
        _ping_tts(),
        return_exceptions=True,
    )

    def _resolve(r) -> str:
        if isinstance(r, Exception):
            return "unavailable"
        return r

    return {
        "orchestrator": _resolve(results[0]),
        "transcriber": _resolve(results[1]),
        "tts": _resolve(results[2]),
    }
