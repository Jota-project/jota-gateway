import pytest
from unittest.mock import AsyncMock, patch

from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.models.schemas import Client, ClientConfig, Handshake


def make_bridge(output_mode=("text",), client_id="hab_sito"):
    client = Client(id=client_id, client_key="key-123", is_active=True)
    config = ClientConfig()
    ws = AsyncMock()
    orchestrator = AsyncMock()
    orchestrator.ping = AsyncMock(return_value=True)
    tracker = AsyncMock()
    handshake = Handshake(
        client_key="key-123",
        input_mode="text",
        output_mode=list(output_mode),
        agent="main",
    )
    client_registry = ClientRegistry()
    bridge = JotaBridge(
        client=client, config=config, client_ws=ws,
        orchestrator=orchestrator, tracker=tracker, handshake=handshake,
        client_registry=client_registry, default_agent="main",
    )
    return bridge, client_registry


@pytest.mark.asyncio
async def test_connect_registers_in_client_registry():
    bridge, registry = make_bridge()
    await bridge.connect_internal_services()
    assert registry.get("hab_sito") is bridge


@pytest.mark.asyncio
async def test_close_unregisters_from_client_registry():
    bridge, registry = make_bridge()
    await bridge.connect_internal_services()
    bridge.tracker.close = AsyncMock()
    await bridge.close_all()
    assert registry.get("hab_sito") is None


@pytest.mark.asyncio
async def test_deliver_push_text_sends_push_message():
    bridge, _ = make_bridge(output_mode=("text",))
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Buenos días!"}
    await bridge.deliver_push(payload)
    bridge.client_ws.send_json.assert_awaited_once_with({
        "type": "push", "content": "Buenos días!"
    })


@pytest.mark.asyncio
async def test_deliver_push_empty_delta_ignored():
    bridge, _ = make_bridge(output_mode=("text",))
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": ""}
    await bridge.deliver_push(payload)
    bridge.client_ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_push_turn_start_no_audio_does_nothing():
    bridge, _ = make_bridge(output_mode=("text",))
    await bridge.on_push_turn_start("agent:main:hab_sito")
    assert bridge._push_tts is None


@pytest.mark.asyncio
async def test_on_push_turn_end_closes_push_tts():
    bridge, _ = make_bridge(output_mode=("audio", "text"))
    mock_tts = AsyncMock()
    bridge._push_tts = mock_tts
    await bridge.on_push_turn_end("agent:main:hab_sito")
    mock_tts.end.assert_awaited_once()
    mock_tts.close.assert_awaited_once()
    assert bridge._push_tts is None


@pytest.mark.asyncio
async def test_on_push_turn_start_audio_creates_tts():
    bridge, _ = make_bridge(output_mode=("audio", "text"))
    with patch("src.services.bridge.TTSClient") as MockTTS:
        mock_tts = AsyncMock()
        MockTTS.return_value = mock_tts
        await bridge.on_push_turn_start("agent:main:hab_sito")
        MockTTS.assert_called_once()
        mock_tts.connect.assert_awaited_once()
        assert bridge._push_tts is mock_tts


@pytest.mark.asyncio
async def test_deliver_push_with_tts_sends_to_tts():
    bridge, _ = make_bridge(output_mode=("audio", "text"))
    mock_tts = AsyncMock()
    bridge._push_tts = mock_tts
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Hola!"}
    await bridge.deliver_push(payload)
    mock_tts.send_text_chunk.assert_awaited_once_with("Hola!")


@pytest.mark.asyncio
async def test_on_push_turn_end_no_tts_is_noop():
    bridge, _ = make_bridge()
    assert bridge._push_tts is None
    await bridge.on_push_turn_end("agent:main:hab_sito")  # must not raise
    assert bridge._push_tts is None
