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
"""

import json
import secrets
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from src.api.deps import get_admin_auth
from src.core.config import effective_audio_gate, settings
from src.core.startup_checks import SpeechConfigError
from src.db.database import get_db_session
from src.db.models import ClientRecord
from src.models.admin_schemas import ClientCreate, ClientResponse, ClientUpdate
from src.services.db_client import db_client
from src.services.speech.registry import SpeechSwapError

_ROTATE_KEY_ATTEMPTS = 3

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
    record = ClientRecord(
        name=body.name,
        client_key=body.client_key or secrets.token_urlsafe(32),
        client_type=body.client_type,
        default_agent=body.default_agent,
        allowed_agents=json.dumps(body.allowed_agents) if body.allowed_agents is not None else None,
        stt_language=body.stt_language,
        stt_vad_thold=body.stt_vad_thold,
        tts_voice=body.tts_voice,
        tts_speed=body.tts_speed,
        barge_in_enabled=body.barge_in_enabled,
        barge_in_min_chars=body.barge_in_min_chars,
        output_mode=json.dumps(body.output_mode) if body.output_mode is not None else None,
        silence_timeout_s=body.silence_timeout_s,
        max_silence_turns=body.max_silence_turns,
        push_enabled=body.push_enabled,
        tool_calls_enabled=body.tool_calls_enabled,
    )
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
    old_key = rec.client_key
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
    # Serializar listas a JSON antes de escribir en la BD
    for list_field in ("allowed_agents", "output_mode"):
        if list_field in patch:
            patch[list_field] = (
                json.dumps(patch[list_field]) if patch[list_field] is not None else None
            )
    for field, value in patch.items():
        setattr(rec, field, value)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    db_client.invalidate(old_key)
    return ClientResponse.from_record(rec)


@router.delete("/clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(
    client_id: str,
    session: Session = Depends(get_db_session),
) -> None:
    rec = _get_or_404(session, client_id)
    client_key = rec.client_key
    session.delete(rec)
    session.commit()
    db_client.invalidate(client_key)


@router.post("/clients/{client_id}/rotate-key", response_model=ClientResponse)
def rotate_client_key(
    client_id: str,
    session: Session = Depends(get_db_session),
) -> ClientResponse:
    rec = _get_or_404(session, client_id)
    old_key = rec.client_key
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
    db_client.invalidate(old_key)
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

    e2e_latencies = [
        round(turn_events["tts_done"].ts_ms - turn_events["transcription_final"].ts_ms, 1)
        for turn_events in by_turn.values()
        if "transcription_final" in turn_events and "tts_done" in turn_events
    ]
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
    return {
        "name": s.name,
        "provider": stt.name,
        "state": s.state.value,
        "connected_at": s.connected_at.isoformat() if s.connected_at else None,
        "reconnect_attempts": s.reconnect_attempts,
        "last_error": s.last_error,
    }


@router.get("/tts/status")
async def get_tts_status(request: Request) -> dict:
    tts = request.app.state.speech.tts
    s = tts.status()
    return {
        "name": s.name,
        "provider": tts.name,
        "state": s.state.value,
        "connected_at": s.connected_at.isoformat() if s.connected_at else None,
        "reconnect_attempts": s.reconnect_attempts,
        "last_error": s.last_error,
    }


# ---------------------------------------------------------------------------
# Proveedores de voz — estado y cambio en caliente (no se persiste)
# ---------------------------------------------------------------------------


class SpeechSwap(BaseModel):
    stt_provider: str | None = None
    tts_provider: str | None = None


@router.get("/speech")
async def get_speech(request: Request) -> dict:
    speech = request.app.state.speech
    return {
        "stt": {"provider": speech.stt.name, "state": speech.stt.state.value},
        "tts": {"provider": speech.tts.name, "state": speech.tts.state.value},
        "audio_gate": effective_audio_gate(settings, speech.stt.name),
    }


@router.put("/speech")
async def put_speech(body: SpeechSwap, request: Request) -> dict:
    """Cambia el proveedor de STT y/o TTS sin reiniciar. No se persiste: al reiniciar el
    gateway vuelven a regir STT_PROVIDER/TTS_PROVIDER. Un TTS cambia en el turno
    siguiente; una sesión STT ya abierta conserva su proveedor hasta cerrarse."""
    try:
        return await request.app.state.speech.swap(
            stt_name=body.stt_provider, tts_name=body.tts_provider
        )
    except SpeechConfigError as e:
        # El mensaje nombra el setting, nunca su valor.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except SpeechSwapError as e:
        # Nombra solo el proveedor, nunca el texto de la excepción original.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
