# Transcriber Failure Handling — Gateway Design

**Date:** 2026-03-14
**Status:** Approved

## Problem

When the transcriber service closes its WebSocket unexpectedly (e.g. rate limit exceeded), the gateway's `listen_loop` exits silently via `ConnectionClosed`. `bridge.run()` detects the task completion via `asyncio.wait(FIRST_COMPLETED)` and calls `close_all()`, disconnecting the client with no explanation.

A second failure mode occurs when the transcriber is alive but hallucinating — it never emits a transcription message to the gateway. The client streams audio into a black hole until the transcriber eventually dies, at which point the first failure mode triggers.

## Goals

1. Send a descriptive `service_status` message to the client before closing the session when the transcriber drops unexpectedly.
2. Detect prolonged silence from the transcriber (no transcription received within N seconds) and proactively notify + close the session.

## Non-Goals

- Automatic transcriber reconnect (out of scope; transcriber is being fixed separately).
- Degraded mode (keep session open without transcriber).

## Design

### 1. TranscriberClient — `transcriber_client.py`

Add two new attributes in `__init__`:

```python
self._dropped_unexpectedly: bool = False
self._last_transcription_at: Optional[float] = None  # time.monotonic()
```

**Unexpected drop detection** — in `listen_loop`, distinguish between a normal close (bridge called `close()` which sets `_is_ready = False` first) and an unexpected one:

```python
except ConnectionClosed:
    if self._is_ready:          # still ready → nobody called close() explicitly
        self._dropped_unexpectedly = True
    self._is_ready = False
    logger.info(...)
```

**Transcription timestamp** — update `_last_transcription_at` whenever a valid transcription arrives:

```python
if t_msg.type == "transcription" and t_msg.text:
    self._last_transcription_at = time.monotonic()
    await on_transcription_callback(...)
```

### 2. JotaBridge — `bridge.py`

**`_session_start`** — record `time.monotonic()` at the start of `run()` so the watchdog can measure elapsed time before any transcription arrives.

**`_transcription_watchdog()`** — new async method, launched as a task in `run()` (only when `input_mode == "audio"`):

```python
async def _transcription_watchdog(self):
    timeout = settings.TRANSCRIBER_SILENCE_TIMEOUT_S
    await asyncio.sleep(timeout)   # initial grace period

    while True:
        await asyncio.sleep(2)
        if not self.transcriber or not self.transcriber._is_ready:
            return  # transcriber already closed; other mechanism handles it

        last = self.transcriber._last_transcription_at
        elapsed = time.monotonic() - last if last else time.monotonic() - self._session_start

        if elapsed > timeout:
            logger.warning(f"[{self.client_id}] Watchdog: {elapsed:.1f}s sin transcripción")
            try:
                await self.client_ws.send_json({
                    "type": "service_status",
                    "service": "transcriber",
                    "status": "degraded",
                    "message": "No transcription received — check microphone or audio quality",
                })
            except Exception:
                pass
            return  # causes asyncio.wait(FIRST_COMPLETED) to fire → session closes
```

**`run()` changes:**

```python
async def run(self):
    self._session_start = time.monotonic()
    self.tasks.append(asyncio.create_task(self._client_input_loop()))

    if self.transcriber:
        self.tasks.append(asyncio.create_task(
            self.transcriber.listen_loop(on_transcription_callback=self._on_transcription)
        ))
        self.tasks.append(asyncio.create_task(self._transcription_watchdog()))

    try:
        done, pending = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[{self.client_id}] Loop crasheó: {e}")

        # Notify client if the transcriber dropped unexpectedly
        if self.transcriber and self.transcriber._dropped_unexpectedly:
            try:
                await self.client_ws.send_json({
                    "type": "service_status",
                    "service": "transcriber",
                    "status": "unavailable",
                    "message": "Transcriber connection lost unexpectedly",
                })
            except Exception:
                pass
    finally:
        await self.close_all()
```

### 3. Config — `core/config.py`

```python
TRANSCRIBER_SILENCE_TIMEOUT_S: int = 10
```

## Data Flow

```
Transcriber closes (rate limit / crash)
  → listen_loop catches ConnectionClosed
  → _is_ready was True → sets _dropped_unexpectedly = True
  → task exits
  → asyncio.wait(FIRST_COMPLETED) fires in bridge.run()
  → _dropped_unexpectedly is True → send service_status(unavailable) to client
  → close_all() → session ends cleanly
```

```
Transcriber alive but silent (hallucinations suppressed internally)
  → watchdog sleeps TRANSCRIBER_SILENCE_TIMEOUT_S
  → checks _last_transcription_at, elapsed > timeout
  → sends service_status(degraded) to client
  → watchdog returns
  → asyncio.wait(FIRST_COMPLETED) fires
  → close_all() → session ends cleanly
```

## Files Changed

| File | Change |
|---|---|
| `src/services/transcriber_client.py` | Add `_dropped_unexpectedly`, `_last_transcription_at`; update `listen_loop` |
| `src/services/bridge.py` | Add `_session_start`, `_transcription_watchdog()`; update `run()` |
| `src/core/config.py` | Add `TRANSCRIBER_SILENCE_TIMEOUT_S` |

## Open Questions

- None. Transcriber reconnect is explicitly out of scope.
