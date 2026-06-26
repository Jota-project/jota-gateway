# Orchestrator Reconnection — Design Spec

**Date:** 2026-06-04  
**Issue:** #46  
**Branch:** feat/openclaw-orchestrator

---

## Problem

When a downstream orchestrator (e.g. OpenClaw) restarts or its WebSocket connection drops, jota-gateway has no reconnection logic. The `_listen()` background task in `OpenClawClient` exits on error, leaving the connection broken. The next call to `stream_response()` fails with an unhandled exception, and any in-flight turn is abandoned silently.

---

## Goals

- Detect orchestrator disconnections automatically.
- Attempt reconnection with configurable exponential backoff.
- After exhausting retries, enter a degraded mode that returns a clear error to callers rather than throwing.
- Allow manual reconnection via a REST endpoint.
- Expose orchestrator connection state via a status endpoint.

---

## Non-goals

- Manual disconnect endpoint (`POST /disconnect`).
- Per-request retry (reconnect is at the connection level, not the call level).
- Failover to a secondary orchestrator.

---

## Architecture

### Components

```
src/services/orchestrators/
    reconnecting.py          NEW — ReconnectingOrchestrator, OrchestratorState, OrchestratorStatus
    openclaw_client.py       MODIFIED — add on_disconnect callback
    registry.py              MODIFIED — wrap clients, add get_status() / reconnect()
src/core/config.py           MODIFIED — 3 new settings
src/api/routes/
    orchestrators.py         NEW — GET /status + POST /reconnect endpoints
tests/integration/
    test_reconnecting_orchestrator.py  NEW
```

### Layering

```
OrchestratorProtocol (interface)
    ├── OpenClawClient              ← protocol only (WebSocket + OpenClaw framing)
    └── ReconnectingOrchestrator    ← lifecycle wrapper (state machine + reconnect loop)
```

`ReconnectingOrchestrator` implements `OrchestratorProtocol` and is transparent to the rest of the codebase. `build_registry()` wraps each client at construction time.

---

## State Machine

```
CONNECTED ──(on_disconnect callback)──> RECONNECTING
RECONNECTING ──(connect() succeeds)──> CONNECTED
RECONNECTING ──(max_duration elapsed)──> DEGRADED
DEGRADED ──(next stream_response/ping call)──> RECONNECTING  (lazy)
DEGRADED ──(POST /api/orchestrators/{name}/reconnect)──> RECONNECTING  (manual)
```

State transitions are atomic (protected by a single asyncio lock to avoid double-launch of the reconnect task).

---

## `ReconnectingOrchestrator`

### Interface

```python
class ReconnectingOrchestrator(OrchestratorProtocol):
    def __init__(self, client: OrchestratorProtocol, name: str)

    # OrchestratorProtocol — delegate to inner client
    async def connect() -> None
    async def close() -> None
    async def ping() -> bool
    async def stream_response(...) -> AsyncIterator[OrchestratorEvent]

    # Observability / control
    def status() -> OrchestratorStatus
    async def trigger_reconnect() -> None
```

### `OrchestratorStatus`

```python
@dataclass
class OrchestratorStatus:
    name: str
    state: OrchestratorState          # CONNECTED | RECONNECTING | DEGRADED
    connected_at: Optional[datetime]
    disconnected_at: Optional[datetime]
    reconnect_attempts: int
    last_error: Optional[str]
```

### Behavior by state

| State | `ping()` | `stream_response()` |
|---|---|---|
| `CONNECTED` | delegate to client | delegate to client |
| `RECONNECTING` | `False` immediately | yield `error: orchestrator_unavailable` |
| `DEGRADED` | `False` + trigger lazy reconnect | yield `error: orchestrator_unavailable` + trigger lazy reconnect |

### Reconnect loop

```python
async def _reconnect_loop(self):
    self._state = RECONNECTING
    start = now()
    backoff = settings.ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF

    while elapsed(start) < settings.ORCHESTRATOR_RECONNECT_MAX_DURATION:
        try:
            await self._client.connect()
            self._state = CONNECTED
            self._reconnect_attempts = 0
            return
        except CancelledError:
            raise
        except Exception as e:
            self._reconnect_attempts += 1
            self._last_error = str(e)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, settings.ORCHESTRATOR_RECONNECT_MAX_BACKOFF)

    self._state = DEGRADED
```

`trigger_reconnect()` cancels any existing task before launching a new one, so it is safe to call from both `DEGRADED` (lazy/manual) and `RECONNECTING` (manual override).

---

## `OpenClawClient` changes

Add one optional constructor parameter:

```python
on_disconnect: Optional[Callable[[], None]] = None
```

`_listen()` calls `on_disconnect()` when it detects a connection drop (i.e. the `async for` exits normally or raises an exception). It does **not** call it on `CancelledError` — that signals a clean gateway shutdown.

No reconnect logic is added to the client itself. It remains protocol-only.

---

## Settings

Three new fields in `src/core/config.py` (all overridable via environment variables):

```python
ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF: float = 1.0   # seconds
ORCHESTRATOR_RECONNECT_MAX_BACKOFF: float = 60.0      # seconds
ORCHESTRATOR_RECONNECT_MAX_DURATION: float = 300.0    # seconds → DEGRADED
```

---

## API Routes (`src/api/routes/orchestrators.py`)

Both routes use `Depends(get_verified_client)` — same auth as all `/api/*` routes.

### `GET /api/orchestrators/{name}/status`

Returns current state of the named orchestrator.

**Response 200:**
```json
{
  "name": "openclaw",
  "state": "DEGRADED",
  "connected_at": "2026-06-04T10:00:00Z",
  "disconnected_at": "2026-06-04T10:05:32Z",
  "reconnect_attempts": 8,
  "last_error": "Connection refused"
}
```

**Response 404:** orchestrator name not registered.

### `POST /api/orchestrators/{name}/reconnect`

Triggers a reconnection attempt regardless of current state. Returns immediately.

**Response 202 Accepted.**  
**Response 404:** orchestrator name not registered.

### Registry additions

```python
def get_status(name: str) -> OrchestratorStatus
async def reconnect(name: str) -> None
```

---

## Error propagation to WebSocket clients

`stream_response()` yields `OrchestratorEvent(type="error", content="orchestrator_unavailable")` when the orchestrator is not `CONNECTED`. `JotaBridge._on_event()` (`bridge.py:338`) already forwards error events to the client as `{"type": "error", "content": "..."}`. No changes to `bridge.py`.

---

## Tests (`tests/integration/test_reconnecting_orchestrator.py`)

| Test | What it verifies |
|---|---|
| `test_disconnect_triggers_reconnecting_state` | `on_disconnect` callback transitions state to `RECONNECTING` |
| `test_stream_response_while_reconnecting_yields_error` | `stream_response` yields error immediately in `RECONNECTING` |
| `test_reconnect_success_restores_connected_state` | Successful `connect()` transitions back to `CONNECTED` |
| `test_reconnect_exhausted_goes_degraded` | `max_duration` exhausted → state becomes `DEGRADED` |
| `test_lazy_reconnect_on_stream_in_degraded` | `stream_response` in `DEGRADED` triggers background reconnect |
| `test_manual_trigger_reconnect` | `trigger_reconnect()` starts new task from `DEGRADED` |
| `test_status_fields` | `status()` returns correct timestamps and attempt counter |
