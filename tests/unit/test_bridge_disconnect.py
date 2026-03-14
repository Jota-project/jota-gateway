"""Tests for _client_input_loop raw disconnect handling."""
import pytest
from unittest.mock import AsyncMock, MagicMock, call
from src.services.bridge import JotaBridge
from src.models.schemas import Handshake


@pytest.fixture
def bridge():
    ws = AsyncMock()
    b = JotaBridge(client_id="test", client_ws=ws)
    b.handshake = Handshake(input_mode="audio", output_mode=["text"])
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
