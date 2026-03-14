# Barge-in and Session Fixes Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add barge-in (interrupt-on-speech) and fix five session-correctness bugs: duplicate transcription processing, blocking listen_loop, raw disconnect crash, uncancellable turns, and send-after-disconnect exceptions.

**Architecture:** A new `_active_turn: Optional[asyncio.Task]` on `JotaBridge` holds the current orchestrator turn. A new `_cancel_active_turn()` method cancels it cleanly. The transcriber callback is renamed and extended to receive `is_final`; partials above a threshold trigger barge-in, finals start a new turn. All client WebSocket sends are wrapped in try/except.

**Tech Stack:** Python 3.11, asyncio, FastAPI WebSocket, pytest-asyncio (asyncio_mode=auto).

---

## File Map

| Action | Path | Change |
|--------|------|--------|
| Modify | `src/core/config.py` | Add `BARGE_IN_MIN_CHARS: int = 5` |
| Modify | `src/services/transcriber_client.py` | Rename callback param, pass `is_final` |
| Modify | `src/services/bridge.py` | `_active_turn`, `_cancel_active_turn()`, `_on_transcription()`, `close_all()`, `_client_input_loop`, `_call_orchestrator` |
| Create | `tests/unit/test_transcriber_listen_loop.py` | Tests for new callback signature |
| Create | `tests/unit/test_bridge_barge_in.py` | Tests for `_cancel_active_turn` + `_on_transcription` + `close_all` |
| Create | `tests/unit/test_bridge_disconnect.py` | Tests for raw disconnect handling |
| Create | `tests/unit/test_bridge_send_guards.py` | Tests for send guards in `_call_orchestrator` |

---

## Chunk 1: Config and Transcriber

### Task 1: `BARGE_IN_MIN_CHARS` in config

**Files:**
- Modify: `src/core/config.py`

- [ ] **Step 1: Add setting**

In `src/core/config.py`, add inside the `Settings` class:

```python
BARGE_IN_MIN_CHARS: int = 5
```

- [ ] **Step 2: Verify it loads**

```bash
cd /home/sito/jota-gateway && python -c "from src.core.config import settings; print(settings.BARGE_IN_MIN_CHARS)"
```

Expected output: `5`

- [ ] **Step 3: Commit**

```bash
git add src/core/config.py
git commit -m "feat: add BARGE_IN_MIN_CHARS config setting"
```

---

### Task 2: `TranscriberClient.listen_loop` — forward `is_final`

**Files:**
- Modify: `src/services/transcriber_client.py`
- Create: `tests/unit/test_transcriber_listen_loop.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_transcriber_listen_loop.py`:

```python
"""Tests for TranscriberClient.listen_loop callback signature change."""
import json
import pytest
from src.services.transcriber_client import TranscriberClient


@pytest.fixture
def client():
    return TranscriberClient(url="ws://test", client_id="test")


async def make_ws(*messages):
    """Async generator simulating a websocket stream."""
    for m in messages:
        yield m


async def test_listen_loop_passes_text_and_is_final_true(client):
    """Final transcription forwarded as (text, True)."""
    msg = json.dumps({"type": "transcription", "text": "hola", "is_final": True})
    client.ws = make_ws(msg)

    received = []
    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == [("hola", True)]


async def test_listen_loop_passes_text_and_is_final_false(client):
    """Partial transcription forwarded as (text, False)."""
    msg = json.dumps({"type": "transcription", "text": "ho", "is_final": False})
    client.ws = make_ws(msg)

    received = []
    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == [("ho", False)]


async def test_listen_loop_passes_is_final_none_as_false(client):
    """is_final=None (absent) is coerced to False."""
    msg = json.dumps({"type": "transcription", "text": "partial"})  # no is_final key
    client.ws = make_ws(msg)

    received = []
    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == [("partial", False)]


async def test_listen_loop_ignores_non_transcription_messages(client):
    """Error and warning messages do not invoke the callback."""
    msgs = [
        json.dumps({"type": "error", "message": "oops"}),
        json.dumps({"type": "warning", "message": "buffer full"}),
    ]
    client.ws = make_ws(*msgs)

    received = []
    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == []


async def test_listen_loop_ignores_empty_text(client):
    """Transcription with empty text does not invoke the callback."""
    msg = json.dumps({"type": "transcription", "text": "", "is_final": True})
    client.ws = make_ws(msg)

    received = []
    async def callback(text: str, is_final: bool):
        received.append((text, is_final))

    await client.listen_loop(on_transcription_callback=callback)

    assert received == []


async def test_listen_loop_returns_immediately_when_ws_is_none(client):
    """listen_loop exits cleanly if ws is not set."""
    client.ws = None

    called = []
    async def callback(text: str, is_final: bool):
        called.append(True)

    await client.listen_loop(on_transcription_callback=callback)

    assert called == []
```

- [ ] **Step 2: Run tests to confirm they FAIL**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_transcriber_listen_loop.py -v
```

Expected: `TypeError` — `listen_loop()` got unexpected keyword argument `on_transcription_callback`.

- [ ] **Step 3: Implement the change in `transcriber_client.py`**

Replace the existing `listen_loop` method (lines 73-101) with:

```python
async def listen_loop(self, on_transcription_callback: Callable[[str, bool], Awaitable[None]]):
    """
    Bucle que escucha transcripciones del C++ y ejecuta el callback con (text, is_final).
    El loop no interpreta is_final — solo reenvía lo que llega.
    """
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
                    pass  # Warning de buffer full

            except json.JSONDecodeError:
                logger.warning(f"[{self.client_id}] Transcriber mandó un non-JSON: {message}")
    except ConnectionClosed:
        self._is_ready = False
        logger.info(f"[{self.client_id}] Loop de escucha del Transcriber finalizado.")
```

- [ ] **Step 4: Run tests to confirm they PASS**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_transcriber_listen_loop.py -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/transcriber_client.py tests/unit/test_transcriber_listen_loop.py
git commit -m "feat: listen_loop forwards (text, is_final) to callback"
```

---

## Chunk 2: Bridge Core — cancel, barge-in, close_all

### Task 3: `_active_turn` + `_cancel_active_turn()`

**Files:**
- Modify: `src/services/bridge.py`
- Create: `tests/unit/test_bridge_barge_in.py` (shared fixture + Task 3 tests)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_bridge_barge_in.py` with the shared fixture and Task 3 tests:

```python
"""Tests for barge-in: _cancel_active_turn, _on_transcription, close_all."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.models.schemas import Handshake


@pytest.fixture
def make_bridge():
    def _make(input_mode="audio", output_mode=None):
        if output_mode is None:
            output_mode = ["audio", "text", "status"]
        ws = AsyncMock()
        bridge = JotaBridge(client_id="test", client_ws=ws)
        bridge.handshake = Handshake(input_mode=input_mode, output_mode=output_mode)
        bridge.orchestrator = AsyncMock()
        bridge.transcriber = MagicMock()
        bridge.transcriber._is_ready = True
        return bridge
    return _make


# ── _cancel_active_turn ──────────────────────────────────────────────────────

async def test_cancel_active_turn_returns_false_when_no_task(make_bridge):
    bridge = make_bridge()
    assert bridge._active_turn is None

    result = await bridge._cancel_active_turn()

    assert result is False


async def test_cancel_active_turn_returns_false_when_task_already_done(make_bridge):
    bridge = make_bridge()

    async def quick(): pass
    bridge._active_turn = asyncio.create_task(quick())
    await asyncio.sleep(0)  # let task complete naturally
    assert bridge._active_turn.done()

    result = await bridge._cancel_active_turn()

    assert result is False


async def test_cancel_active_turn_cancels_running_task(make_bridge):
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)  # let task start

    result = await bridge._cancel_active_turn()

    assert result is True
    assert bridge._active_turn is None


async def test_cancel_active_turn_clears_active_turn(make_bridge):
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    await bridge._cancel_active_turn()

    assert bridge._active_turn is None
```

- [ ] **Step 2: Run tests to confirm they FAIL**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_barge_in.py::test_cancel_active_turn_returns_false_when_no_task tests/unit/test_bridge_barge_in.py::test_cancel_active_turn_returns_false_when_task_already_done tests/unit/test_bridge_barge_in.py::test_cancel_active_turn_cancels_running_task tests/unit/test_bridge_barge_in.py::test_cancel_active_turn_clears_active_turn -v
```

Expected: `AttributeError` — `_active_turn` and `_cancel_active_turn` do not exist yet.

- [ ] **Step 3: Add `_active_turn` to `__init__` and implement `_cancel_active_turn()`**

In `src/services/bridge.py`:

1. In `__init__`, after `self.tasks: list[asyncio.Task] = []`, add:

```python
self._active_turn: Optional[asyncio.Task] = None
```

2. Add `_cancel_active_turn` method after `health_check()` and before `run()`:

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

- [ ] **Step 4: Run tests to confirm they PASS**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_barge_in.py::test_cancel_active_turn_returns_false_when_no_task tests/unit/test_bridge_barge_in.py::test_cancel_active_turn_returns_false_when_task_already_done tests/unit/test_bridge_barge_in.py::test_cancel_active_turn_cancels_running_task tests/unit/test_bridge_barge_in.py::test_cancel_active_turn_clears_active_turn -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/services/bridge.py tests/unit/test_bridge_barge_in.py
git commit -m "feat: add _active_turn and _cancel_active_turn() to JotaBridge"
```

---

### Task 4: `_on_transcription()` — replace `_on_transcribed_text`

**Files:**
- Modify: `src/services/bridge.py`
- Modify: `tests/unit/test_bridge_barge_in.py` (append tests)

- [ ] **Step 1: Append failing tests to `test_bridge_barge_in.py`**

Add these tests after the `_cancel_active_turn` tests:

```python
# ── _on_transcription ────────────────────────────────────────────────────────

async def test_partial_below_threshold_is_ignored(make_bridge):
    """Partials shorter than BARGE_IN_MIN_CHARS (5) are silently ignored."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hi", False)  # 2 chars

    bridge.client_ws.send_json.assert_not_called()
    assert bridge._active_turn is None


async def test_partial_above_threshold_with_no_active_turn_is_ignored(make_bridge):
    """Partial above threshold but no active turn — no barge-in needed."""
    bridge = make_bridge()

    await bridge._on_transcription("hello world", False)

    bridge.client_ws.send_json.assert_not_called()
    assert bridge._active_turn is None


async def test_partial_above_threshold_with_active_turn_triggers_barge_in(make_bridge):
    """Partial above threshold with active turn → cancel + send interrupted."""
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)

    await bridge._on_transcription("hello world", False)

    bridge.client_ws.send_json.assert_called_once_with({"type": "interrupted"})
    assert bridge._active_turn is None


async def test_partial_does_not_call_orchestrator(make_bridge):
    """Partials — regardless of threshold — never call the orchestrator."""
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hello world", False)

    bridge._call_orchestrator.assert_not_called()


async def test_final_sends_transcription_to_client(make_bridge):
    """Final transcription → send {"type":"transcription"} to client."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hola mundo", True)
    await asyncio.sleep(0)

    bridge.client_ws.send_json.assert_called_once_with(
        {"type": "transcription", "text": "hola mundo"}
    )


async def test_final_starts_new_active_turn(make_bridge):
    """Final transcription starts a new _active_turn task."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()

    await bridge._on_transcription("hola", True)
    await asyncio.sleep(0)

    assert bridge._active_turn is not None
    # cleanup
    bridge._active_turn.cancel()
    try:
        await bridge._active_turn
    except (asyncio.CancelledError, Exception):
        pass


async def test_final_cancels_previous_active_turn(make_bridge):
    """Final transcription cancels any in-progress turn before starting a new one."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()
    old_turn = asyncio.create_task(asyncio.sleep(60))
    bridge._active_turn = old_turn
    await asyncio.sleep(0)

    await bridge._on_transcription("nueva frase", True)
    await asyncio.sleep(0)

    assert old_turn.cancelled()
    assert bridge._active_turn is not None
    assert bridge._active_turn is not old_turn
    bridge._active_turn.cancel()
    try:
        await bridge._active_turn
    except (asyncio.CancelledError, Exception):
        pass


async def test_final_with_disconnected_client_does_not_start_turn(make_bridge):
    """If send_json raises (disconnected client), no new turn is started."""
    bridge = make_bridge()
    bridge._call_orchestrator = AsyncMock()
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))

    await bridge._on_transcription("hola", True)

    assert bridge._active_turn is None


async def test_barge_in_interrupted_send_failure_is_silent(make_bridge):
    """If interrupted send fails, no exception propagates."""
    bridge = make_bridge()
    bridge._active_turn = asyncio.create_task(asyncio.sleep(60))
    await asyncio.sleep(0)
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))

    # Must not raise
    await bridge._on_transcription("hello world", False)
```

- [ ] **Step 2: Run new tests to confirm they FAIL**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_barge_in.py -k "transcription or partial or final or barge_in" -v
```

Expected: `AttributeError` — `_on_transcription` does not exist.

- [ ] **Step 3: Replace `_on_transcribed_text` with `_on_transcription` in `bridge.py`**

Remove the existing `_on_transcribed_text` method entirely and add `_on_transcription` in its place:

```python
async def _on_transcription(self, text: str, is_final: bool):
    """Callback dispatched by TranscriberClient on every transcription event.

    Partials: trigger barge-in if text is substantial and a turn is active.
    Finals: cancel any running turn, notify the client, start a new turn.
    All client_ws sends are guarded — the client may disconnect at any time.
    """
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

Also update `run()` — change the `listen_loop` call from:
```python
self.transcriber.listen_loop(on_text_callback=self._on_transcribed_text)
```
to:
```python
self.transcriber.listen_loop(on_transcription_callback=self._on_transcription)
```

- [ ] **Step 4: Run all barge-in tests to confirm they PASS**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_barge_in.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/bridge.py
git commit -m "feat: add _on_transcription() with barge-in and replace _on_transcribed_text"
```

---

### Task 5: `close_all()` — cancel and await `_active_turn`

**Files:**
- Modify: `src/services/bridge.py`
- Modify: `tests/unit/test_bridge_barge_in.py` (append test)

- [ ] **Step 1: Append failing test**

Add to `test_bridge_barge_in.py`:

```python
# ── close_all ────────────────────────────────────────────────────────────────

async def test_close_all_cancels_and_awaits_active_turn(make_bridge):
    """close_all() cancels _active_turn and waits for cleanup (e.g. tts.close())
    to complete before closing other clients."""
    bridge = make_bridge()

    cleanup_ran = asyncio.Event()

    async def mock_turn():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cleanup_ran.set()
            raise

    bridge._active_turn = asyncio.create_task(mock_turn())
    await asyncio.sleep(0)

    await bridge.close_all()

    assert cleanup_ran.is_set()
```

- [ ] **Step 2: Run test to confirm it FAILS**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_barge_in.py::test_close_all_cancels_and_awaits_active_turn -v
```

Expected: FAIL — cleanup_ran is not set (close_all does not currently await _active_turn).

- [ ] **Step 3: Modify `close_all()` in `bridge.py`**

At the very beginning of `close_all()`, before the existing `for task in self.tasks:` loop, add:

```python
# Cancel and await the active turn first so TTS finally-blocks run before
# orchestrator/transcriber clients are closed.
if self._active_turn and not self._active_turn.done():
    self._active_turn.cancel()
    try:
        await self._active_turn
    except (asyncio.CancelledError, Exception):
        pass
```

- [ ] **Step 4: Run test to confirm it PASSES**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_barge_in.py -v
```

Expected: all 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/bridge.py tests/unit/test_bridge_barge_in.py
git commit -m "feat: close_all() awaits _active_turn before closing other clients"
```

---

## Chunk 3: Disconnect Fix and Send Guards

### Task 6: `_client_input_loop` — raw disconnect fix

**Files:**
- Modify: `src/services/bridge.py`
- Create: `tests/unit/test_bridge_disconnect.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_bridge_disconnect.py`:

```python
"""Tests for _client_input_loop raw disconnect handling."""
import pytest
from unittest.mock import AsyncMock, MagicMock, call
from src.services.bridge import JotaBridge
from src.models.schemas import Handshake


@pytest.fixture
def bridge():
    ws = AsyncMock()
    b = JotaBridge(client_id="test", client_ws=ws)
    b.handshake = Handshake(input_mode="audio", output_mode=["text"])
    b.transcriber = MagicMock()
    b.transcriber._is_ready = True
    return b


async def test_raw_disconnect_message_exits_loop_cleanly(bridge):
    """{"type":"websocket.disconnect"} must break the loop without raising."""
    bridge.client_ws.receive = AsyncMock(
        return_value={"type": "websocket.disconnect"}
    )

    # Must complete without exception
    await bridge._client_input_loop()

    # Receive was called once — loop exited on first message
    bridge.client_ws.receive.assert_called_once()


async def test_raw_disconnect_does_not_log_error(bridge, caplog):
    """Raw disconnect must not produce an ERROR log entry."""
    import logging
    bridge.client_ws.receive = AsyncMock(
        return_value={"type": "websocket.disconnect"}
    )

    with caplog.at_level(logging.ERROR):
        await bridge._client_input_loop()

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records == []


async def test_audio_message_is_still_processed(bridge):
    """bytes messages are still forwarded to the transcriber after the fix."""
    audio = b"\x00\x01\x02"
    bridge.transcriber.send_audio = AsyncMock()
    bridge.client_ws.receive = AsyncMock(side_effect=[
        {"bytes": audio},
        {"type": "websocket.disconnect"},
    ])

    await bridge._client_input_loop()

    bridge.transcriber.send_audio.assert_called_once_with(audio)
```

- [ ] **Step 2: Run tests to confirm they FAIL**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_disconnect.py -v
```

Expected: `test_raw_disconnect_message_exits_loop_cleanly` will hang or error — the loop does not break on the disconnect message.

- [ ] **Step 3: Add disconnect check in `_client_input_loop`**

In `src/services/bridge.py`, inside `_client_input_loop`, immediately after `message = await self.client_ws.receive()`, add:

```python
if message.get("type") == "websocket.disconnect":
    logger.info(f"[{self.client_id}] Cliente físico desconectado.")
    break
```

The loop body should now look like:

```python
while True:
    message = await self.client_ws.receive()

    if message.get("type") == "websocket.disconnect":
        logger.info(f"[{self.client_id}] Cliente físico desconectado.")
        break

    if "bytes" in message:
        ...
    elif "text" in message:
        ...
```

- [ ] **Step 4: Run tests to confirm they PASS**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_disconnect.py -v
```

Expected: 3 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/services/bridge.py tests/unit/test_bridge_disconnect.py
git commit -m "fix: handle raw websocket.disconnect in _client_input_loop"
```

---

### Task 7: `_call_orchestrator` — send guards

**Files:**
- Modify: `src/services/bridge.py`
- Create: `tests/unit/test_bridge_send_guards.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_bridge_send_guards.py`:

```python
"""Tests for send guards in _call_orchestrator closures."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.bridge import JotaBridge
from src.models.schemas import Handshake


@pytest.fixture
def bridge():
    ws = AsyncMock()
    b = JotaBridge(client_id="test", client_ws=ws)
    b.handshake = Handshake(input_mode="text", output_mode=["text", "status"])
    b.orchestrator = AsyncMock()
    b.transcriber = None
    return b


def make_listen_loop_with_token(token: str):
    """Return an orchestrator.listen_loop side_effect that yields one token."""
    async def _listen(text, on_token, on_event, **kwargs):
        await on_token(token)
    return _listen


def make_listen_loop_with_event(event: dict):
    """Return an orchestrator.listen_loop side_effect that yields one event."""
    async def _listen(text, on_token, on_event, **kwargs):
        await on_event(event)
    return _listen


async def test_token_send_failure_does_not_propagate(bridge):
    """send_json raising inside _on_token must not crash _call_orchestrator."""
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))
    bridge.orchestrator.listen_loop = make_listen_loop_with_token("hello")

    # Must not raise
    await bridge._call_orchestrator("test")


async def test_event_send_failure_does_not_propagate(bridge):
    """send_json raising inside _on_event must not crash _call_orchestrator."""
    bridge.client_ws.send_json = AsyncMock(side_effect=RuntimeError("disconnected"))
    bridge.orchestrator.listen_loop = make_listen_loop_with_event(
        {"type": "status", "content": "thinking"}
    )

    # Must not raise
    await bridge._call_orchestrator("test")


async def test_audio_send_failure_does_not_propagate():
    """send_bytes raising inside pipe_audio must not crash _call_orchestrator."""
    ws = AsyncMock()
    ws.send_bytes = AsyncMock(side_effect=RuntimeError("disconnected"))
    b = JotaBridge(client_id="test", client_ws=ws)
    b.handshake = Handshake(input_mode="audio", output_mode=["audio", "text"])
    b.orchestrator = AsyncMock()

    # Orchestrator produces one token, TTS returns one audio chunk
    async def listen_with_token(text, on_token, on_event, **kwargs):
        await on_token("hi")

    b.orchestrator.listen_loop = listen_with_token

    import src.services.bridge as bridge_module
    original = bridge_module.TTSClient

    class FakeTTS:
        def __init__(self, **kwargs): pass
        async def connect(self): pass
        async def send_text_chunk(self, t): pass
        async def end(self): pass
        async def close(self): pass
        async def get_audio_stream(self):
            yield b"\xff\xfe"

    bridge_module.TTSClient = FakeTTS
    try:
        await b._call_orchestrator("test")  # must not raise
    finally:
        bridge_module.TTSClient = original


```

- [ ] **Step 2: Run tests to confirm they FAIL**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_bridge_send_guards.py::test_token_send_failure_does_not_propagate tests/unit/test_bridge_send_guards.py::test_event_send_failure_does_not_propagate -v
```

Expected: tests FAIL — exceptions propagate (no guard yet).

- [ ] **Step 3: Add send guards in `_call_orchestrator`**

In `src/services/bridge.py`, inside `_call_orchestrator`, replace the three closures with guarded versions:

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

- [ ] **Step 4: Run all tests to confirm they PASS**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/ -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/services/bridge.py tests/unit/test_bridge_send_guards.py
git commit -m "fix: guard all client_ws sends in _call_orchestrator against disconnect"
```

---

## Final verification

- [ ] **Run complete test suite**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/ -v
```

Expected: all tests PASS, no warnings about missing fixtures.

- [ ] **Verify docker container picks up changes**

```bash
docker compose up --build jota_gateway
```

Connect a client and verify in logs:
- Health check runs at session start (existing behavior)
- Partial transcriptions below threshold produce no log entries
- Partial above threshold with active turn → `Barge-in: turno cancelado` log + `{"type":"interrupted"}` to client
- Final transcription → `Transcripción final` log + `{"type":"transcription"}` to client
- `RuntimeError: Cannot call "receive"` no longer appears on disconnect
