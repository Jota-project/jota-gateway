import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.services.reconnection import ConnectionState
from src.services.transcriber_reconnecting import ReconnectingTranscriberClient


def _wrap(**kwargs):
    return ReconnectingTranscriberClient(url="test:1", client_id="c1", **kwargs)


@pytest.mark.asyncio
async def test_initial_connect_success_sets_connected():
    w = _wrap()
    with patch.object(w._client, "connect", new=AsyncMock()):
        await w.connect(language="es", token="k", vad_thold=0.0)
    assert w.state == ConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_initial_connect_failure_does_not_raise_and_sets_reconnecting():
    w = _wrap()
    with patch.object(w._client, "connect", new=AsyncMock(side_effect=OSError("refused"))):
        await w.connect(language="es", token="k", vad_thold=0.0)  # must not raise
    assert w.state == ConnectionState.RECONNECTING
    assert w._last_error == "refused"


@pytest.mark.asyncio
async def test_on_state_change_fires_on_transitions():
    w = _wrap()
    seen = []
    w.on_state_change = seen.append
    with patch.object(w._client, "connect", new=AsyncMock()):
        await w.connect()
    assert seen == [ConnectionState.CONNECTED]


@pytest.mark.asyncio
async def test_run_clean_close_does_not_reconnect():
    """listen_loop returning with _dropped_unexpectedly=False (clean close,
    e.g. our own close() was called) must not trigger the reconnect loop."""
    w = _wrap()
    w.state = ConnectionState.CONNECTED

    async def fake_listen_loop(on_transcription_callback, on_warning_callback=None):
        w._client._dropped_unexpectedly = False  # simulate a clean 1000 close

    w._client.listen_loop = fake_listen_loop
    connect_spy = AsyncMock()
    w._client.connect = connect_spy

    await w.run(on_transcription_callback=AsyncMock())

    connect_spy.assert_not_awaited()  # never entered the reconnect loop


@pytest.mark.asyncio
async def test_run_unexpected_drop_reconnects_and_resumes_listening():
    w = _wrap(initial_backoff=0.01, max_backoff=0.01, max_duration=1.0)
    w.state = ConnectionState.CONNECTED

    calls = {"listen": 0}

    async def fake_listen_loop(on_transcription_callback, on_warning_callback=None):
        calls["listen"] += 1
        if calls["listen"] == 1:
            w._client._dropped_unexpectedly = True  # first cycle: unexpected drop
        else:
            w._client._dropped_unexpectedly = False  # second cycle: clean close, stop
            w._closed = True

    w._client.listen_loop = fake_listen_loop
    w._client.connect = AsyncMock()  # reconnect succeeds instantly

    await asyncio.wait_for(w.run(on_transcription_callback=AsyncMock()), timeout=2.0)

    assert calls["listen"] == 2
    assert w.state == ConnectionState.CONNECTED


@pytest.mark.asyncio
async def test_reconnect_exhausted_enters_degraded_and_run_returns():
    w = _wrap(initial_backoff=0.01, max_backoff=0.01, max_duration=0.05)
    w.state = ConnectionState.RECONNECTING
    w._client.connect = AsyncMock(side_effect=OSError("still down"))
    w._client.listen_loop = AsyncMock()  # must never be reached

    await asyncio.wait_for(w.run(on_transcription_callback=AsyncMock()), timeout=2.0)

    assert w.state == ConnectionState.DEGRADED
    w._client.listen_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_audio_send_end_close_delegate_to_inner_client():
    w = _wrap()
    w._client.send_audio = AsyncMock()
    w._client.send_end = AsyncMock()
    w._client.close = AsyncMock()

    await w.send_audio(b"\x00\x01")
    await w.send_end()
    await w.close()

    w._client.send_audio.assert_awaited_once_with(b"\x00\x01")
    w._client.send_end.assert_awaited_once()
    w._client.close.assert_awaited_once()
    assert w._closed is True


@pytest.mark.asyncio
async def test_close_cancels_in_flight_reconnect_loop():
    """Regression test: close() must interrupt run() even while it is stuck
    mid-backoff inside _reconnect_loop(), so the supervising task and its
    socket don't leak after the caller believes the session is torn down."""
    w = _wrap(initial_backoff=0.01, max_backoff=0.01, max_duration=1000.0)
    w.state = ConnectionState.RECONNECTING
    w._client.connect = AsyncMock(side_effect=OSError("still down"))
    w._client.listen_loop = AsyncMock()  # must never be reached
    w._client.close = AsyncMock()

    task = asyncio.create_task(w.run(on_transcription_callback=AsyncMock()))
    await asyncio.sleep(0.05)  # let it enter _reconnect_loop's backoff a few times
    assert not task.done()

    await asyncio.wait_for(w.close(), timeout=2.0)  # must alone stop the task, no external cancel

    # close() already cancelled-and-awaited the run task internally; just confirm
    # it actually finished promptly rather than being left running/leaked.
    assert task.done()
    assert task.cancelled()
    w._client.listen_loop.assert_not_awaited()
    w._client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconnect_loop_success_clears_last_error():
    """Issue #104: a successful reconnect via _reconnect_loop() must clear
    _last_error — otherwise status() keeps reporting a stale error from
    before the drop even though the transcriber is healthy again right now."""
    w = _wrap(initial_backoff=0.01, max_backoff=0.01, max_duration=1.0)
    w._client.connect = AsyncMock(side_effect=[OSError("refused"), None])

    recovered = await w._reconnect_loop()

    assert recovered is True
    assert w.state == ConnectionState.CONNECTED
    assert w._last_error is None


def test_last_transcription_at_proxies_inner_client():
    w = _wrap()
    w._client._last_transcription_at = 123.45
    assert w._last_transcription_at == 123.45


def test_status_shape():
    w = _wrap()
    s = w.status()
    assert s.name == "transcriber"
    assert s.state == ConnectionState.DEGRADED
    assert s.reconnect_attempts == 0
    assert s.last_error is None
