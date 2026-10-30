"""
admin_routes.py
~~~~~~~~~~~~~~~
/admin/* — gestión y observabilidad del gateway.

Auth: X-Admin-Token header validado contra ADMIN_TOKEN env var (via get_admin_auth).

Clientes:
  GET    /admin/clients              — lista clientes
  POST   /admin/clients              — crear cliente (devuelve client_key generada)
  GET    /admin/clients/{id}         — detalle de un cliente
  PATCH  /admin/clients/{id}         — actualizar campos (nombre, config, etc.)
  DELETE /admin/clients/{id}         — borrar cliente
  POST   /admin/clients/{id}/rotate-key — regenerar client_key

Observabilidad:
  GET    /admin/sessions             — sesiones en memoria
  GET    /admin/sessions/{id}        — detalle de sesión
  GET    /admin/orchestrators/{name}/status
  POST   /admin/orchestrators/{name}/reconnect  (202)
  GET    /admin/transcriber/status
  GET    /admin/tts/status
  GET    /admin/state                — instantánea viva (gateway, servicios, totales, sesiones)
  GET    /admin/state/stream         — la misma instantánea por SSE cada STATE_STREAM_INTERVAL_S
"""

import asyncio
import json
import logging
import secrets
import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from src.api.deps import get_admin_auth
from src.db.database import get_db_session
from src.db.models import ClientRecord
from src.models.admin_schemas import ClientCreate, ClientResponse, ClientUpdate
from src.services.reconnection import ServiceStatus
from src.services.stats import gateway_stats

_ROTATE_KEY_ATTEMPTS = 3
STATE_STREAM_INTERVAL_S = 1.0
# Vida máxima de cada stream: por debajo del --timeout-graceful-shutdown (35 s) de la
# imagen, para que un panel abierto no bloquee el apagado; el panel reconecta.
STATE_STREAM_MAX_S = 25.0

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", dependencies=[Depends(get_admin_auth)])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_404(session: Session, client_id: str) -> ClientRecord:
    rec = session.get(ClientRecord, client_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return rec


def _check_agent_consistency(default_agent: str | None, allowed_agents: list[str] | None) -> None:
    """422 si el estado resultante deja `default_agent` fuera de `allowed_agents`.

    `allowed_agents is None` = sin restricción; `[]` = todo denegado (#105), así que
    un `default_agent` con `[]` es inconsistente.
    """
    if default_agent is None or allowed_agents is None:
        return
    if default_agent not in allowed_agents:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="default_agent must be one of allowed_agents",
        )


def _lists_to_json(data: dict[str, Any]) -> dict[str, Any]:
    """Serializa a JSON las listas que la BD guarda como texto (si vienen y no son null)."""
    for field in ("allowed_agents", "output_mode"):
        if data.get(field) is not None:
            data[field] = json.dumps(data[field])
    return data


# ---------------------------------------------------------------------------
# Client CRUD
# ---------------------------------------------------------------------------


@router.get("/clients", response_model=list[ClientResponse])
def list_clients(session: Session = Depends(get_db_session)) -> list[ClientResponse]:
    records = session.exec(select(ClientRecord)).all()
    return [ClientResponse.from_record(r) for r in records]


@router.post("/clients", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
def create_client(
    body: ClientCreate,
    session: Session = Depends(get_db_session),
) -> ClientResponse:
    _check_agent_consistency(body.default_agent, body.allowed_agents)
    data = _lists_to_json(body.model_dump())
    data["client_key"] = data["client_key"] or secrets.token_urlsafe(32)
    record = ClientRecord(**data)
    session.add(record)
    try:
        session.commit()
    except IntegrityError:
        # client_key duplicada (única restricción alcanzable tras validar la entrada).
        # `from None` y sin loguear la excepción: su mensaje/parámetros contienen la
        # client_key completa (credencial bearer, G-27a).
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="client_key already exists"
        ) from None
    session.refresh(record)
    return ClientResponse.from_record(record)


@router.get("/clients/{client_id}", response_model=ClientResponse)
def get_client(
    client_id: str,
    session: Session = Depends(get_db_session),
) -> ClientResponse:
    return ClientResponse.from_record(_get_or_404(session, client_id))


@router.patch("/clients/{client_id}", response_model=ClientResponse)
def update_client(
    client_id: str,
    body: ClientUpdate,
    session: Session = Depends(get_db_session),
) -> ClientResponse:
    rec = _get_or_404(session, client_id)
    patch = body.model_dump(exclude_unset=True)
    # Solo si el PATCH toca alguno de los dos campos: una fila antigua inconsistente
    # que se edita por otros campos no debe fallar (solo se valida la entrada, #122).
    if "default_agent" in patch or "allowed_agents" in patch:
        _check_agent_consistency(
            patch.get("default_agent", rec.default_agent),
            patch["allowed_agents"]
            if "allowed_agents" in patch
            else (json.loads(rec.allowed_agents) if rec.allowed_agents else None),
        )
    for field, value in _lists_to_json(patch).items():
        setattr(rec, field, value)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return ClientResponse.from_record(rec)


@router.delete("/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(
    client_id: str,
    session: Session = Depends(get_db_session),
) -> None:
    session.delete(_get_or_404(session, client_id))
    session.commit()


@router.post("/clients/{client_id}/rotate-key", response_model=ClientResponse)
def rotate_client_key(
    client_id: str,
    session: Session = Depends(get_db_session),
) -> ClientResponse:
    rec = _get_or_404(session, client_id)
    for _ in range(_ROTATE_KEY_ATTEMPTS):
        rec.client_key = secrets.token_urlsafe(32)
        session.add(rec)
        try:
            session.commit()
            break
        except IntegrityError:
            # Colisión de client_key: reintento con otra. Sin loguear ni encadenar la
            # excepción (su mensaje lleva la clave, G-27a).
            session.rollback()
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="could not generate a unique client_key"
        ) from None
    session.refresh(rec)
    return ClientResponse.from_record(rec)


# ---------------------------------------------------------------------------
# Sessions — observabilidad en memoria
# ---------------------------------------------------------------------------


def _serialize_events(events) -> list[dict]:
    return [
        {
            "stage": e.stage,
            "ts_ms": round(e.ts_ms),
            "elapsed_ms": round(e.elapsed_ms),
            "meta": e.meta,
        }
        for e in events
    ]


def _session_summary(record) -> dict:
    t = record.tracker
    return {
        "session_id": record.session_id,
        "client_id": record.client_id,
        "status": record.status,
        "input_mode": record.input_mode,
        "output_mode": record.output_mode,
        "started_at": record.started_at.isoformat(),
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        "turn_count": t.turn_count,
        "last_latencies": {
            "llm_first_token_ms": t.llm_first_token_ms(),
            "tts_first_chunk_ms": t.tts_first_chunk_ms(),
            "turn_e2e_ms": t.turn_e2e_ms(),
        },
    }


def _session_detail(record) -> dict:
    t = record.tracker
    events = t.events

    end_event = next((e for e in reversed(events) if e.stage == "session_end"), None)
    duration_s = end_event.meta.get("duration_s") if end_event else None

    by_turn: dict[int, dict] = defaultdict(dict)
    for e in events:
        by_turn[e.turn][e.stage] = e

    llm_latencies = [
        round(turn_events["llm_first_token"].ts_ms - turn_events["llm_start"].ts_ms, 1)
        for turn_events in by_turn.values()
        if "llm_start" in turn_events and "llm_first_token" in turn_events
    ]
    avg_llm_first_token_ms = (
        round(sum(llm_latencies) / len(llm_latencies), 1) if llm_latencies else None
    )

    e2e_latencies = list(t.turn_e2e_latencies().values())
    avg_turn_e2e_ms = round(sum(e2e_latencies) / len(e2e_latencies), 1) if e2e_latencies else None

    return {
        **_session_summary(record),
        "summary": {
            "turn_count": t.turn_count,
            "duration_s": duration_s,
            "avg_llm_first_token_ms": avg_llm_first_token_ms,
            "avg_turn_e2e_ms": avg_turn_e2e_ms,
        },
        "events": _serialize_events(events),
    }


@router.get("/sessions")
async def list_sessions(request: Request) -> dict:
    registry = request.app.state.session_registry
    sessions = registry.get_all()
    active = sum(1 for s in sessions if s.status == "active")
    return {
        "active": active,
        "total": len(sessions),
        "sessions": [_session_summary(s) for s in sessions],
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict:
    registry = request.app.state.session_registry
    record = registry.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_detail(record)


# ---------------------------------------------------------------------------
# Orchestrators — estado y control
# ---------------------------------------------------------------------------


@router.get("/orchestrators/{name}/status")
async def get_orchestrator_status(name: str, request: Request) -> dict:
    openclaw = request.app.state.openclaw
    if name != openclaw.get_name():
        raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
    s = openclaw.status()
    return {
        "name": s.name,
        "state": s.state.value,
        "connected_at": s.connected_at.isoformat() if s.connected_at else None,
        "reconnect_attempts": s.reconnect_attempts,
        "last_error": s.last_error,
    }


@router.post("/orchestrators/{name}/reconnect", status_code=202)
async def post_orchestrator_reconnect(name: str, request: Request) -> dict:
    openclaw = request.app.state.openclaw
    if name != openclaw.get_name():
        raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
    job_id = openclaw.trigger_reconnect()
    return {"accepted": True, "job_id": job_id}


# ---------------------------------------------------------------------------
# Transcriber / TTS — estado (solo lectura; ver docstring del módulo)
# ---------------------------------------------------------------------------


@router.get("/transcriber/status")
async def get_transcriber_status(request: Request) -> dict:
    """Transcriber has no process-level connection (one instance per audio
    session) — this reports live readiness via the STT provider's probe, wrapped
    in the same shape as the other two status endpoints for consistency."""
    stt = request.app.state.speech.stt
    await stt.probe()
    s = stt.status()
    body = {
        "name": s.name,
        "provider": stt.name,
        "state": s.state.value,
        "connected_at": s.connected_at.isoformat() if s.connected_at else None,
        "reconnect_attempts": s.reconnect_attempts,
        "last_error": s.last_error,
    }
    counters = getattr(stt, "counters", None)  # diagnóstico opcional (ElevenLabs)
    if callable(counters):
        body["counters"] = counters()
    return body


@router.get("/tts/status")
async def get_tts_status(request: Request) -> dict:
    tts = request.app.state.speech.tts
    s = tts.status()
    body = {
        "name": s.name,
        "provider": tts.name,
        "state": s.state.value,
        "connected_at": s.connected_at.isoformat() if s.connected_at else None,
        "reconnect_attempts": s.reconnect_attempts,
        "last_error": s.last_error,
    }
    counters = getattr(tts, "counters", None)  # diagnóstico opcional (ElevenLabs)
    if callable(counters):
        body["counters"] = counters()
    return body


# ---------------------------------------------------------------------------
# Proveedores de voz — estado (se eligen al arrancar con STT_PROVIDER/TTS_PROVIDER)
# ---------------------------------------------------------------------------


@router.get("/speech")
async def get_speech(request: Request) -> dict:
    speech = request.app.state.speech
    return {
        "stt": {"provider": speech.stt.name, "state": speech.stt.state.value},
        "tts": {"provider": speech.tts.name, "state": speech.tts.state.value},
        # La puerta solo existe en el STT de ElevenLabs: con jota es siempre "off".
        "audio_gate": getattr(speech.stt, "audio_gate", "off"),
    }


# ---------------------------------------------------------------------------
# Estado vivo (#219) — barato cada segundo: estado en memoria, sin probe()
# ---------------------------------------------------------------------------


def _service_state(s: ServiceStatus) -> dict[str, Any]:
    return {"state": s.state.value, "last_error": s.last_error}


def _build_state(app: FastAPI) -> dict[str, Any]:
    speech = app.state.speech
    sessions = []
    for bridge in app.state.client_registry.bridges():
        try:
            row = bridge.snapshot()
            record = app.state.session_registry.get(row["session_id"])
            if record is not None:
                summary = _session_summary(record)
                row["started_at"] = summary["started_at"]
                row["last_latencies"] = summary["last_latencies"]
        except Exception as e:  # una sesión rota no tumba la instantánea entera
            logger.debug("snapshot de sesión omitido: %s", type(e).__name__)
            continue
        sessions.append(row)
    return {
        "gateway": {
            "started_at": gateway_stats.started_at.isoformat(),
            "uptime_s": round(time.monotonic() - gateway_stats.started_monotonic, 3),
        },
        "services": {
            "orchestrator": _service_state(app.state.openclaw.status()),
            "stt": {"provider": speech.stt.name, **_service_state(speech.stt.status())},
            "tts": {"provider": speech.tts.name, **_service_state(speech.tts.status())},
        },
        "totals": {**gateway_stats.snapshot(), "sessions_active": len(sessions)},
        "sessions": sessions,
    }


@router.get("/state")
async def get_state(request: Request) -> dict:
    return _build_state(request.app)


@router.get("/state/stream")
async def get_state_stream(request: Request) -> StreamingResponse:
    async def state_stream():
        deadline = time.monotonic() + STATE_STREAM_MAX_S
        while time.monotonic() < deadline and not await request.is_disconnected():
            yield f"data: {json.dumps(_build_state(request.app))}\n\n"
            await asyncio.sleep(STATE_STREAM_INTERVAL_S)

    return StreamingResponse(state_stream(), media_type="text/event-stream")
