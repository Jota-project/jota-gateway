"""Tests for send guards in _call_orchestrator closures."""

from unittest.mock import AsyncMock

import pytest

from fogar_gateway.models.schemas import ClientConfig
from fogar_gateway.services.openclaw.models import ToolCallEvent
from fogar_gateway.services.outbound import ClientGone
from fogar_gateway.services.protocol import OrchestratorEvent
from tests.fakes.bridge import GoneSender, sent


@pytest.fixture
async def bridge(make_bridge):
    b, _ = make_bridge(output_mode=("text", "status"))
    return b


def make_stream_with_token(token: str):
    """Return an async generator that yields one token event."""

    async def _stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content=token)

    return _stream


def make_stream_with_event(event_type: str, content: str):
    """Return an async generator that yields one non-token event."""

    async def _stream(*args, **kwargs):
        yield OrchestratorEvent(type=event_type, content=content)

    return _stream


def make_stream_with_tool_call(tool_call: ToolCallEvent):
    async def _stream(*args, **kwargs):
        yield OrchestratorEvent(type="tool_call", tool_call=tool_call)

    return _stream


async def test_tool_call_forwarded_when_enabled(make_bridge):
    b, ws = make_bridge(config=ClientConfig(tool_calls_enabled=True))
    tc = ToolCallEvent(phase="start", name="exec", tool_call_id="call-1", args={"command": "ls"})
    b.orchestrator.stream_response = make_stream_with_tool_call(tc)

    await b._call_orchestrator("test")
    await sent(b)

    tool_call_msgs = ws.of("tool_call")
    assert len(tool_call_msgs) == 1
    assert tool_call_msgs[0]["phase"] == "start"
    assert tool_call_msgs[0]["name"] == "exec"
    assert tool_call_msgs[0]["tool_call_id"] == "call-1"
    assert tool_call_msgs[0]["args"] == {"command": "ls"}
    assert tool_call_msgs[0]["turn_id"] == "t-1"


# A client that left (ClientGone) and any other send failure take different branches.
_SEND_ERRORS = pytest.mark.parametrize("error", [ClientGone, RuntimeError])


async def test_tool_call_not_forwarded_when_disabled(make_bridge):
    b, ws = make_bridge(config=ClientConfig(tool_calls_enabled=False))
    tc = ToolCallEvent(phase="start", name="exec", tool_call_id="call-1", args={"command": "ls"})
    b.orchestrator.stream_response = make_stream_with_tool_call(tc)

    await b._call_orchestrator("test")
    await sent(b)

    tool_call_msgs = ws.of("tool_call")
    assert tool_call_msgs == []


@_SEND_ERRORS
async def test_token_send_failure_does_not_propagate(make_bridge, error):
    """send_json raising inside _on_token must not crash _call_orchestrator."""
    bridge, _ = make_bridge(output_mode=("text", "status"), sender=GoneSender(error))
    bridge.orchestrator.stream_response = make_stream_with_token("hello")

    # Must not raise
    await bridge._call_orchestrator("test")


@_SEND_ERRORS
async def test_event_send_failure_does_not_propagate(make_bridge, error):
    """send_json raising inside _on_event must not crash _call_orchestrator."""
    bridge, _ = make_bridge(output_mode=("text", "status"), sender=GoneSender(error))
    bridge.orchestrator.stream_response = make_stream_with_event("status", "thinking")

    # Must not raise
    await bridge._call_orchestrator("test")


@_SEND_ERRORS
async def test_audio_send_failure_does_not_propagate(make_bridge, error):
    """send_bytes raising inside pipe_audio must not crash _call_orchestrator."""

    class FakeTTS:
        async def send_text_chunk(self, t):
            pass

        async def end(self):
            pass

        async def close(self):
            pass

        async def get_audio_stream(self):
            yield b"\xff\xfe"

    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=FakeTTS())

    b, _ = make_bridge(
        input_mode="audio", output_mode=("audio", "text"), tts=tts_wrapper, sender=GoneSender()
    )

    async def stream_with_token(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="hi")

    b.orchestrator.stream_response = stream_with_token

    await b._call_orchestrator("test")  # must not raise


async def test_turn_in_progress_error_event_sends_non_fatal_turn_error_frame(bridge):
    """Issue #99: when the orchestrator rejects a duplicate session_key turn
    (an 'error' event with TURN_IN_PROGRESS_ERROR content — see
    OpenClawClient.stream_response), the existing generic RuntimeError
    handling in pipe_tokens() must already surface it to the client as a
    non-fatal TURN_ERROR frame, without closing the WS. No WS-specific
    code should be needed for this issue — this test verifies that."""
    from fogar_gateway.services.openclaw.registry import TURN_IN_PROGRESS_ERROR

    bridge.orchestrator.stream_response = make_stream_with_event("error", TURN_IN_PROGRESS_ERROR)

    await bridge._call_orchestrator("test")  # must not raise — session stays open
    await sent(bridge)

    error_msgs = bridge.client_ws.of("error")
    assert len(error_msgs) == 1
    assert error_msgs[0]["code"] == "TURN_ERROR"
    assert error_msgs[0]["fatal"] is False
    assert error_msgs[0]["message"] == TURN_IN_PROGRESS_ERROR
    assert error_msgs[0]["turn_id"] == "t-1"


async def test_tts_end_called_when_orchestrator_raises_non_runtime_error(make_bridge):
    """tts.end() debe llamarse aunque call_orchestrator lance algo distinto de RuntimeError (e.g. OSError)."""
    tts_end_called = False

    class FakeTTS:
        async def send_text_chunk(self, t):
            pass

        async def end(self):
            nonlocal tts_end_called
            tts_end_called = True

        async def close(self):
            pass

        async def get_audio_stream(self):
            if False:
                yield b""  # async generator vacío — pipe_audio termina inmediatamente

    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=FakeTTS())

    b, _ = make_bridge(input_mode="audio", output_mode=("audio", "text"), tts=tts_wrapper)

    async def _crashing_stream(*args, **kwargs):
        raise OSError("connection reset by peer")
        yield

    b.orchestrator.stream_response = _crashing_stream

    try:
        await b._call_orchestrator("test")
    except Exception:
        pass  # _call_orchestrator puede propagar el OSError — nos interesa el efecto secundario

    assert tts_end_called, (
        "tts.end() debe llamarse siempre para señalizar el fin al servidor TTS, "
        "incluso cuando el orquestador lanza una excepción no-RuntimeError"
    )
