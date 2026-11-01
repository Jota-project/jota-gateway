import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.bridge as bridge_module
from src.services.pipeline_tracker import PipelineTracker
from src.services.reconnection import ConnectionState
from tests.fakes.bridge import sent


def _audio_transcriber():
    t = MagicMock()
    t.state = ConnectionState.CONNECTED
    t.last_transcript_at = None
    t.send_audio = AsyncMock()
    t.send_end = AsyncMock()
    return t


def _text(obj_or_str):
    s = obj_or_str if isinstance(obj_or_str, str) else json.dumps(obj_or_str)
    return {"type": "websocket.receive", "text": s}


@pytest.fixture
def make_loop_bridge(make_bridge):
    def _make(input_mode="audio"):
        tracker = PipelineTracker(
            session_id="test:loop",
            client_id="hab_sito",
            input_mode=input_mode,
            output_mode=["text"],
            registry=MagicMock(),
        )
        transcriber = AsyncMock()
        bridge, ws = make_bridge(
            client_id="hab_sito", input_mode=input_mode, tracker=tracker, transcriber=transcriber
        )
        return bridge, ws, transcriber

    return _make


async def test_end_message_calls_transcriber_send_end_in_audio_mode(make_loop_bridge):
    bridge, ws, transcriber = make_loop_bridge(input_mode="audio")
    # Simulate receiving {"type":"end"} then disconnect
    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": json.dumps({"type": "end"})},
            {"type": "websocket.disconnect"},
        ]
    )
    await bridge._client_input_loop()
    transcriber.send_end.assert_called_once()


async def test_end_message_ignored_in_text_mode(make_loop_bridge):
    bridge, ws, transcriber = make_loop_bridge(input_mode="text")
    bridge.transcriber = None
    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": json.dumps({"type": "end"})},
            {"type": "websocket.disconnect"},
        ]
    )
    await bridge._client_input_loop()
    # no transcriber → no send_end call, no crash


async def test_send_message_dispatches_to_orchestrator(make_loop_bridge):
    bridge, ws, transcriber = make_loop_bridge(input_mode="audio")
    bridge._active_turn = None

    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": json.dumps({"type": "send", "text": "hola"})},
            {"type": "websocket.disconnect"},
        ]
    )
    await bridge._client_input_loop()
    # _active_turn should have been created
    assert bridge._active_turn is not None
    bridge._active_turn.cancel()


async def test_plain_text_sets_active_turn(make_loop_bridge):
    """En modo texto, texto plano debe crear un task y asignarlo a _active_turn."""
    bridge, ws, _ = make_loop_bridge(input_mode="text")
    bridge.transcriber = None
    bridge._active_turn = None

    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": "hola mundo"},
            {"type": "websocket.disconnect"},
        ]
    )

    await bridge._client_input_loop()

    assert bridge._active_turn is not None, (
        "El texto plano debe asignar _active_turn (actualmente es None — loop bloqueante)"
    )
    bridge._active_turn.cancel()


async def test_receiving_message_updates_last_client_activity(make_loop_bridge):
    """#115: cualquier mensaje del cliente (aquí texto plano) actualiza el
    reloj que consume el idle watchdog."""
    bridge, ws, _ = make_loop_bridge(input_mode="text")
    bridge.transcriber = None
    bridge._last_client_activity = 0.0

    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": "hola"},
            {"type": "websocket.disconnect"},
        ]
    )

    await bridge._client_input_loop()

    assert bridge._last_client_activity > 0.0
    bridge._active_turn.cancel()


async def test_plain_text_cancels_previous_active_turn(make_loop_bridge):
    """En modo texto, un segundo mensaje plano debe cancelar el turn en vuelo."""
    bridge, ws, _ = make_loop_bridge(input_mode="text")
    bridge.transcriber = None

    gate = asyncio.Event()

    async def _blocking():
        await gate.wait()

    first_task = asyncio.create_task(_blocking())
    bridge._active_turn = first_task

    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": "nuevo mensaje"},
            {"type": "websocket.disconnect"},
        ]
    )

    await bridge._client_input_loop()

    assert first_task.cancelled(), "El turn anterior debe cancelarse al llegar texto plano nuevo"
    assert bridge._active_turn is not None
    assert bridge._active_turn is not first_task
    bridge._active_turn.cancel()


async def test_cancel_message_cancels_active_turn_without_new_turn(make_loop_bridge):
    """Un {type:'cancel'} debe cancelar el turn en vuelo y NO lanzar uno nuevo."""
    bridge, ws, _ = make_loop_bridge(input_mode="audio")

    gate = asyncio.Event()

    async def _blocking():
        await gate.wait()

    active = asyncio.create_task(_blocking())
    bridge._active_turn = active

    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": json.dumps({"type": "cancel"})},
            {"type": "websocket.disconnect"},
        ]
    )

    await bridge._client_input_loop()

    assert active.cancelled(), "El turn activo debe cancelarse al recibir {type:'cancel'}"
    assert bridge._active_turn is None, "No debe crearse un nuevo turn tras cancel"


async def test_cancel_message_with_no_active_turn_is_harmless(make_loop_bridge):
    """Un {type:'cancel'} sin turn activo no debe crashear."""
    bridge, ws, _ = make_loop_bridge(input_mode="audio")
    bridge._active_turn = None

    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": json.dumps({"type": "cancel"})},
            {"type": "websocket.disconnect"},
        ]
    )

    await bridge._client_input_loop()  # no debe lanzar


async def test_second_send_cancels_first_active_turn(make_loop_bridge):
    """Un segundo {type:'send'} debe cancelar el turn en vuelo antes de crear uno nuevo."""
    bridge, ws, _ = make_loop_bridge(input_mode="audio")

    # Simular un turn en vuelo que bloquea indefinidamente
    gate = asyncio.Event()

    async def _blocking():
        await gate.wait()

    first_task = asyncio.create_task(_blocking())
    bridge._active_turn = first_task

    ws.receive = AsyncMock(
        side_effect=[
            {"type": "websocket.message", "text": json.dumps({"type": "send", "text": "segundo"})},
            {"type": "websocket.disconnect"},
        ]
    )

    await bridge._client_input_loop()

    assert first_task.cancelled(), (
        "El primer _active_turn debe cancelarse cuando llega un segundo {type:'send'}"
    )
    assert bridge._active_turn is not None
    assert bridge._active_turn is not first_task
    bridge._active_turn.cancel()


# ── G-20 (#215): validación de mensajes del cliente ─────────────────────────


async def test_malformed_client_messages_get_a_non_fatal_error_and_the_session_goes_on(make_bridge):
    bridge, ws = make_bridge(input_mode="audio", transcriber=_audio_transcriber())
    bad = ["42", "[1]", '"s"', "true", "null", '{"type":"send","text":5}', '{"type":"send","text":null}']
    for raw in bad:
        ws.inbox.put_nowait(_text(raw))
    ws.inbox.put_nowait(_text({"type": "end"}))
    ws.inbox.put_nowait({"type": "websocket.disconnect"})

    await asyncio.wait_for(bridge._client_input_loop(), 2.0)

    await sent(bridge)
    errors = ws.of("error")
    assert len(errors) == len(bad)
    assert all(e["fatal"] is False and e["code"] == "INVALID_MESSAGE" for e in errors)
    assert "secret" not in json.dumps(errors)
    bridge.transcriber.send_end.assert_awaited_once()  # the loop survived all of them


async def test_deeply_nested_json_is_treated_as_unparsed_text(make_bridge):
    # json.loads raises RecursionError (not ValueError): the payload stays non-JSON
    # and the session goes on, same as any other unparseable text.
    bridge, ws = make_bridge(input_mode="text")
    bridge._start_turn = AsyncMock()
    ws.inbox.put_nowait(_text("[" * 100_000))
    ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(bridge._client_input_loop(), 2.0)
    bridge._start_turn.assert_awaited_once()
    assert bridge._close_reason is None


async def test_a_parser_bug_is_not_mistaken_for_plain_text(make_bridge, monkeypatch):
    # #221: only decoding errors mean "not JSON"; any other failure is a bug and
    # ends the input loop as an error instead of becoming a turn.
    bridge, ws = make_bridge(input_mode="text")
    bridge._start_turn = AsyncMock()
    monkeypatch.setattr(bridge_module.json, "loads", MagicMock(side_effect=TypeError("x")))
    ws.inbox.put_nowait(_text({"type": "send", "text": "hola"}))
    await asyncio.wait_for(bridge._client_input_loop(), 2.0)
    bridge._start_turn.assert_not_awaited()
    assert bridge._close_reason == "error"


async def test_non_string_send_text_never_reaches_the_orchestrator(make_bridge):
    bridge, ws = make_bridge(input_mode="text")
    ws.inbox.put_nowait(_text({"type": "send", "text": ["x"]}))
    ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(bridge._client_input_loop(), 2.0)
    assert bridge._active_turn is None
    await sent(bridge)
    assert len(ws.of("error")) == 1


async def test_keepalive_and_unknown_dict_messages_stay_silent(make_bridge):
    bridge, ws = make_bridge(input_mode="audio", transcriber=_audio_transcriber())
    ws.inbox.put_nowait(_text({"type": "keepalive"}))
    ws.inbox.put_nowait(_text({}))
    ws.inbox.put_nowait(_text({"type": "send"}))  # no text: ignored as before
    ws.inbox.put_nowait({"type": "websocket.disconnect"})
    await asyncio.wait_for(bridge._client_input_loop(), 2.0)
    await sent(bridge)
    assert ws.of("error") == []
