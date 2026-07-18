import time
import pytest
from unittest.mock import AsyncMock, patch
from src.services.tts_reconnecting import ReconnectingTTSClient
from src.services.reconnection import ConnectionState


@pytest.mark.asyncio
async def test_successful_connect_returns_client_and_stays_connected():
    w = ReconnectingTTSClient(url="test:1", token="t")
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        mock_client = AsyncMock()
        MockTTS.return_value = mock_client
        result = await w.connect(voice="v", speed=1.0, client_id="c1")
    assert result is mock_client
    mock_client.connect.assert_awaited_once_with(voice="v", speed=1.0)
    assert w.state == ConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_failed_connect_returns_none_and_records_failure():
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=10.0)
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        mock_client = AsyncMock()
        mock_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = mock_client
        result = await w.connect(voice=None, speed=None, client_id="c1")
    assert result is None
    assert w.state == ConnectionState.RECONNECTING
    assert w._reconnect_attempts == 1
    assert w._last_error == "refused"


@pytest.mark.asyncio
async def test_second_attempt_within_backoff_window_is_skipped_without_trying():
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=10.0)
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        mock_client = AsyncMock()
        mock_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = mock_client
        await w.connect(voice=None, speed=None, client_id="c1")  # first failure

        MockTTS.reset_mock()
        result = await w.connect(voice=None, speed=None, client_id="c1")  # too soon

    assert result is None
    MockTTS.assert_not_called()  # never even tried to construct a client


@pytest.mark.asyncio
async def test_attempt_after_backoff_elapses_tries_again():
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=0.05)
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        failing_client = AsyncMock()
        failing_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = failing_client
        await w.connect(voice=None, speed=None, client_id="c1")

        time.sleep(0.1)  # let the backoff window elapse

        succeeding_client = AsyncMock()
        MockTTS.return_value = succeeding_client
        result = await w.connect(voice=None, speed=None, client_id="c1")

    assert result is succeeding_client
    assert w.state == ConnectionState.CONNECTED
    assert w._reconnect_attempts == 0  # reset on success


@pytest.mark.asyncio
async def test_backoff_doubles_on_repeated_failure_capped_at_max():
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=1.0, max_backoff=1.5)
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        mock_client = AsyncMock()
        mock_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = mock_client
        w._last_failure_at = time.monotonic() - 100  # force should_attempt True
        await w.connect(voice=None, speed=None, client_id="c1")
    assert w._backoff == 1.5  # doubled from 1.0, capped at max_backoff


@pytest.mark.asyncio
async def test_record_success_clears_last_error_after_prior_failure():
    """Issue #104 regression check: unlike the other two reconnecting
    wrappers, TTS funnels both outcomes through a single _record_success()/
    _record_failure() pair — _record_success() already clears _last_error
    (no production change needed here), this locks that behavior in with an
    explicit test rather than leaving it implicitly covered."""
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=0.05)
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        failing_client = AsyncMock()
        failing_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = failing_client
        await w.connect(voice=None, speed=None, client_id="c1")
        assert w.status().last_error == "refused"

        time.sleep(0.1)  # let the backoff window elapse

        succeeding_client = AsyncMock()
        MockTTS.return_value = succeeding_client
        await w.connect(voice=None, speed=None, client_id="c1")

    assert w.status().last_error is None


def test_status_shape_defaults_to_connected():
    w = ReconnectingTTSClient(url="test:1", token="t")
    s = w.status()
    assert s.name == "tts"
    assert s.state == ConnectionState.CONNECTED
    assert s.reconnect_attempts == 0
    assert s.connected_at is None
