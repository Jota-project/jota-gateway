# Service Health Check — Design Spec

**Date:** 2026-03-14
**Status:** Approved

## Problem

When a client session starts, the gateway connects to its microservices
(Orchestrator, Transcriber, TTS) but does not verify that they are actually
healthy before launching the session loops. Failures surface late — in the
middle of a live session — with no structured notification to the client.

The triggering incident: `TTS_WS_URL=""` in `.env` caused a crash on first
transcription with a raw exception message, no client warning.

## Goal

After the handshake and service connections are established, run a pre-session
health check. Send structured JSON notifications to the client for every
unavailable service. Abort the session for critical failures; continue in
degraded mode for non-critical ones.

---

## Criticality Rules

| Service      | Active when                        | Failure action                        |
|--------------|------------------------------------|---------------------------------------|
| Orchestrator | Always                             | Send error status → close session     |
| Transcriber  | `input_mode == "audio"`            | Send error status → close session     |
| TTS          | `"audio" in output_mode`           | Send warning status → continue (degraded) |

TTS is always non-critical regardless of output_mode composition. If audio is
the only declared output mode and TTS is down, the client receives the warning
and must handle the absence of audio frames.

---

## Client Protocol — New Message Type

```json
// Warning: session continues
{
  "type": "service_status",
  "service": "tts",
  "status": "unavailable",
  "message": "Audio output unavailable"
}

// Error: session will be closed
{
  "type": "service_status",
  "service": "orchestrator",
  "status": "unavailable",
  "message": "Orchestrator unavailable, closing session"
}
```

---

## Architecture

### Approach: `ping()` per client, `health_check()` in bridge

Each client is responsible for knowing how to verify itself. The bridge
orchestrates the results and applies criticality rules.

### Session startup sequence (updated)

```
WebSocket accepted
       │
  Handshake
       │
  connect_internal_services()   ← unchanged
       │
  health_check()                ← NEW
       ├─ orchestrator.ping()   → fail → send error JSON → return False
       ├─ transcriber._is_ready → fail (audio input only) → send error JSON → return False
       └─ TTSClient.ping(url)   → fail (audio output only) → send warning JSON → continue
       │
  run()
```

---

## Component Changes

### 1. `OrchestratorClient.ping() -> bool`

Uses the already-initialised `httpx.AsyncClient` to hit `GET {base_url}/health`.
`OrchestratorClient.connect()` only creates an in-memory `httpx.AsyncClient` — no
network I/O — so it is infallible; `self._http` is always non-None when `ping()` is
called.

```python
async def ping(self) -> bool:
    try:
        r = await self._http.get(f"{self.base_url}/health", timeout=5.0)
        return r.is_success
    except Exception:
        return False
```

### 2. `TTSClient.ping(url: str) -> bool` (static method)

Converts `ws://host:port/...` → `http://host:port/health` (and `wss://` →
`https://`) and performs a one-shot GET. Static because TTS has no persistent
instance at session-start time.

Empty or malformed URLs (e.g. the triggering `TTS_WS_URL=""`) will cause the
regex/split to produce an invalid URL, which raises inside the `try` block and
returns `False` — the session continues with a TTS warning, which is the correct
degraded behaviour.

```python
@staticmethod
async def ping(url: str) -> bool:
    import re, httpx
    http_base = re.sub(r'^ws', 'http', url.split('?')[0])   # ws→http, wss→https
    http_base = '/'.join(http_base.split('/')[:3])           # scheme+host+port only
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{http_base}/health", timeout=5.0)
            return r.is_success
    except Exception:
        return False
```

### 3. `TranscriberClient` — no new method

`connect()` already opens the WebSocket and waits for `{"type": "ready"}`.
If that succeeds, `_is_ready` is `True`. The bridge reads `self.transcriber._is_ready`
directly.

The primary failure path (transcriber unreachable) is: `connect_internal_services()`
raises → `routes.py` catches it → session closed before `health_check()` is reached.
The `_is_ready` guard inside `health_check()` is **defense-in-depth** for the race
where the transcriber goes down between `connect()` returning and `health_check()`
executing, or for future code paths that bypass the exception.

### 4. `JotaBridge.health_check() -> bool`

`TTSClient` and `settings` are already imported at the top of `bridge.py`.
`settings.TTS_WS_URL` is the canonical URL source — no TTSClient instance is
stored on the bridge at this point.

```python
async def health_check(self) -> bool:
    # Orchestrator — always critical
    if not await self.orchestrator.ping():
        await self.client_ws.send_json({
            "type": "service_status", "service": "orchestrator",
            "status": "unavailable",
            "message": "Orchestrator unavailable, closing session"
        })
        return False

    # Transcriber — critical if audio input
    if self.handshake.input_mode == "audio":
        if not self.transcriber or not self.transcriber._is_ready:
            await self.client_ws.send_json({
                "type": "service_status", "service": "transcriber",
                "status": "unavailable",
                "message": "Transcriber unavailable, closing session"
            })
            return False

    # TTS — non-critical if audio output requested
    if "audio" in self.handshake.output_mode:
        if not await TTSClient.ping(settings.TTS_WS_URL):
            await self.client_ws.send_json({
                "type": "service_status", "service": "tts",
                "status": "unavailable",
                "message": "Audio output unavailable"
            })
            # session continues

    return True
```

### 5. `routes.py` — insert health_check call

Between `connect_internal_services()` and `run()`:

```python
if not await bridge.health_check():
    await websocket.close(code=1011, reason="Servicio crítico no disponible.")
    return
```

---

## Error Handling

- All `ping()` methods catch every exception and return `False` — they never
  raise. The bridge is the only place that decides what to do with the result.
- Timeout per ping: **5 seconds**.
- Pings run **sequentially** (orchestrator → transcriber → tts). Parallel pings
  would complicate short-circuit logic (stop on first critical failure).

---

## Out of Scope

- Reconnection / retry logic during a live session.
- Periodic health polling after session start.
- Exposing a `/health` endpoint on the gateway itself.
