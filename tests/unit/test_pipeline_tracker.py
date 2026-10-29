"""PipelineTracker.close() must be idempotent (issue #127)."""

from unittest.mock import AsyncMock, MagicMock

from src.services.outbound import DirectSender
from src.services.pipeline_tracker import PipelineTracker


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
