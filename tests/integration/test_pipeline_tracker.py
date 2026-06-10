import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.services.pipeline_tracker import PipelineTracker, PipelineEvent


def _make_tracker(ws=None, output_mode=None):
    if ws is None:
        ws = AsyncMock()
    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="test:123",
        client_id="test",
        input_mode="audio",
        output_mode=output_mode if output_mode is not None else ["audio", "text", "status"],
        client_ws=ws,
        registry=registry,
    )
    return tracker, ws, registry


async def test_record_appends_event():
    tracker, _, _ = _make_tracker()
    await tracker.record("session_start", input_mode="audio")
    assert len(tracker.events) == 1
    assert tracker.events[0].stage == "session_start"
    assert tracker.events[0].meta == {"input_mode": "audio"}


async def test_record_ts_ms_increases_over_time():
    tracker, _, _ = _make_tracker()
    await tracker.record("session_start")
    await asyncio.sleep(0.05)
    await tracker.record("llm_start")
    assert tracker.events[1].ts_ms > tracker.events[0].ts_ms
    assert tracker.events[1].ts_ms >= 40


async def test_record_elapsed_ms_measures_gap():
    tracker, _, _ = _make_tracker()
    await tracker.record("session_start")
    await asyncio.sleep(0.05)
    await tracker.record("llm_start")
    assert tracker.events[1].elapsed_ms >= 40


async def test_record_sends_pipeline_event_when_status_in_output_mode():
    ws = AsyncMock()
    tracker, _, _ = _make_tracker(ws=ws, output_mode=["text", "status"])
    await tracker.record("llm_start")
    ws.send_json.assert_called_once()
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "pipeline_event"
    assert payload["stage"] == "llm_start"
    assert "elapsed_ms" in payload
    assert "turn" in payload


async def test_record_does_not_send_when_status_not_in_output_mode():
    ws = AsyncMock()
    tracker, _, _ = _make_tracker(ws=ws, output_mode=["text"])
    await tracker.record("llm_start")
    ws.send_json.assert_not_called()


async def test_record_swallows_send_exception():
    ws = AsyncMock()
    ws.send_json.side_effect = Exception("disconnected")
    tracker, _, _ = _make_tracker(ws=ws)
    # Must not raise
    await tracker.record("llm_start")
    assert len(tracker.events) == 1


async def test_start_turn_increments_counter():
    tracker, _, _ = _make_tracker()
    assert tracker.turn_count == 0
    tracker.start_turn()
    assert tracker.turn_count == 1
    tracker.start_turn()
    assert tracker.turn_count == 2


async def test_turn_number_sent_in_client_event():
    ws = AsyncMock()
    tracker, _, _ = _make_tracker(ws=ws)
    tracker.start_turn()
    await tracker.record("llm_start")
    payload = ws.send_json.call_args[0][0]
    assert payload["turn"] == 1


async def test_llm_first_token_ms_computes_correctly():
    tracker, _, _ = _make_tracker()
    await tracker.record("llm_start")
    await asyncio.sleep(0.05)
    await tracker.record("llm_first_token")
    result = tracker.llm_first_token_ms()
    assert result is not None
    assert result >= 40


async def test_llm_first_token_ms_returns_none_without_events():
    tracker, _, _ = _make_tracker()
    assert tracker.llm_first_token_ms() is None


async def test_tts_first_chunk_ms_computes_correctly():
    tracker, _, _ = _make_tracker()
    await tracker.record("tts_start")
    await asyncio.sleep(0.05)
    await tracker.record("tts_first_chunk")
    assert tracker.tts_first_chunk_ms() >= 40


async def test_turn_e2e_ms_computes_correctly():
    tracker, _, _ = _make_tracker()
    await tracker.record("transcription_final")
    await asyncio.sleep(0.05)
    await tracker.record("tts_done")
    assert tracker.turn_e2e_ms() >= 40


async def test_turn_e2e_ms_returns_none_without_tts_done():
    tracker, _, _ = _make_tracker()
    await tracker.record("transcription_final")
    assert tracker.turn_e2e_ms() is None


async def test_close_records_session_end_and_notifies_registry():
    tracker, _, registry = _make_tracker()
    await tracker.close()
    assert tracker.events[-1].stage == "session_end"
    assert tracker.events[-1].meta["turn_count"] == 0
    assert "duration_s" in tracker.events[-1].meta
    registry.close.assert_called_once_with("test:123", "completed")


async def test_close_with_error_status():
    tracker, _, registry = _make_tracker()
    await tracker.close("error")
    registry.close.assert_called_once_with("test:123", "error")
