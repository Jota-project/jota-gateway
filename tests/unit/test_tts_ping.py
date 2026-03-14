"""Tests for TTSClient.ping() static method."""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.tts_client import TTSClient


async def test_ping_ws_url_hits_http_health():
    """ws://host:port/path → GET http://host:port/health"""
    mock_response = MagicMock()
    mock_response.is_success = True

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tts_client.httpx.AsyncClient", return_value=mock_client):
        result = await TTSClient.ping("ws://localhost:8005/ws")

    assert result is True
    mock_client.get.assert_called_once_with("http://localhost:8005/health", timeout=5.0)


async def test_ping_wss_url_hits_https_health():
    """wss://host/path → GET https://host/health"""
    mock_response = MagicMock()
    mock_response.is_success = True

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tts_client.httpx.AsyncClient", return_value=mock_client):
        result = await TTSClient.ping("wss://tts.example.com/synthesize")

    assert result is True
    mock_client.get.assert_called_once_with("https://tts.example.com/health", timeout=5.0)


async def test_ping_returns_false_on_503():
    mock_response = MagicMock()
    mock_response.is_success = False

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tts_client.httpx.AsyncClient", return_value=mock_client):
        result = await TTSClient.ping("ws://localhost:8005/ws")

    assert result is False


async def test_ping_returns_false_on_empty_url():
    """Triggering incident: TTS_WS_URL='' must not crash, must return False."""
    result = await TTSClient.ping("")
    assert result is False


async def test_ping_returns_false_on_network_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.tts_client.httpx.AsyncClient", return_value=mock_client):
        result = await TTSClient.ping("ws://localhost:8005/ws")

    assert result is False
