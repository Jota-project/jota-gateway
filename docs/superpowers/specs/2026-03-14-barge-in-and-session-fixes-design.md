# Barge-in and Session Fixes — Design Spec (Spec A)

**Date:** 2026-03-14
**Status:** Approved

## Problem

Five bugs observed in live sessions cause incorrect or fragile behavior:

1. `transcriber_client.py:88` — `listen_loop` fires the callback on every partial transcription, not just finals. Same phrase processed N times → N orchestrator calls + N TTS connections.
2. `bridge.py:147` — `_on_transcribed_text` is `await`ed inline inside `listen_loop`. The transcriber loop is blocked for the entire duration of LLM streaming + TTS, causing audio frames to pile up in the buffer.
3. `bridge.py:129` — `_client_input_loop` does not handle the raw WebSocket disconnect message `{"type":"websocket.disconnect"}`. Causes RuntimeError logged as ERROR instead of clean INFO disconnect.
4. `bridge.py:154` — `_call_orchestrator` is not cancellable. Barge-in is architecturally impossible.
5. `bridge.py:172-180` — `_on_token`, `_on_event`, and `pipe_audio` send to the client WebSocket without guarding against a disconnected client, causing silent exceptions during streaming.

## Goal

- Add **barge-in**: when the user speaks while the assistant is responding, cancel the active turn immediately.
- Fix all five bugs as part of the same architectural change.
- Keep Spec B (session lifecycle robustness, timeouts) out of scope.

---

## Barge-in Behavior

- **Partial transcription with `len(text) >= BARGE_IN_MIN_CHARS`**: if a turn is active, cancel it and send `{"type":"interrupted"}` to the client. Do NOT call the orchestrator.
- **Final transcription (`is_final=True`)**: cancel any active turn, send `{"type":"transcription","text":...}` to the client, start a new orchestrator turn.
- **Partial transcription below threshold**: ignored entirely (noise filter).
- **Final transcription with no active turn**: normal first-turn flow.

`BARGE_IN_MIN_CHARS` defaults to `5`. Configurable via `.env`.

---

## Architecture

### New state on `JotaBridge`

```python
_active_turn: Optional[asyncio.Task] = None  # current orchestrator turn
```

Initialized to `None` in `__init__`. Cancelled and awaited in `close_all()`.

### Session startup sequence — unchanged

The health check, connect, and run sequence is not modified.

### Turn lifecycle

```
Partial event (len >= threshold) + active turn running
    → _cancel_active_turn()
    → send {"type": "interrupted"}
    → return (no orchestrator call)

Final event
    → _cancel_active_turn() (no-op if nothing running)
    → send {"type": "transcription", "text": text}
    → _active_turn = asyncio.create_task(_call_orchestrator(text))

Client disconnect (raw message or WebSocketDisconnect)
    → break _client_input_loop cleanly

Token/audio send while streaming
    → try/except — silent failure if WebSocket already closed
```

---

## Component Changes

### 1. `src/core/config.py` — new setting

```python
BARGE_IN_MIN_CHARS: int = 5
```

### 2. `src/services/transcriber_client.py` — callback signature

Change `listen_loop` callback from `Callable[[str], Awaitable[None]]` to `Callable[[str, bool], Awaitable[None]]`. Pass `(text, bool(t_msg.is_final))` — the loop itself does not interpret `is_final`, it just forwards it.

```python
async def listen_loop(
    self,
    on_transcription_callback: Callable[[str, bool], Awaitable[None]],
):
    if not self.ws:
        return
    try:
        async for message in self.ws:
            try:
                data = json.loads(message)
                t_msg = TranscriberMessage(**data)
                if t_msg.type == "transcription" and t_msg.text:
                    await on_transcription_callback(t_msg.text, bool(t_msg.is_final))
                elif t_msg.type == "error":
                    logger.error(f"[{self.client_id}] Transcriber Runtime Error: {t_msg.message}")
                elif t_msg.type == "warning":
                    pass
            except json.JSONDecodeError:
                logger.warning(f"[{self.client_id}] Transcriber mandó un non-JSON: {message}")
    except ConnectionClosed:
        self._is_ready = False
        logger.info(f"[{self.client_id}] Loop de escucha del Transcriber finalizado.")
```

`transcribe_file` is not modified — it already checks `is_final` correctly.

### 3. `src/services/bridge.py` — five changes

#### 3a. `__init__` — add `_active_turn`

```python
self._active_turn: Optional[asyncio.Task] = None
```

#### 3b. `close_all()` — cancel and await active turn

Add before the existing task cancellation loop. The active turn must be awaited
(with exception suppression) so that the TTS `finally: await tts.close()` block
inside `_call_orchestrator` completes before `gather(*close_aws)` closes the
orchestrator and transcriber clients:

```python
if self._active_turn and not self._active_turn.done():
    self._active_turn.cancel()
    try:
        await self._active_turn
    except (asyncio.CancelledError, Exception):
        pass
```

#### 3c. New method `_cancel_active_turn() -> bool`

```python
async def _cancel_active_turn(self) -> bool:
    """Cancel the active orchestrator turn if one is running. Returns True if cancelled."""
    if self._active_turn and not self._active_turn.done():
        self._active_turn.cancel()
        try:
            await self._active_turn
        except (asyncio.CancelledError, Exception):
            pass
        self._active_turn = None
        return True
    return False
```

#### 3d. Replace `_on_transcribed_text` with `_on_transcription(text, is_final)`

All sends to `client_ws` inside `_on_transcription` are guarded with `try/except`,
consistent with the principle established in section 3f — the client may disconnect
at any point, including between barge-in detection and the send:

```python
async def _on_transcription(self, text: str, is_final: bool):
    """Callback dispatched by TranscriberClient on every transcription event."""
    if not is_final:
        # Barge-in: interrupt active turn if partial is substantial enough
        if len(text) >= settings.BARGE_IN_MIN_CHARS:
            if await self._cancel_active_turn():
                logger.info(f"[{self.client_id}] Barge-in: turno cancelado por parcial '{text[:30]}'")
                try:
                    await self.client_ws.send_json({"type": "interrupted"})
                except Exception:
                    pass
        return  # partials never reach the orchestrator

    # Final: cancel any running turn, notify client, start new turn
    await self._cancel_active_turn()
    logger.info(f"[{self.client_id}] Transcripción final: '{text}'")
    try:
        await self.client_ws.send_json({"type": "transcription", "text": text})
    except Exception:
        return  # client disconnected — no point starting a new turn
    self._active_turn = asyncio.create_task(self._call_orchestrator(text))
```

Note: when the client is disconnected and the `transcription` send fails, `_call_orchestrator`
is not started — there is no receiver for the tokens or audio.

Two coordinated renames are required:
- `listen_loop` parameter: `on_text_callback` → `on_transcription_callback` (in `transcriber_client.py`, section 2 above)
- Call site in `run()` (in `bridge.py`): keyword argument and callback method name both change:

```python
self.transcriber.listen_loop(on_transcription_callback=self._on_transcription)
```

#### 3e. `_client_input_loop` — raw disconnect fix

Add disconnect check at the top of the receive loop:

```python
message = await self.client_ws.receive()

if message.get("type") == "websocket.disconnect":
    logger.info(f"[{self.client_id}] Cliente físico desconectado.")
    break
```

#### 3f. `_call_orchestrator` — send guards

All three closures that send to `client_ws` must be guarded (bug 5 covers
`_on_token`, `_on_event`, and `pipe_audio`):

```python
async def _on_token(token_text: str):
    try:
        if "text" in self.handshake.output_mode:
            await self.client_ws.send_json({"type": "token", "content": token_text})
        if tts:
            await tts.send_text_chunk(token_text)
    except Exception:
        pass  # client disconnected mid-stream

async def _on_event(data: dict):
    try:
        if data.get("type") == "error" or "status" in self.handshake.output_mode:
            await self.client_ws.send_json(data)
    except Exception:
        pass  # client disconnected mid-stream

async def pipe_audio():
    async for chunk in tts.get_audio_stream():
        try:
            await self.client_ws.send_bytes(chunk)
        except Exception:
            return  # client disconnected, abort audio stream
```

---

## Protocol — New Client Message

| Message | When | Session continues? |
|---|---|---|
| `{"type":"interrupted"}` | Barge-in triggered by partial ≥ threshold while a turn was active | Yes |
| `{"type":"transcription","text":"..."}` | Final transcription (unchanged) | Yes |

**Stream termination on final-triggered cancellation:** when a final transcription
cancels an in-progress turn, the client receives **no explicit end-of-stream marker**
for the interrupted token stream. The new `{"type":"transcription","text":"..."}` event
that immediately follows serves as the implicit signal that the previous stream has ended.
Clients must treat any incoming `transcription` event as a stream reset — discarding any
accumulated partial response — rather than expecting a dedicated `done` or `end` message.

---

## Error Handling

- `_cancel_active_turn` catches all exceptions from the awaited task — never raises.
- `_call_orchestrator` tasks propagate `CancelledError` cleanly through the existing `finally: await tts.close()` block.
- Send guards use bare `except Exception: pass/return` — errors are expected (disconnected client) and do not need logging.
- `close_all()` awaits `_active_turn` (with exception suppression) but does **not** await the tasks in `self.tasks` — this is pre-existing intentional behavior. The `self.tasks` tasks (input_loop, listen_loop) are only cancelled; they are left to complete naturally or be garbage-collected. This asymmetry is out of scope for Spec A.

---

## Out of Scope (Spec B)

- Session lifecycle when transcriber dies mid-session (`FIRST_COMPLETED` behavior)
- Orchestrator streaming timeout
- Concurrent calls from `_client_input_loop` (text mode) and `listen_loop` (audio mode) — currently possible but low risk since text-mode clients don't have a transcriber
- `_client_input_loop` calling `_call_orchestrator` directly (text input) should also use `_active_turn` — deferred to Spec B
