from fastapi import APIRouter, Depends, HTTPException, Request
from src.api.deps import get_verified_client

router = APIRouter(prefix="/sessions", tags=["sessions"])


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

    starts = [e for e in events if e.stage == "llm_start"]
    first_tokens = [e for e in events if e.stage == "llm_first_token"]
    avg_llm_first_token_ms = None
    pairs = list(zip(starts, first_tokens))
    if pairs:
        latencies = [round(ft.ts_ms - s.ts_ms, 1) for s, ft in pairs if ft.ts_ms > s.ts_ms]
        if latencies:
            avg_llm_first_token_ms = round(sum(latencies) / len(latencies), 1)

    t_finals = [e for e in events if e.stage == "transcription_final"]
    tts_dones = [e for e in events if e.stage == "tts_done"]
    avg_turn_e2e_ms = None
    e2e_pairs = list(zip(t_finals, tts_dones))
    if e2e_pairs:
        e2e_latencies = [round(d.ts_ms - f.ts_ms, 1) for f, d in e2e_pairs if d.ts_ms > f.ts_ms]
        if e2e_latencies:
            avg_turn_e2e_ms = round(sum(e2e_latencies) / len(e2e_latencies), 1)

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


@router.get("")
async def list_sessions(request: Request, _=Depends(get_verified_client)):
    registry = request.app.state.session_registry
    sessions = registry.get_all()
    active = sum(1 for s in sessions if s.status == "active")
    return {
        "active": active,
        "total": len(sessions),
        "sessions": [_session_summary(s) for s in sessions],
    }


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    _=Depends(get_verified_client),
):
    registry = request.app.state.session_registry
    record = registry.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_detail(record)
