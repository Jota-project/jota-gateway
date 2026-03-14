"""Tests for send guards in _call_orchestrator closures."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.models.schemas import Handshake


@pytest.fixture
def bridge():
    ws = AsyncMock()
    b = JotaBridge(client_id="test", client_ws=ws)
    b.handshake = Handshake(input_mode="text", output_mode=["text", "status"])
    b.orchestrator = AsyncMock()
    b.transcriber = None
    return b


def make_listen_loop_with_token(token: str):
    """Return an orchestrator.listen_loop side_effect that yields one token."""
    async def _listen(text, on_token, on_event, **kwargs):
        await on_token(token)
    return _listen


def make_listen_loop_with_event(event: dict):
    """Return an orchestrator.listen_loop side_effect that yields one event."""
    async def _listen(text, on_token, on_event, **kwargs):
        await on_event(event)
    return _listen


async def test_token_send_failure_does_not_propagate(bridge):
    """send_json raising inside _on_token must not crash _call_orchestrator."""
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))
    bridge.orchestrator.listen_loop = make_listen_loop_with_token("hello")

    # Must not raise
    await bridge._call_orchestrator("test")


async def test_event_send_failure_does_not_propagate(bridge):
    """send_json raising inside _on_event must not crash _call_orchestrator."""
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))
    bridge.orchestrator.listen_loop = make_listen_loop_with_event(
        {"type": "status", "content": "thinking"}
    )

    # Must not raise
    await bridge._call_orchestrator("test")


async def test_audio_send_failure_does_not_propagate():
    """send_bytes raising inside pipe_audio must not crash _call_orchestrator."""
    ws = AsyncMock()
    ws.send_bytes = AsyncMock(side_effect=RuntimeError("disconnected"))
    b = JotaBridge(client_id="test", client_ws=ws)
    b.handshake = Handshake(input_mode="audio", output_mode=["audio", "text"])
    b.orchestrator = AsyncMock()

    # Orchestrator produces one token, TTS returns one audio chunk
    async def listen_with_token(text, on_token, on_event, **kwargs):
        await on_token("hi")

    b.orchestrator.listen_loop = listen_with_token

    import src.services.bridge as bridge_module

    class FakeTTS:
        def __init__(self, **kwargs): pass
        async def connect(self): pass
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
