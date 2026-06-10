"""Tests for send guards in _call_orchestrator closures."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.services.orchestrators.protocol import OrchestratorEvent
from src.models.schemas import Client, ClientConfig, Handshake

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


def _mock_tracker():
    t = MagicMock()
    t.start_turn = MagicMock(return_value=1)
    t.record = AsyncMock()
    t.close = AsyncMock()
    return t


@pytest.fixture
def bridge():
    ws = AsyncMock()
    b = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(), tracker=_mock_tracker())
    b.handshake = Handshake(client_key="test-key", input_mode="text", output_mode=["text", "status"])
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


async def test_audio_send_failure_does_not_propagate():
    """send_bytes raising inside pipe_audio must not crash _call_orchestrator."""
    ws = AsyncMock()
    ws.send_bytes = AsyncMock(side_effect=RuntimeError("disconnected"))
    b = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(), tracker=_mock_tracker())
    b.handshake = Handshake(client_key="test-key", input_mode="audio", output_mode=["audio", "text"])

    async def stream_with_token(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="hi")

    b.orchestrator.stream_response = stream_with_token

    import src.services.bridge as bridge_module

    class FakeTTS:
        def __init__(self, **kwargs): pass
        async def connect(self, **kwargs): pass
        async def send_text_chunk(self, t): pass
        async def end(self): pass
        async def close(self): pass
        async def get_audio_stream(self):
            yield b"\xff\xfe"

    original = bridge_module.TTSClient
    bridge_module.TTSClient = FakeTTS
    try:
        await b._call_orchestrator("test")  # must not raise
    finally:
        bridge_module.TTSClient = original
