"""Tests for TTSClient.connect() — auth handshake with voice/speed."""
import json
import pytest
from unittest.mock import AsyncMock, patch
from src.services.tts_client import TTSClient


def _make_ws(auth_response: dict):
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps(auth_response))
    ws.send = AsyncMock()
    return ws


@pytest.fixture
def mock_ws_connect():
    ws = _make_ws({"type": "auth_ok"})
    mock_connect = AsyncMock(return_value=ws)
    with patch("src.services.tts_client.websockets.connect", mock_connect) as p:
        yield p, ws


async def test_connect_sends_token_only_when_no_voice_speed(mock_ws_connect):
    """If voice/speed are None, auth message contains only token."""
    _, ws = mock_ws_connect
    client = TTSClient(url="localhost:8005", token="key123", client_id="cid")

    await client.connect()

    sent = json.loads(ws.send.call_args[0][0])
    assert sent == {"type": "auth", "token": "key123"}


async def test_connect_includes_voice_when_provided(mock_ws_connect):
    _, ws = mock_ws_connect
    client = TTSClient(url="localhost:8005", token="key123", client_id="cid")

    await client.connect(voice="af_heart")

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["voice"] == "af_heart"
    assert sent["token"] == "key123"


async def test_connect_includes_speed_when_provided(mock_ws_connect):
    _, ws = mock_ws_connect
    client = TTSClient(url="localhost:8005", token="key123", client_id="cid")

    await client.connect(speed=1.25)

    sent = json.loads(ws.send.call_args[0][0])
    assert sent["speed"] == 1.25


async def test_connect_includes_both_voice_and_speed(mock_ws_connect):
    _, ws = mock_ws_connect
    client = TTSClient(url="localhost:8005", token="key123", client_id="cid")

    await client.connect(voice="bf_emma", speed=0.9)

    sent = json.loads(ws.send.call_args[0][0])
    assert sent == {"type": "auth", "token": "key123", "voice": "bf_emma", "speed": 0.9}


async def test_connect_raises_on_auth_failure(mock_ws_connect):
    _, ws = mock_ws_connect
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth_error", "message": "bad key"}))
    client = TTSClient(url="localhost:8005", token="badkey", client_id="cid")

    with pytest.raises(RuntimeError, match="auth failed"):
        await client.connect()
