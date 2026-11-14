"""PipelineTracker.close() must be idempotent (issue #127)."""

from unittest.mock import AsyncMock, MagicMock

from fogar_gateway.services.pipeline_tracker import PipelineTracker
from tests.fakes.bridge import DirectSender


def _tracker(registry):
    return PipelineTracker(
        session_id="s1",
        client_id="c1",
        input_mode="text",
        output_mode=["text"],
        sender=DirectSender(AsyncMock()),
        registry=registry,
    )


async def test_close_twice_records_session_end_and_closes_registry_once():
    registry = MagicMock()
    tracker = _tracker(registry)

    await tracker.close("completed")
    await tracker.close("completed")

    assert [e.stage for e in tracker.events].count("session_end") == 1
    registry.close.assert_called_once_with("s1", "completed")


async def test_second_close_does_not_overwrite_first_status():
    registry = MagicMock()
    tracker = _tracker(registry)

    await tracker.close("error")
    await tracker.close("completed")

    registry.close.assert_called_once_with("s1", "error")


async def test_tracker_without_sender_records_and_sends_nothing():
    """Sin sender (sesiones HTTP de /v1) registra el evento aunque pida `status`."""
    tracker = PipelineTracker(
        session_id="s1", client_id="c1", input_mode="text", output_mode=["status"],
        registry=MagicMock(),
    )  # fmt: skip
    event = await tracker.record("llm_start")
    assert tracker.events == [event]


async def test_push_tts_metrics_do_not_skew_the_user_turn_latencies():
    """#271: the push records tts_start/tts_first_chunk/tts_done too (origin="push",
    under the counter of the last user turn): each first chunk pairs with its own
    tts_start, and a push's tts_done answers no utterance of the user."""
    t = _tracker(MagicMock())
    await t.record("transcription_final", text_len=4)
    t.start_turn()
    await t.record("tts_start", voice="")
    await t.record("tts_first_chunk")
    await t.record("tts_done")
    user_e2e = t.turn_e2e_ms()
    user_first_chunk = t.tts_first_chunk_ms()
    assert user_e2e is not None and user_first_chunk is not None

    await t.record("tts_start", voice="", origin="push")
    await t.record("tts_done", origin="push")  # a push with no audio chunk at all
    assert t.tts_first_chunk_ms() == user_first_chunk  # not paired with the push's start
    assert t.turn_e2e_ms() == user_e2e
    assert list(t.turn_e2e_latencies().values()) == [user_e2e]

    await t.record("tts_first_chunk", origin="push")
    assert t.tts_first_chunk_ms() is not None
    assert t.turn_e2e_ms() == user_e2e
