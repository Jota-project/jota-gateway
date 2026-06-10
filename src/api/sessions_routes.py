from collections import defaultdict

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
