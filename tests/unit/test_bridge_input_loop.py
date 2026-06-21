import json
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.pipeline_tracker import PipelineTracker, _NullWS

_CLIENT = Client(id="hab_sito", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


def _make_bridge(input_mode="audio"):
    ws = AsyncMock()
    registry = MagicMock()
    tracker = PipelineTracker(
        session_id="test:loop", client_id="hab_sito",
        input_mode=input_mode, output_mode=["text"],
        client_ws=_NullWS(), registry=registry,
    )
    handshake = Handshake(client_key="test-key", input_mode=input_mode, output_mode=["text"])
    orch = AsyncMock()
    transcriber = AsyncMock()
    transcriber._is_ready = True
    bridge = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws,
                        orchestrator=orch, tracker=tracker, handshake=handshake)
    bridge.transcriber = transcriber
    return bridge, ws, transcriber


async def test_end_message_calls_transcriber_send_end_in_audio_mode():
    bridge, ws, transcriber = _make_bridge(input_mode="audio")
    # Simulate receiving {"type":"end"} then disconnect
    ws.receive = AsyncMock(side_effect=[
        {"type": "websocket.message", "text": json.dumps({"type": "end"})},
        {"type": "websocket.disconnect"},
    ])
    await bridge._client_input_loop()
    transcriber.send_end.assert_called_once()


async def test_end_message_ignored_in_text_mode():
    bridge, ws, transcriber = _make_bridge(input_mode="text")
    bridge.transcriber = None
    ws.receive = AsyncMock(side_effect=[
        {"type": "websocket.message", "text": json.dumps({"type": "end"})},
        {"type": "websocket.disconnect"},
    ])
    await bridge._client_input_loop()
    # no transcriber → no send_end call, no crash


async def test_send_message_dispatches_to_orchestrator():
    bridge, ws, transcriber = _make_bridge(input_mode="audio")
    bridge._active_turn = None

    ws.receive = AsyncMock(side_effect=[
        {"type": "websocket.message", "text": json.dumps({"type": "send", "text": "hola"})},
        {"type": "websocket.disconnect"},
    ])
    await bridge._client_input_loop()
    # _active_turn should have been created
    assert bridge._active_turn is not None
    bridge._active_turn.cancel()
