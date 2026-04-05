"""Tests: bridge passes ClientConfig values to TTS and Orchestrator."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.bridge import JotaBridge
from src.models.schemas import Client, ClientConfig, Handshake

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)


def _make_bridge(config: ClientConfig, output_mode=None):
    if output_mode is None:
        output_mode = ["text", "status"]
    ws = AsyncMock()
    bridge = JotaBridge(client=_CLIENT, config=config, client_ws=ws)
    bridge.handshake = Handshake(
        client_key="test-key",
        input_mode="audio",
        output_mode=output_mode,
    )
    bridge.orchestrator = AsyncMock()
    bridge.orchestrator.listen_loop = AsyncMock()
    return bridge


async def test_call_orchestrator_passes_preferred_model_id():
    config = ClientConfig(preferred_model_id="llama3-70b")
    bridge = _make_bridge(config)

    await bridge._call_orchestrator("hola")

    kwargs = bridge.orchestrator.listen_loop.call_args.kwargs
    assert kwargs.get("model_id") == "llama3-70b"


async def test_call_orchestrator_passes_none_model_id_when_not_set():
    config = ClientConfig(preferred_model_id=None)
    bridge = _make_bridge(config)

    await bridge._call_orchestrator("hola")

    kwargs = bridge.orchestrator.listen_loop.call_args.kwargs
    assert kwargs.get("model_id") is None


async def test_call_orchestrator_passes_system_prompt_extra():
    config = ClientConfig(system_prompt_extra="responde siempre en inglés")
    bridge = _make_bridge(config)

    await bridge._call_orchestrator("hola")

    kwargs = bridge.orchestrator.listen_loop.call_args.kwargs
    assert kwargs.get("system_prompt_extra") == "responde siempre en inglés"


async def test_call_orchestrator_passes_none_system_prompt_when_not_set():
    config = ClientConfig(system_prompt_extra=None)
    bridge = _make_bridge(config)

    await bridge._call_orchestrator("hola")

    kwargs = bridge.orchestrator.listen_loop.call_args.kwargs
    assert kwargs.get("system_prompt_extra") is None


async def test_call_orchestrator_passes_voice_and_speed_to_tts():
    """bridge passes config.tts_voice and config.tts_speed to TTSClient.connect()."""
    config = ClientConfig(tts_voice="bf_emma", tts_speed=1.2)
    bridge = _make_bridge(config, output_mode=["audio", "text"])

    mock_tts = AsyncMock()
    mock_tts.connect = AsyncMock()
    mock_tts.end = AsyncMock()
    mock_tts.close = AsyncMock()

    async def empty_audio_stream():
        return
        yield  # make it an async generator

    mock_tts.get_audio_stream = MagicMock(return_value=empty_audio_stream())

    with patch("src.services.bridge.TTSClient", return_value=mock_tts):
        await bridge._call_orchestrator("hola")

    mock_tts.connect.assert_called_once_with(voice="bf_emma", speed=1.2)
