import pytest
from unittest.mock import MagicMock
from src.services.orchestration import call_orchestrator
from src.services.protocol import OrchestratorEvent
from src.services.openclaw.models import ToolCallEvent


def _make_orchestrator(events):
    async def _stream(**kwargs):
        for e in events:
            yield e
    mock = MagicMock()
    mock.stream_response = _stream
    return mock


async def test_tokens_reach_callback():
    orch = _make_orchestrator([
        OrchestratorEvent(type="token", content="Hello"),
        OrchestratorEvent(type="token", content=" world"),
        OrchestratorEvent(type="status", content="done"),
    ])
    received = []
    async def on_token(t):
        received.append(t)

    await call_orchestrator(orch, "hi", "agent:main:ha", "ha", on_token=on_token)
    assert received == ["Hello", " world"]


async def test_tracker_receives_llm_events():
    from src.services.pipeline_tracker import PipelineTracker, _NullWS

    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="t1", client_id="ha",
        input_mode="text", output_mode=[],
        client_ws=_NullWS(), registry=registry,
    )
    orch = _make_orchestrator([
        OrchestratorEvent(type="token", content="hi"),
        OrchestratorEvent(type="status", content="done"),
    ])

    await call_orchestrator(orch, "hi", "agent:main:ha", "ha", tracker=tracker)

    stages = [e.stage for e in tracker.events]
    assert "llm_start" in stages
    assert "llm_first_token" in stages
    assert "llm_done" in stages


async def test_no_tracker_does_not_raise():
    orch = _make_orchestrator([OrchestratorEvent(type="status", content="done")])
    await call_orchestrator(orch, "hi", "agent:main:ha", "ha", tracker=None, on_token=None)


async def test_no_on_token_does_not_raise():
    orch = _make_orchestrator([
        OrchestratorEvent(type="token", content="t"),
        OrchestratorEvent(type="status", content="done"),
    ])
    await call_orchestrator(orch, "hi", "agent:main:ha", "ha", on_token=None)


async def test_error_event_raises():
    orch = _make_orchestrator([OrchestratorEvent(type="error", content="orchestrator_unavailable")])
    with pytest.raises(RuntimeError, match="orchestrator_unavailable"):
        await call_orchestrator(orch, "hi", "agent:main:ha", "ha")


async def test_tool_call_reaches_callback():
    tc_start = ToolCallEvent(phase="start", name="exec", tool_call_id="call-1", args={"command": "ls"})
    tc_result = ToolCallEvent(phase="result", name="exec", tool_call_id="call-1", result="ok", is_error=False)
    orch = _make_orchestrator([
        OrchestratorEvent(type="tool_call", tool_call=tc_start),
        OrchestratorEvent(type="tool_call", tool_call=tc_result),
        OrchestratorEvent(type="status", content="done"),
    ])
    received = []

    async def on_tool_call(tc):
        received.append(tc)

    await call_orchestrator(orch, "hi", "agent:main:ha", "ha", on_tool_call=on_tool_call)
    assert received == [tc_start, tc_result]


async def test_no_on_tool_call_does_not_raise():
    tc = ToolCallEvent(phase="start", name="exec", tool_call_id="call-1")
    orch = _make_orchestrator([
        OrchestratorEvent(type="tool_call", tool_call=tc),
        OrchestratorEvent(type="status", content="done"),
    ])
    await call_orchestrator(orch, "hi", "agent:main:ha", "ha", on_tool_call=None)


async def test_llm_done_meta_has_token_count():
    from src.services.pipeline_tracker import PipelineTracker, _NullWS
    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="t2", client_id="ha",
        input_mode="text", output_mode=[],
        client_ws=_NullWS(), registry=registry,
    )
    orch = _make_orchestrator([
        OrchestratorEvent(type="token", content="a"),
        OrchestratorEvent(type="token", content="b"),
        OrchestratorEvent(type="status", content="done"),
    ])
    await call_orchestrator(orch, "hi", "agent:main:ha", "ha", tracker=tracker)
    done_event = next(e for e in tracker.events if e.stage == "llm_done")
    assert done_event.meta["token_count"] == 2
