import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DEGRADED = "DEGRADED"


@dataclass
class ServiceStatus:
    name: str
    state: ConnectionState
    connected_at: datetime | None
    reconnect_attempts: int
    last_error: str | None


def to_wire_state(state: ConnectionState) -> str:
    """Maps a ConnectionState to the client-facing `status` wire vocabulary.

    Only call this at an actual transition — the caller decides whether a
    transition is worth notifying a client about at all (e.g. don't call
    this for CONNECTED on a session's very first successful connect, since
    nothing was ever broken).
    """
    return {
        ConnectionState.CONNECTED: "restored",
        ConnectionState.RECONNECTING: "reconnecting",
        ConnectionState.DEGRADED: "unavailable",
    }[state]


def health_attr(name: str) -> property:
    """Read/write property forwarding `name` to the wrapper's `self._health`, so a
    wrapper keeps its historical attributes (`state`, `on_state_change`, ...)
    while the data lives in the composed ServiceHealth."""

    def getter(self):
        return getattr(self._health, name)

    def setter(self, value) -> None:
        setattr(self._health, name, value)

    return property(getter, setter)


class ServiceHealth:
    """Observable connection state of one service, shared by composition.

    Each reconnecting wrapper *has* one of these (no inheritance, issue #126):
    it owns `state`, the counters `status()` reports, and the `on_state_change`
    hook. `set_state()` fires the hook only on an actual transition, so callers
    can record every attempt without re-broadcasting "still down"/"still up".
    """

    def __init__(self, name: str, initial_state: ConnectionState) -> None:
        self.name = name
        self.state = initial_state
        self.connected_at: datetime | None = None
        self.reconnect_attempts = 0
        self.last_error: str | None = None
        self.on_state_change: Callable[[ConnectionState], None] | None = None

    def set_state(self, state: ConnectionState) -> None:
        if state == self.state:
            return
        self.state = state
        if self.on_state_change:
            self.on_state_change(state)

    def mark_connected(self) -> None:
        # Fields first, hook last: a callback calling status() sees the final state.
        self.connected_at = datetime.now(UTC)
        self.reconnect_attempts = 0
        self.last_error = None
        self.set_state(ConnectionState.CONNECTED)

    def mark_attempt_failed(self, error: str) -> None:
        self.reconnect_attempts += 1
        self.last_error = error

    def status(self) -> ServiceStatus:
        return ServiceStatus(
            name=self.name,
            state=self.state,
            connected_at=self.connected_at,
            reconnect_attempts=self.reconnect_attempts,
            last_error=self.last_error,
        )


async def reconnect_with_backoff(
    attempt: Callable[[], Awaitable[None]],
    health: ServiceHealth,
    *,
    initial_backoff: float,
    max_backoff: float,
    max_duration: float,
    label: str,
) -> bool:
    """Retries `attempt()` with exponential backoff until it succeeds or
    `max_duration` elapses. True on success (health -> CONNECTED); False once
    exhausted (health -> DEGRADED). Cancellation propagates."""
    start = time.monotonic()
    backoff = initial_backoff
    while True:
        try:
            await attempt()
            health.mark_connected()
            logger.info("[%s] reconnected.", label)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            health.mark_attempt_failed(str(e))
            logger.warning(
                "[%s] reconnect attempt %d failed: %s", label, health.reconnect_attempts, e
            )

        if time.monotonic() - start >= max_duration:
            health.set_state(ConnectionState.DEGRADED)
            logger.warning("[%s] reconnect exhausted — DEGRADED.", label)
            return False

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


class LazyBackoffGate:
    """Lazy backoff window for services with no persistent connection (TTS).

    `begin()` returns an attempt token, or None while the window from the last
    recorded failure hasn't elapsed. Attempts may overlap (concurrent turns), so
    outcomes are ordered by token instead of by completion time (issue #152) —
    everything is synchronous (no await between check and update), which is what
    makes it safe on the event loop without an `asyncio.Lock` held across IO:

    - a success always applies and makes every in-flight attempt started before
      it stale;
    - a failure applies only if its attempt started after the last recorded
      outcome; otherwise it belongs to the same outage (or predates a success)
      and must neither double the backoff again nor overwrite fresher state.
    """

    def __init__(self, initial_backoff: float, max_backoff: float) -> None:
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff = initial_backoff
        self.last_failure_at: float | None = None
        self._started = 0  # tokens handed out so far
        self._marker = 0  # `_started` at the last recorded outcome

    def begin(self) -> int | None:
        if (
            self.last_failure_at is not None
            and (time.monotonic() - self.last_failure_at) < self.backoff
        ):
            return None
        self._started += 1
        return self._started

    def record_failure(self, token: int) -> bool:
        """True if applied (the caller should record the error/state)."""
        if token <= self._marker:
            return False
        self._marker = self._started
        self.last_failure_at = time.monotonic()
        self.backoff = min(self.backoff * 2, self.max_backoff)
        return True

    def record_success(self) -> None:
        self._marker = max(self._marker, self._started)
        self.last_failure_at = None
        self.backoff = self.initial_backoff
