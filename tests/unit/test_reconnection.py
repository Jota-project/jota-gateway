from src.services.reconnection import ConnectionState, ServiceStatus, to_wire_state


def test_to_wire_state_mapping():
    assert to_wire_state(ConnectionState.CONNECTED) == "restored"
    assert to_wire_state(ConnectionState.RECONNECTING) == "reconnecting"
    assert to_wire_state(ConnectionState.DEGRADED) == "unavailable"


def test_service_status_is_a_plain_dataclass():
    s = ServiceStatus(
        name="x",
        state=ConnectionState.CONNECTED,
        connected_at=None,
        reconnect_attempts=0,
        last_error=None,
    )
    assert s.name == "x"
    assert s.state == ConnectionState.CONNECTED


# --- ServiceHealth (#126) ---------------------------------------------------

import asyncio  # noqa: E402

import pytest  # noqa: E402

from src.services.reconnection import (  # noqa: E402
    LazyBackoffGate,
    ServiceHealth,
    reconnect_with_backoff,
)


def test_health_starts_in_given_state_and_reports_status():
    h = ServiceHealth("x", ConnectionState.DEGRADED)
    s = h.status()
    assert (s.name, s.state, s.connected_at, s.reconnect_attempts, s.last_error) == (
        "x",
        ConnectionState.DEGRADED,
        None,
        0,
        None,
    )


def test_set_state_fires_callback_only_on_transition():
    h = ServiceHealth("x", ConnectionState.CONNECTED)
    seen: list[ConnectionState] = []
    h.on_state_change = seen.append
    h.set_state(ConnectionState.CONNECTED)
    h.set_state(ConnectionState.RECONNECTING)
    h.set_state(ConnectionState.RECONNECTING)
    h.set_state(ConnectionState.CONNECTED)
    assert seen == [ConnectionState.RECONNECTING, ConnectionState.CONNECTED]


def test_mark_connected_resets_fields_before_firing_callback():
    h = ServiceHealth("x", ConnectionState.RECONNECTING)
    h.reconnect_attempts = 3
    h.last_error = "boom"
    snapshots = []
    h.on_state_change = lambda st: snapshots.append(h.status())
    h.mark_connected()
    assert len(snapshots) == 1
    assert snapshots[0].state == ConnectionState.CONNECTED
    assert snapshots[0].reconnect_attempts == 0
    assert snapshots[0].last_error is None
    assert snapshots[0].connected_at is not None


def test_mark_attempt_failed_counts_and_records_error():
    h = ServiceHealth("x", ConnectionState.RECONNECTING)
    h.mark_attempt_failed("refused")
    h.mark_attempt_failed("refused again")
    assert h.reconnect_attempts == 2
    assert h.last_error == "refused again"


# --- reconnect_with_backoff -------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_loop_recovers_after_failures():
    h = ServiceHealth("x", ConnectionState.RECONNECTING)
    calls = 0

    async def attempt():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("down")

    ok = await reconnect_with_backoff(
        attempt, h, initial_backoff=0.001, max_backoff=0.002, max_duration=5, label="x"
    )
    assert ok is True
    assert h.state == ConnectionState.CONNECTED
    assert h.reconnect_attempts == 0
    assert h.last_error is None


@pytest.mark.asyncio
async def test_backoff_loop_degrades_when_max_duration_exceeded():
    h = ServiceHealth("x", ConnectionState.RECONNECTING)

    async def attempt():
        raise OSError("down")

    ok = await reconnect_with_backoff(
        attempt, h, initial_backoff=0.001, max_backoff=0.002, max_duration=0.01, label="x"
    )
    assert ok is False
    assert h.state == ConnectionState.DEGRADED
    assert h.reconnect_attempts >= 1
    assert h.last_error == "OSError"  # tipo, no el texto (#228)


@pytest.mark.asyncio
async def test_backoff_loop_doubles_sleep_up_to_cap(monkeypatch):
    h = ServiceHealth("x", ConnectionState.RECONNECTING)
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = 0

    async def attempt():
        nonlocal calls
        calls += 1
        if calls < 6:
            raise OSError("down")

    await reconnect_with_backoff(
        attempt, h, initial_backoff=1.0, max_backoff=4.0, max_duration=1000, label="x"
    )
    assert sleeps == [1.0, 2.0, 4.0, 4.0, 4.0]


@pytest.mark.asyncio
async def test_backoff_loop_propagates_cancellation():
    h = ServiceHealth("x", ConnectionState.RECONNECTING)

    async def attempt():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await reconnect_with_backoff(
            attempt, h, initial_backoff=0.001, max_backoff=0.002, max_duration=5, label="x"
        )


# --- LazyBackoffGate (#152) -------------------------------------------------


def test_gate_allows_first_attempt_and_blocks_within_window():
    g = LazyBackoffGate(initial_backoff=10.0, max_backoff=60.0)
    token = g.begin()
    assert token is not None
    assert g.record_failure(token) is True
    assert g.begin() is None  # inside the 10s window


def test_gate_failures_double_backoff_capped():
    g = LazyBackoffGate(initial_backoff=1.0, max_backoff=3.0)
    for expected in (2.0, 3.0, 3.0):
        g.last_failure_at = None  # force the window open
        token = g.begin()
        assert g.record_failure(token) is True
        assert g.backoff == expected


def test_gate_concurrent_failures_of_one_outage_double_only_once():
    g = LazyBackoffGate(initial_backoff=1.0, max_backoff=60.0)
    a, b, c = g.begin(), g.begin(), g.begin()  # three turns start while healthy
    assert g.record_failure(a) is True
    assert g.record_failure(b) is False
    assert g.record_failure(c) is False
    assert g.backoff == 2.0


def test_gate_success_resets_and_makes_older_inflight_failure_stale():
    g = LazyBackoffGate(initial_backoff=1.0, max_backoff=60.0)
    a = g.begin()
    g.begin()
    g.record_success()
    assert g.record_failure(a) is False  # started before the success: stale
    assert g.backoff == 1.0
    assert g.last_failure_at is None


def test_gate_failure_after_newer_attempt_started_still_counts():
    g = LazyBackoffGate(initial_backoff=1.0, max_backoff=60.0)
    a = g.begin()
    assert g.record_failure(a) is True
    g.last_failure_at = None  # window elapsed
    b = g.begin()
    assert g.record_failure(b) is True  # genuinely new probe, new doubling
    assert g.backoff == 4.0
