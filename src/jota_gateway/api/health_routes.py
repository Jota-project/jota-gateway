"""
health_routes.py
~~~~~~~~~~~~~~~~
GET /healthz  — liveness: always 200 if the process is running
GET /ready    — readiness: pings OpenClaw (critical), TTS and transcriber (non-critical)

OpenClaw down → 503 "unavailable"
TTS or transcriber down → 200 "degraded"
All ok → 200 "ok"

A provider set to `off` (voice disabled on purpose) still reports "unavailable" in `services`
but does not count for the aggregate `status`: otherwise a monitor that tolerates a permanent
"degraded" would miss a real outage of the other provider.
"""

import asyncio
import logging

from fastapi import APIRouter, Request, Response

from jota_gateway.services.reconnection import describe_error

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


async def _ping_orchestrator(request: Request) -> str:
    try:
        ok = await request.app.state.openclaw.ping()
        return "ok" if ok else "unavailable"
    except Exception as e:  # noqa: BLE001 — sonda de salud: cualquier fallo es «unavailable»
        logger.debug("/ready: ping del orquestador falló (%s)", describe_error(e))
        return "unavailable"


async def _ping_transcriber(request: Request) -> str:
    ok = await request.app.state.speech.stt.probe()
    return "ok" if ok else "unavailable"


async def _ping_tts(request: Request) -> str:
    ok = await request.app.state.speech.tts.probe()
    return "ok" if ok else "unavailable"


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict:
    results = await asyncio.gather(
        _ping_orchestrator(request),
        _ping_transcriber(request),
        _ping_tts(request),
        return_exceptions=True,
    )

    def _resolve(name: str, r) -> str:
        if isinstance(r, BaseException):
            logger.debug("/ready: sonda de %s falló (%s)", name, describe_error(r))
            return "unavailable"
        return r

    services = {
        "orchestrator": _resolve("orchestrator", results[0]),
        "transcriber": _resolve("transcriber", results[1]),
        "tts": _resolve("tts", results[2]),
    }

    speech = request.app.state.speech
    off = {k for k, p in (("transcriber", speech.stt), ("tts", speech.tts)) if p.name == "off"}
    if services["orchestrator"] == "unavailable":
        status = "unavailable"
        response.status_code = 503
    elif any(v == "unavailable" for k, v in services.items() if k not in off):
        status = "degraded"
    else:
        status = "ok"

    return {"status": status, "services": services}
