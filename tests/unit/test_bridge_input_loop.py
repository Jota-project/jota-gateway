import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
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
    bridge = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws,
                        orchestrator=orch, tts=AsyncMock(), tracker=tracker, handshake=handshake,
                        client_registry=ClientRegistry(), default_agent="main")
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


async def test_plain_text_sets_active_turn():
    """En modo texto, texto plano debe crear un task y asignarlo a _active_turn."""
    bridge, ws, _ = _make_bridge(input_mode="text")
    bridge.transcriber = None
    bridge._active_turn = None

    ws.receive = AsyncMock(side_effect=[
        {"type": "websocket.message", "text": "hola mundo"},
        {"type": "websocket.disconnect"},
    ])

    await bridge._client_input_loop()

    assert bridge._active_turn is not None, (
        "El texto plano debe asignar _active_turn (actualmente es None — loop bloqueante)"
    )
    bridge._active_turn.cancel()


async def test_plain_text_cancels_previous_active_turn():
    """En modo texto, un segundo mensaje plano debe cancelar el turn en vuelo."""
    bridge, ws, _ = _make_bridge(input_mode="text")
    bridge.transcriber = None

    gate = asyncio.Event()

    async def _blocking():
        await gate.wait()

    first_task = asyncio.create_task(_blocking())
    bridge._active_turn = first_task

    ws.receive = AsyncMock(side_effect=[
        {"type": "websocket.message", "text": "nuevo mensaje"},
        {"type": "websocket.disconnect"},
    ])

    await bridge._client_input_loop()

    assert first_task.cancelled(), "El turn anterior debe cancelarse al llegar texto plano nuevo"
    assert bridge._active_turn is not None
    assert bridge._active_turn is not first_task
    bridge._active_turn.cancel()


async def test_cancel_message_cancels_active_turn_without_new_turn():
    """Un {type:'cancel'} debe cancelar el turn en vuelo y NO lanzar uno nuevo."""
    bridge, ws, _ = _make_bridge(input_mode="audio")

    gate = asyncio.Event()

    async def _blocking():
        await gate.wait()

    active = asyncio.create_task(_blocking())
    bridge._active_turn = active

    ws.receive = AsyncMock(side_effect=[
        {"type": "websocket.message", "text": json.dumps({"type": "cancel"})},
        {"type": "websocket.disconnect"},
    ])

    await bridge._client_input_loop()

    assert active.cancelled(), "El turn activo debe cancelarse al recibir {type:'cancel'}"
    assert bridge._active_turn is None, "No debe crearse un nuevo turn tras cancel"


async def test_cancel_message_with_no_active_turn_is_harmless():
    """Un {type:'cancel'} sin turn activo no debe crashear."""
    bridge, ws, _ = _make_bridge(input_mode="audio")
    bridge._active_turn = None

    ws.receive = AsyncMock(side_effect=[
        {"type": "websocket.message", "text": json.dumps({"type": "cancel"})},
        {"type": "websocket.disconnect"},
    ])

    await bridge._client_input_loop()  # no debe lanzar


async def test_second_send_cancels_first_active_turn():
    """Un segundo {type:'send'} debe cancelar el turn en vuelo antes de crear uno nuevo."""
    bridge, ws, _ = _make_bridge(input_mode="audio")

    # Simular un turn en vuelo que bloquea indefinidamente
    gate = asyncio.Event()

    async def _blocking():
        await gate.wait()

    first_task = asyncio.create_task(_blocking())
    bridge._active_turn = first_task

    ws.receive = AsyncMock(side_effect=[
        {"type": "websocket.message", "text": json.dumps({"type": "send", "text": "segundo"})},
        {"type": "websocket.disconnect"},
    ])

    await bridge._client_input_loop()

    assert first_task.cancelled(), (
        "El primer _active_turn debe cancelarse cuando llega un segundo {type:'send'}"
    )
    assert bridge._active_turn is not None
    assert bridge._active_turn is not first_task
    bridge._active_turn.cancel()
