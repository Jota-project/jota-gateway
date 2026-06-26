from unittest.mock import AsyncMock, MagicMock
from src.services.pipeline_tracker import PipelineTracker


def _make_live_tracker(app, session_id="sess:111", output_mode=None):
    """Create and register a tracker directly into the live app.state.session_registry."""
    ws = AsyncMock()
    registry_mock = MagicMock()
    tracker = PipelineTracker(
        session_id=session_id,
        client_id="client-abc",
        input_mode="audio",
        output_mode=output_mode or ["audio", "text"],
        client_ws=ws,
        registry=registry_mock,
    )
    app.state.session_registry.register(tracker)
    return tracker


def test_list_sessions_empty(client, auth_headers):
    r = client.get("/api/sessions", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["active"] == 0
    assert data["total"] == 0
    assert data["sessions"] == []


def test_list_sessions_requires_auth(client):
    r = client.get("/api/sessions")
    assert r.status_code == 422  # missing X-API-Key header


def test_list_sessions_shows_active_session(client, auth_headers):
    from src.main import app
    _make_live_tracker(app, "sess:active")
    try:
        r = client.get("/api/sessions", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["active"] == 1
        session = data["sessions"][0]
        assert session["session_id"] == "sess:active"
        assert session["status"] == "active"
        assert session["input_mode"] == "audio"
    finally:
        app.state.session_registry.close("sess:active", "completed")


def test_list_sessions_includes_completed(client, auth_headers):
    from src.main import app
    _make_live_tracker(app, "sess:done")
    app.state.session_registry.close("sess:done", "completed")
    r = client.get("/api/sessions", headers=auth_headers)
    data = r.json()
    ids = [s["session_id"] for s in data["sessions"]]
    assert "sess:done" in ids


def test_get_session_not_found(client, auth_headers):
    r = client.get("/api/sessions/nope:999", headers=auth_headers)
    assert r.status_code == 404


def test_get_session_returns_required_fields(client, auth_headers):
    """Response structure: session_id, events list, summary dict, last_latencies."""
    from src.main import app

    _make_live_tracker(app, "sess:fields")
    r = client.get("/api/sessions/sess:fields", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == "sess:fields"
    assert isinstance(data["events"], list)
    assert "summary" in data
    assert "turn_count" in data["summary"]
    assert "avg_llm_first_token_ms" in data["summary"]
    assert "avg_turn_e2e_ms" in data["summary"]
    app.state.session_registry.close("sess:fields", "completed")


def test_get_session_last_latencies_fields_present(client, auth_headers):
    """last_latencies always present with the three keys (values may be None without events)."""
    from src.main import app

    _make_live_tracker(app, "sess:lat")
    r = client.get("/api/sessions/sess:lat", headers=auth_headers)
    data = r.json()
    lat = data["last_latencies"]
    assert "llm_first_token_ms" in lat
    assert "tts_first_chunk_ms" in lat
    assert "turn_e2e_ms" in lat
    app.state.session_registry.close("sess:lat", "completed")


async def test_get_session_avg_latencies_computed_correctly(client, auth_headers):
    """Verify avg_llm_first_token_ms is computed correctly from pipeline events."""
    import asyncio
    from src.main import app

    ws = AsyncMock()
    registry_mock = MagicMock()
    tracker = PipelineTracker(
        session_id="sess:avgs",
        client_id="c1",
        input_mode="audio",
        output_mode=["text"],
        client_ws=ws,
        registry=registry_mock,
    )
    # Turn 1: llm_start → wait → llm_first_token
    tracker.start_turn()
    await tracker.record("transcription_final")
    await tracker.record("llm_start")
    await asyncio.sleep(0.05)
    await tracker.record("llm_first_token")
    await tracker.record("tts_done")

    app.state.session_registry.register(tracker)
    r = client.get("/api/sessions/sess:avgs", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["avg_llm_first_token_ms"] is not None
    assert data["summary"]["avg_llm_first_token_ms"] >= 40
    assert data["summary"]["avg_turn_e2e_ms"] is not None
    assert data["summary"]["avg_turn_e2e_ms"] >= 40
    app.state.session_registry.close("sess:avgs", "completed")


async def test_get_session_avg_latencies_exclude_cancelled_turns(client, auth_headers):
    """A cancelled turn (llm_start but no llm_first_token) must not corrupt averages."""
    import asyncio
    from src.main import app

    ws = AsyncMock()
    registry_mock = MagicMock()
    tracker = PipelineTracker(
        session_id="sess:cancel",
        client_id="c1",
        input_mode="audio",
        output_mode=["text"],
        client_ws=ws,
        registry=registry_mock,
    )
    # Turn 1: cancelled (no llm_first_token, no tts_done)
    tracker.start_turn()
    await tracker.record("transcription_final")
    await tracker.record("llm_start")
    # barge-in cancels — no first_token

    # Turn 2: completes normally
    tracker.start_turn()
    await tracker.record("transcription_final")
    await tracker.record("llm_start")
    await asyncio.sleep(0.05)
    await tracker.record("llm_first_token")
    await tracker.record("tts_done")

    app.state.session_registry.register(tracker)
    r = client.get("/api/sessions/sess:cancel", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    # Only turn 2 contributed — latency should be >= 40ms (not some weird negative or None)
    assert data["summary"]["avg_llm_first_token_ms"] is not None
    assert data["summary"]["avg_llm_first_token_ms"] >= 40
    app.state.session_registry.close("sess:cancel", "completed")
