"""Tests for send guards in _call_orchestrator closures."""
import pytest
from unittest.mock import AsyncMock
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.protocol import OrchestratorEvent
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.openclaw.models import ToolCallEvent
from src.services.reconnection import ConnectionState, ServiceStatus

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


def _connected_status() -> ServiceStatus:
    """ReconnectingTTSClient.status() is synchronous, unlike AsyncMock's
    auto-mocked attributes — tests that stub tts.connect() to succeed must
    also stub tts.status() as a plain callable, or _maybe_notify_tts_state()
    receives an unawaited coroutine instead of a ServiceStatus."""
    return ServiceStatus(
        name="tts", state=ConnectionState.CONNECTED,
        connected_at=None, reconnect_attempts=0, last_error=None,
    )


@pytest.fixture
def bridge(mock_tracker):
    ws = AsyncMock()
    b = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(), tts=AsyncMock(), tracker=mock_tracker,
                   handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text", "status"]),
                   client_registry=ClientRegistry(), default_agent="main")
    b.transcriber = None
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


async def test_tool_call_forwarded_when_enabled(mock_tracker):
    ws = AsyncMock()
    config = ClientConfig(tool_calls_enabled=True)
    b = JotaBridge(client=_CLIENT, config=config, client_ws=ws, orchestrator=AsyncMock(), tts=AsyncMock(), tracker=mock_tracker,
                   handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text"]),
                   client_registry=ClientRegistry(), default_agent="main")
    tc = ToolCallEvent(phase="start", name="exec", tool_call_id="call-1", args={"command": "ls"})
    b.orchestrator.stream_response = make_stream_with_tool_call(tc)

    await b._call_orchestrator("test")

    tool_call_msgs = [
        c.args[0] for c in ws.send_json.await_args_list
        if c.args[0].get("type") == "tool_call"
    ]
    assert len(tool_call_msgs) == 1
    assert tool_call_msgs[0]["phase"] == "start"
    assert tool_call_msgs[0]["name"] == "exec"
    assert tool_call_msgs[0]["tool_call_id"] == "call-1"
    assert tool_call_msgs[0]["args"] == {"command": "ls"}
    assert tool_call_msgs[0]["turn_id"] == "t-1"


async def test_tool_call_not_forwarded_when_disabled(mock_tracker):
    ws = AsyncMock()
    config = ClientConfig(tool_calls_enabled=False)
    b = JotaBridge(client=_CLIENT, config=config, client_ws=ws, orchestrator=AsyncMock(), tts=AsyncMock(), tracker=mock_tracker,
                   handshake=Handshake(client_key="test-key", input_mode="text", output_mode=["text"]),
                   client_registry=ClientRegistry(), default_agent="main")
    tc = ToolCallEvent(phase="start", name="exec", tool_call_id="call-1", args={"command": "ls"})
    b.orchestrator.stream_response = make_stream_with_tool_call(tc)

    await b._call_orchestrator("test")

    tool_call_msgs = [
        c.args[0] for c in ws.send_json.await_args_list
        if c.args[0].get("type") == "tool_call"
    ]
    assert tool_call_msgs == []


async def test_token_send_failure_does_not_propagate(bridge):
    """send_json raising inside _on_token must not crash _call_orchestrator."""
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))
    bridge.orchestrator.stream_response = make_stream_with_token("hello")

    # Must not raise
    await bridge._call_orchestrator("test")


async def test_event_send_failure_does_not_propagate(bridge):
    """send_json raising inside _on_event must not crash _call_orchestrator."""
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))
    bridge.orchestrator.stream_response = make_stream_with_event("status", "thinking")

    # Must not raise
    await bridge._call_orchestrator("test")


async def test_audio_send_failure_does_not_propagate(mock_tracker):
    """send_bytes raising inside pipe_audio must not crash _call_orchestrator."""
    ws = AsyncMock()
    ws.send_bytes = AsyncMock(side_effect=RuntimeError("disconnected"))

    class FakeTTS:
        async def send_text_chunk(self, t): pass
        async def end(self): pass
        async def close(self): pass
        async def get_audio_stream(self):
            yield b"\xff\xfe"

    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=FakeTTS())
    tts_wrapper.status = lambda: _connected_status()

    b = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(),
                   tts=tts_wrapper, tracker=mock_tracker,
                   handshake=Handshake(client_key="test-key", input_mode="audio", output_mode=["audio", "text"]),
                   client_registry=ClientRegistry(), default_agent="main")

    async def stream_with_token(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="hi")

    b.orchestrator.stream_response = stream_with_token

    await b._call_orchestrator("test")  # must not raise


async def test_tts_end_called_when_orchestrator_raises_non_runtime_error(mock_tracker):
    """tts.end() debe llamarse aunque call_orchestrator lance algo distinto de RuntimeError (e.g. OSError)."""
    ws = AsyncMock()
    tts_end_called = False

    class FakeTTS:
        async def send_text_chunk(self, t): pass
        async def end(self):
            nonlocal tts_end_called
            tts_end_called = True
        async def close(self): pass
        async def get_audio_stream(self):
            if False:
                yield b""  # async generator vacío — pipe_audio termina inmediatamente

    tts_wrapper = AsyncMock()
    tts_wrapper.connect = AsyncMock(return_value=FakeTTS())
    tts_wrapper.status = lambda: _connected_status()

    b = JotaBridge(
        client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(),
        tts=tts_wrapper, tracker=mock_tracker,
        handshake=Handshake(client_key="test-key", input_mode="audio", output_mode=["audio", "text"]),
        client_registry=ClientRegistry(), default_agent="main",
    )

    async def _crashing_stream(*args, **kwargs):
        raise OSError("connection reset by peer")
        yield  # noqa: F501 — hace que Python lo trate como async generator

    b.orchestrator.stream_response = _crashing_stream

    try:
        await b._call_orchestrator("test")
    except Exception:
        pass  # _call_orchestrator puede propagar el OSError — nos interesa el efecto secundario

    assert tts_end_called, (
        "tts.end() debe llamarse siempre para señalizar el fin al servidor TTS, "
        "incluso cuando el orquestador lanza una excepción no-RuntimeError"
    )
