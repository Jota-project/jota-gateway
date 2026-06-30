"""Tests for send guards in _call_orchestrator closures."""
import pytest
from unittest.mock import AsyncMock
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.protocol import OrchestratorEvent
from src.models.schemas import Client, ClientConfig, Handshake

import src.services.bridge as bridge_module

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


@pytest.fixture
def bridge(mock_tracker):
    ws = AsyncMock()
    b = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(), tracker=mock_tracker,
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
    b = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(), tracker=mock_tracker,
                   handshake=Handshake(client_key="test-key", input_mode="audio", output_mode=["audio", "text"]),
                   client_registry=ClientRegistry(), default_agent="main")

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


async def test_tts_end_called_when_orchestrator_raises_non_runtime_error(mock_tracker):
    """tts.end() debe llamarse aunque call_orchestrator lance algo distinto de RuntimeError (e.g. OSError)."""
    ws = AsyncMock()
    b = JotaBridge(
        client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="test-key", input_mode="audio", output_mode=["audio", "text"]),
        client_registry=ClientRegistry(), default_agent="main",
    )

    tts_end_called = False

    class FakeTTS:
        def __init__(self, **kwargs): pass
        async def connect(self, **kwargs): pass
        async def send_text_chunk(self, t): pass
        async def end(self):
            nonlocal tts_end_called
            tts_end_called = True
        async def close(self): pass
        async def get_audio_stream(self):
            if False:
                yield b""  # async generator vacío — pipe_audio termina inmediatamente

    # El orquestador lanza OSError (no RuntimeError) — simula un fallo de red crudo
    async def _crashing_stream(*args, **kwargs):
        raise OSError("connection reset by peer")
        yield  # noqa: F501 — hace que Python lo trate como async generator

    b.orchestrator.stream_response = _crashing_stream

    original = bridge_module.TTSClient
    bridge_module.TTSClient = FakeTTS
    try:
        await b._call_orchestrator("test")
    except Exception:
        pass  # _call_orchestrator puede propagar el OSError — nos interesa el efecto secundario
    finally:
        bridge_module.TTSClient = original

    assert tts_end_called, (
        "tts.end() debe llamarse siempre para señalizar el fin al servidor TTS, "
        "incluso cuando el orquestador lanza una excepción no-RuntimeError"
    )
