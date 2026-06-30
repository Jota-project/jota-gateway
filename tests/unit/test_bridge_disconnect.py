"""Tests for _client_input_loop raw disconnect handling."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.models.schemas import Client, ClientConfig, Handshake

_CLIENT = Client(id="test-uuid", client_key="test-key", is_active=True)
_CONFIG = ClientConfig()


@pytest.fixture
def bridge(mock_tracker):
    ws = AsyncMock()
    b = JotaBridge(client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(), tracker=mock_tracker,
                   handshake=Handshake(client_key="test-key", input_mode="audio", output_mode=["text"]),
                   client_registry=ClientRegistry(), default_agent="main")
    b.transcriber = MagicMock()
    b.transcriber._is_ready = True
    return b


async def test_raw_disconnect_message_exits_loop_cleanly(bridge):
    """{"type":"websocket.disconnect"} must break the loop without raising."""
    bridge.client_ws.receive = AsyncMock(
        return_value={"type": "websocket.disconnect"}
    )

    # Must complete without exception
    await bridge._client_input_loop()

    # Receive was called once — loop exited on first message
    bridge.client_ws.receive.assert_called_once()


async def test_raw_disconnect_does_not_log_error(bridge, caplog):
    """Raw disconnect must not produce an ERROR log entry."""
    import logging
    bridge.client_ws.receive = AsyncMock(
        return_value={"type": "websocket.disconnect"}
    )

    with caplog.at_level(logging.ERROR):
        await bridge._client_input_loop()

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records == []


async def test_empty_string_last_final_text_suppresses_transcriber_unavailable(mock_tracker):
    """_last_final_text='' (transcripción vacía recibida) NO debe enviar 'transcriber unavailable'.

    Bug 9: `not self._last_final_text` es True para '' igual que para None.
    La condición correcta es `self._last_final_text is None` — solo notificar
    cuando no se recibió NINGUNA transcripción final.
    """
    ws = AsyncMock()
    b = JotaBridge(
        client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="test-key", input_mode="audio", output_mode=["text"]),
        client_registry=ClientRegistry(), default_agent="main",
    )

    transcriber = MagicMock()
    transcriber._dropped_unexpectedly = True
    transcriber._is_ready = False
    transcriber.listen_loop = AsyncMock(return_value=None)
    transcriber.close = AsyncMock()
    b.transcriber = transcriber

    # Simular que recibimos una transcripción final vacía (edge case)
    b._last_final_text = ""

    # El input_loop sale inmediatamente (simula desconexión)
    async def _instant_loop():
        return
    b._client_input_loop = _instant_loop

    await b.run()

    # No debe enviarse el aviso de "transcriber unavailable"
    for call in ws.send_json.call_args_list:
        payload = call.args[0] if call.args else call.kwargs.get("json", {})
        assert not (
            payload.get("type") == "service_status"
            and payload.get("service") == "transcriber"
            and payload.get("status") == "unavailable"
        ), f"No debería haberse enviado 'transcriber unavailable' con _last_final_text='': {payload}"


async def test_none_last_final_text_sends_transcriber_unavailable(mock_tracker):
    """_last_final_text=None (sin transcripción recibida) SÍ debe enviar 'transcriber unavailable'."""
    ws = AsyncMock()
    b = JotaBridge(
        client=_CLIENT, config=_CONFIG, client_ws=ws, orchestrator=AsyncMock(),
        tracker=mock_tracker,
        handshake=Handshake(client_key="test-key", input_mode="audio", output_mode=["text"]),
        client_registry=ClientRegistry(), default_agent="main",
    )

    transcriber = MagicMock()
    transcriber._dropped_unexpectedly = True
    transcriber._is_ready = False
    transcriber.listen_loop = AsyncMock(return_value=None)
    transcriber.close = AsyncMock()
    b.transcriber = transcriber

    b._last_final_text = None  # nunca recibimos nada

    async def _instant_loop():
        return
    b._client_input_loop = _instant_loop

    await b.run()

    unavailable_calls = [
        call for call in ws.send_json.call_args_list
        if (call.args[0] if call.args else {}).get("type") == "service_status"
        and (call.args[0] if call.args else {}).get("service") == "transcriber"
        and (call.args[0] if call.args else {}).get("status") == "unavailable"
    ]
    assert unavailable_calls, "Debe enviarse 'transcriber unavailable' cuando _last_final_text is None"


async def test_audio_message_is_still_processed(bridge):
    """bytes messages are still forwarded to the transcriber after the fix."""
    audio = b"\x00\x01\x02"
    bridge.transcriber.send_audio = AsyncMock()
    bridge.client_ws.receive = AsyncMock(side_effect=[
        {"bytes": audio},
        {"type": "websocket.disconnect"},
    ])

    await bridge._client_input_loop()

    bridge.transcriber.send_audio.assert_called_once_with(audio)
