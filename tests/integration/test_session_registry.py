from unittest.mock import AsyncMock, MagicMock

from src.services.pipeline_tracker import PipelineTracker
from src.services.session_registry import SessionRegistry


def _make_tracker(session_id="s1", client_id="c1"):
    ws = AsyncMock()
    registry = MagicMock()
    return PipelineTracker(
        session_id=session_id,
        client_id=client_id,
        input_mode="audio",
        output_mode=["audio"],
        client_ws=ws,
        registry=registry,
    )


def test_register_creates_active_record():
    reg = SessionRegistry()
    tracker = _make_tracker()
    record = reg.register(tracker)
    assert record.session_id == "s1"
    assert record.client_id == "c1"
    assert record.status == "active"
    assert record.ended_at is None


def test_register_stores_record_retrievable_by_id():
    reg = SessionRegistry()
    reg.register(_make_tracker())
    assert reg.get("s1") is not None


def test_register_links_tracker():
    reg = SessionRegistry()
    tracker = _make_tracker()
    record = reg.register(tracker)
    assert record.tracker is tracker


def test_close_marks_completed_and_sets_ended_at():
    reg = SessionRegistry()
    reg.register(_make_tracker())
    reg.close("s1", "completed")
    record = reg.get("s1")
    assert record.status == "completed"
    assert record.ended_at is not None


def test_close_with_error_status():
    reg = SessionRegistry()
    reg.register(_make_tracker())
    reg.close("s1", "error")
    assert reg.get("s1").status == "error"


def test_close_unknown_session_is_noop():
    reg = SessionRegistry()
    reg.close("nonexistent")  # must not raise


def test_get_all_returns_newest_first():
    reg = SessionRegistry()
    reg.register(_make_tracker("s1"))
    reg.register(_make_tracker("s2"))
    all_sessions = reg.get_all()
    assert all_sessions[0].session_id == "s2"
    assert all_sessions[1].session_id == "s1"


def test_get_all_empty_registry():
    reg = SessionRegistry()
    assert reg.get_all() == []


def test_get_returns_none_for_unknown_id():
    reg = SessionRegistry()
    assert reg.get("nope") is None


def test_eviction_removes_oldest_non_active_when_cap_reached():
    reg = SessionRegistry(maxsize=2)
    reg.register(_make_tracker("s1"))
    reg.close("s1", "completed")
    reg.register(_make_tracker("s2"))
    reg.register(_make_tracker("s3"))  # triggers eviction of s1
    assert reg.get("s1") is None
    assert reg.get("s2") is not None
    assert reg.get("s3") is not None


def test_eviction_skips_active_sessions():
    reg = SessionRegistry(maxsize=2)
    reg.register(_make_tracker("s1"))  # active
    reg.register(_make_tracker("s2"))  # active
    reg.register(_make_tracker("s3"))  # nothing to evict — both active
    # all three survive
    assert reg.get("s1") is not None
    assert reg.get("s2") is not None
    assert reg.get("s3") is not None


def test_events_list_is_live_reference():
    """SessionRecord.events is the same list object as tracker.events."""
    reg = SessionRegistry()
    tracker = _make_tracker()
    record = reg.register(tracker)
    assert record.events is tracker.events
