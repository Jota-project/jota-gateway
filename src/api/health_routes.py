"""
health_routes.py
~~~~~~~~~~~~~~~~
GET /api/health — estado de los servicios internos (sin auth, uso de operador)

Siempre devuelve 200. Los valores por servicio son "ok" o "unavailable".
"""
import asyncio
from fastapi import APIRouter

from src.core.config import settings
from src.services.orchestrator_client import OrchestratorClient
from src.services.transcriber_client import TranscriberClient
from src.services.tts_client import TTSClient

router = APIRouter()


async def _ping_orchestrator() -> str:
    client = OrchestratorClient(
        base_url=settings.ORCHESTRATOR_BASE_URL,
        api_key=settings.GATEWAY_KEY,
        client_id="gateway",
    )
    await client.connect()
    try:
        ok = await client.ping()
        return "ok" if ok else "unavailable"
    finally:
        await client.close()


async def _ping_transcriber() -> str:
    ok = await TranscriberClient.ping(settings.TRANSCRIBER_WS_URL)
    return "ok" if ok else "unavailable"


async def _ping_tts() -> str:
    ok = await TTSClient.ping(settings.TTS_WS_URL)
    return "ok" if ok else "unavailable"


@router.get("/health")
async def health() -> dict:
    results = await asyncio.gather(
        _ping_orchestrator(),
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
