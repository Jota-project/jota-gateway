import time
from unittest.mock import AsyncMock, patch

import pytest

from src.services.reconnection import ConnectionState
from src.services.tts_reconnecting import ReconnectingTTSClient


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
    assert w._health.reconnect_attempts == 1
    assert w._health.last_error == "OSError"


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
        assert w.status().last_error == "OSError"

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


@pytest.mark.asyncio
async def test_on_state_change_fires_on_transition_to_reconnecting():
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=10.0)
    seen = []
    w.on_state_change = seen.append
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        mock_client = AsyncMock()
        mock_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = mock_client
        await w.connect(voice=None, speed=None, client_id="c1")
    assert seen == [ConnectionState.RECONNECTING]


@pytest.mark.asyncio
async def test_on_state_change_fires_on_recovery_to_connected():
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=0.05)
    seen = []
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        failing_client = AsyncMock()
        failing_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = failing_client
        await w.connect(voice=None, speed=None, client_id="c1")  # first failure

        w.on_state_change = seen.append  # wired after the initial failure on purpose

        time.sleep(0.1)  # let the backoff window elapse
        succeeding_client = AsyncMock()
        MockTTS.return_value = succeeding_client
        await w.connect(voice=None, speed=None, client_id="c1")

    assert seen == [ConnectionState.CONNECTED]


@pytest.mark.asyncio
async def test_on_state_change_does_not_refire_for_repeated_same_state():
    """A sustained outage calls _record_failure once per turn attempt that
    clears the backoff window — the callback must only fire on the actual
    transition into RECONNECTING, not on every subsequent still-down attempt,
    or every connected client would get spammed with a fresh 'reconnecting'
    notice each time the singleton backoff window elapses."""
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=0.05)
    seen = []
    w.on_state_change = seen.append
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        failing_client = AsyncMock()
        failing_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = failing_client
        await w.connect(voice=None, speed=None, client_id="c1")  # first failure

        time.sleep(0.1)  # let the backoff window elapse
        await w.connect(voice=None, speed=None, client_id="c1")  # still down

    assert seen == [ConnectionState.RECONNECTING]


@pytest.mark.asyncio
async def test_on_state_change_not_called_when_unset():
    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=10.0)
    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        mock_client = AsyncMock()
        mock_client.connect.side_effect = OSError("refused")
        MockTTS.return_value = mock_client
        await w.connect(voice=None, speed=None, client_id="c1")  # must not raise


# --- concurrent turns (#152) -----------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_failures_of_one_outage_double_backoff_once():
    import asyncio

    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=1.0, max_backoff=60.0)
    changes = []
    w.on_state_change = changes.append
    gate = asyncio.Event()

    async def failing_connect(**kwargs):
        await gate.wait()  # all attempts are in flight before any of them fails
        raise OSError("refused")

    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        MockTTS.return_value.connect = failing_connect
        tasks = [
            asyncio.create_task(w.connect(voice=None, speed=None, client_id=f"c{i}"))
            for i in range(5)
        ]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*tasks)

    assert results == [None] * 5
    assert w._gate.backoff == 2.0  # one outage, one doubling (not 32.0)
    assert w._health.reconnect_attempts == 1
    assert changes == [ConnectionState.RECONNECTING]


@pytest.mark.asyncio
async def test_failure_finishing_after_a_newer_success_does_not_clobber_it():
    import asyncio

    w = ReconnectingTTSClient(url="test:1", token="t", initial_backoff=1.0)
    release_slow = asyncio.Event()
    calls = 0

    async def connect(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:  # the slow, older attempt: fails only after the other succeeded
            await release_slow.wait()
            raise OSError("late failure")

    with patch("src.services.tts_reconnecting.TTSClient") as MockTTS:
        MockTTS.return_value.connect = connect
        slow = asyncio.create_task(w.connect(voice=None, speed=None, client_id="a"))
        await asyncio.sleep(0)
        fast = await w.connect(voice=None, speed=None, client_id="b")
        assert fast is not None
        release_slow.set()
        assert await slow is None

    assert w.state == ConnectionState.CONNECTED
    assert w._health.last_error is None
    assert w._gate.backoff == 1.0
    assert w._gate.last_failure_at is None
