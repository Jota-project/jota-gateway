"""
admin_routes.py
~~~~~~~~~~~~~~~
/admin/* — gestión y observabilidad del gateway.

Auth: X-Admin-Token header validado contra ADMIN_TOKEN env var (via get_admin_auth).

Clientes:
  GET    /admin/clients              — lista clientes (stub → 501 hasta DB session)
  POST   /admin/clients              — crear cliente (stub → 501)
  GET    /admin/clients/{id}         — detalle (stub → 501)
  PATCH  /admin/clients/{id}         — actualizar (stub → 501)
  DELETE /admin/clients/{id}         — borrar (stub → 501)
  POST   /admin/clients/{id}/rotate-key — rotar key (stub → 501)

Observabilidad:
  GET    /admin/sessions             — sesiones en memoria
  GET    /admin/sessions/{id}        — detalle de sesión
  GET    /admin/orchestrators/{name}/status
  POST   /admin/orchestrators/{name}/reconnect  (202)
"""
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.deps import get_admin_auth

router = APIRouter(prefix="/admin", dependencies=[Depends(get_admin_auth)])


# ---------------------------------------------------------------------------
# Client CRUD — stubs until DB session is implemented
# ---------------------------------------------------------------------------

@router.get("/clients")
async def list_clients() -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.post("/clients", status_code=201)
async def create_client() -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.get("/clients/{client_id}")
async def get_client(client_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.patch("/clients/{client_id}")
async def update_client(client_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.delete("/clients/{client_id}", status_code=204)
async def delete_client(client_id: str) -> None:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


@router.post("/clients/{client_id}/rotate-key")
async def rotate_client_key(client_id: str) -> dict:
    raise HTTPException(status_code=501, detail="Not implemented — pending DB session")


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
    avg_llm_first_token_ms = round(sum(llm_latencies) / len(llm_latencies), 1) if llm_latencies else None

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
        "disconnected_at": None,
        "reconnect_attempts": s.reconnect_attempts,
        "last_error": s.last_error,
    }


@router.post("/orchestrators/{name}/reconnect", status_code=202)
async def post_orchestrator_reconnect(name: str, request: Request) -> dict:
    openclaw = request.app.state.openclaw
    if name != openclaw.get_name():
        raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
    await openclaw.connect()
    return {"accepted": True}
