# TTSClient jota-speaker Integration Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the obsolete TTSClient with a new implementation that speaks the jota-speaker WebSocket protocol, and refactor JotaBridge to use a per-request TTS lifecycle.

**Architecture:** New TTSClient handles auth, structured JSON messages, and binary/JSON frame multiplexing. JotaBridge creates a fresh TTSClient per `_call_orchestrator` invocation and runs token-sending and audio-receiving concurrently via `asyncio.gather`. No persistent TTS task in the bridge.

**Tech Stack:** Python 3.11+, `websockets>=11`, `pytest` with `asyncio_mode=auto`, `unittest.mock`

> **Note:** jota-gateway is not a git repository — commit steps are omitted.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/services/tts_client.py` | Rewrite | jota-speaker WS protocol: auth, send tokens, stream audio |
| `src/core/config.py` | Modify | Add `TTS_TOKEN`, update `TTS_WS_URL` default |
| `src/services/bridge.py` | Modify (surgical) | Per-request TTS lifecycle in `_call_orchestrator` |
| `tests/unit/__init__.py` | Create | Empty init for unit test package |
| `tests/unit/test_tts_client.py` | Create | Unit tests for TTSClient |

---

## Chunk 1: TTSClient

### Task 1: Create unit test file (failing)

**Files:**
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_tts_client.py`

- [ ] **Step 1: Create the unit test package init**

```bash
touch tests/unit/__init__.py
```

- [ ] **Step 2: Write the full test file**

Create `tests/unit/test_tts_client.py`:

```python
import json
import pytest
from unittest.mock import AsyncMock, patch

from src.services.tts_client import TTSClient


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_ws(recv_msg: str = '{"type": "auth_ok"}', closed: bool = False, stream_msgs=None):
    """
    Build a minimal mock WebSocket.

    - recv()      → returns recv_msg (used by connect() for auth)
    - send()      → AsyncMock, records calls
    - close()     → AsyncMock
    - closed      → bool property
    - async iter  → yields stream_msgs one by one
    """
    msgs = stream_msgs or []

    class _WS:
        def __init__(self):
            self.closed = closed
            self.send = AsyncMock()
            self.close = AsyncMock()
            self.recv = AsyncMock(return_value=recv_msg)

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for m in msgs:
                yield m

    return _WS()


def _patch_connect(ws):
    """Patch websockets.connect to return `ws` when awaited."""
    return patch("src.services.tts_client.websockets.connect", new=AsyncMock(return_value=ws))


# ── connect() ────────────────────────────────────────────────────────────────

async def test_connect_happy_path():
    ws = make_ws('{"type": "auth_ok"}')
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()

    ws.send.assert_called_once_with(json.dumps({"type": "auth", "token": "gateway"}))
    assert client.ws is ws


async def test_connect_auth_error_raises():
    ws = make_ws('{"type": "auth_error", "reason": "Invalid token"}')
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="bad", client_id="t1")
        with pytest.raises(RuntimeError):
            await client.connect()


async def test_connect_unexpected_message_raises():
    ws = make_ws('{"type": "done"}')
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        with pytest.raises(RuntimeError):
            await client.connect()


async def test_connect_connection_closed_raises():
    from websockets.exceptions import ConnectionClosed
    from websockets.frames import Close

    ws = make_ws()
    ws.recv = AsyncMock(side_effect=ConnectionClosed(Close(1008, ""), None))
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        with pytest.raises(RuntimeError):
            await client.connect()


# ── send_text_chunk() ─────────────────────────────────────────────────────────

async def test_send_text_chunk_sends_token_json():
    ws = make_ws()
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        ws.send.reset_mock()
        await client.send_text_chunk("Hello")

    ws.send.assert_called_once_with(json.dumps({"type": "token", "text": "Hello"}))


async def test_send_text_chunk_noop_when_ws_closed():
    ws = make_ws()
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        ws.send.reset_mock()
        ws.closed = True
        await client.send_text_chunk("Hello")

    ws.send.assert_not_called()


async def test_send_text_chunk_noop_when_ws_none():
    client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
    # ws is None, must not raise
    await client.send_text_chunk("Hello")


# ── end() ─────────────────────────────────────────────────────────────────────

async def test_end_sends_end_json():
    ws = make_ws()
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        ws.send.reset_mock()
        await client.end()

    ws.send.assert_called_once_with(json.dumps({"type": "end"}))


async def test_end_noop_when_ws_closed():
    ws = make_ws()
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        ws.send.reset_mock()
        ws.closed = True
        await client.end()

    ws.send.assert_not_called()


# ── get_audio_stream() ────────────────────────────────────────────────────────

async def test_get_audio_stream_yields_binary_frames():
    audio = b'\x00\x01\x02\x03'
    stream = [
        '{"type": "audio_start", "chunk_id": 0, "sample_rate": 24000, "channels": 1, "encoding": "pcm16"}',
        audio,
        '{"type": "audio_end", "chunk_id": 0}',
        '{"type": "done"}',
    ]
    ws = make_ws(stream_msgs=stream)
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        chunks = [c async for c in client.get_audio_stream()]

    assert chunks == [audio]


async def test_get_audio_stream_stops_at_done():
    stream = [
        b'\xaa\xbb',
        '{"type": "done"}',
        b'\xff\xff',  # must NOT be yielded
    ]
    ws = make_ws(stream_msgs=stream)
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        chunks = [c async for c in client.get_audio_stream()]

    assert chunks == [b'\xaa\xbb']


async def test_get_audio_stream_stops_at_error():
    stream = [
        b'\x01\x02',
        '{"type": "error", "code": "session_timeout", "message": "timed out"}',
        b'\x03\x04',  # must NOT be yielded
    ]
    ws = make_ws(stream_msgs=stream)
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        chunks = [c async for c in client.get_audio_stream()]

    assert chunks == [b'\x01\x02']


async def test_get_audio_stream_stops_at_connection_closed():
    from websockets.exceptions import ConnectionClosed
    from websockets.frames import Close

    ws = make_ws()

    async def _gen():
        yield b'\x10\x20'
        raise ConnectionClosed(Close(1000, ""), None)

    ws.__aiter__ = lambda self: _gen()

    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        chunks = [c async for c in client.get_audio_stream()]

    assert chunks == [b'\x10\x20']


async def test_get_audio_stream_noop_when_ws_none():
    client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
    chunks = [c async for c in client.get_audio_stream()]
    assert chunks == []


# ── close() ───────────────────────────────────────────────────────────────────

async def test_close_sends_code_1000():
    ws = make_ws()
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        await client.close()

    ws.close.assert_called_once_with(1000)


async def test_close_noop_when_ws_none():
    client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
    # Must not raise
    await client.close()


async def test_close_noop_when_already_closed():
    ws = make_ws()
    ws.closed = True
    with _patch_connect(ws):
        client = TTSClient(url="ws://localhost:8005/ws", token="gateway", client_id="t1")
        await client.connect()
        await client.close()

    ws.close.assert_not_called()
```

- [ ] **Step 3: Run the tests to confirm they all fail**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_tts_client.py -v
```

Expected: errors like `ImportError` or `TypeError` (TTSClient has the wrong interface).

---

### Task 2: Implement TTSClient

**Files:**
- Modify: `src/services/tts_client.py`

- [ ] **Step 1: Replace the entire file with the new implementation**

```python
import json
import logging
from typing import AsyncGenerator, Optional

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)


class TTSClient:
    """
    Client for jota-speaker TTS service.

    One instance = one WebSocket session (auth → tokens → end → audio → done).
    Create a fresh instance per _call_orchestrator invocation.
    """

    def __init__(self, url: str, token: str, client_id: str) -> None:
        self.url = url
        self.token = token
        self.client_id = client_id
        self.ws: Optional[websockets.WebSocketClientProtocol] = None

    async def connect(self) -> None:
        """Open WS, authenticate. Raises RuntimeError on auth failure."""
        self.ws = await websockets.connect(self.url)
        await self.ws.send(json.dumps({"type": "auth", "token": self.token}))
        try:
            raw = await self.ws.recv()
        except ConnectionClosed as exc:
            raise RuntimeError(
                f"[{self.client_id}] TTS connection closed during auth: {exc}"
            ) from exc

        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(
                f"[{self.client_id}] TTS sent non-JSON during auth: {raw!r}"
            ) from exc

        if msg.get("type") != "auth_ok":
            raise RuntimeError(
                f"[{self.client_id}] TTS auth failed: {msg}"
            )
        logger.info("[%s] Connected to TTS at %s", self.client_id, self.url)

    async def send_text_chunk(self, text: str) -> None:
        """Send one LLM token to TTS. No-op if WS is closed."""
        if not self.ws or self.ws.closed:
            logger.warning("[%s] send_text_chunk: WS not available, skipping", self.client_id)
            return
        try:
            await self.ws.send(json.dumps({"type": "token", "text": text}))
        except ConnectionClosed:
            logger.warning("[%s] send_text_chunk: ConnectionClosed", self.client_id)

    async def end(self) -> None:
        """Signal no more tokens. No-op if WS is closed."""
        if not self.ws or self.ws.closed:
            logger.warning("[%s] end: WS not available, skipping", self.client_id)
            return
        try:
            await self.ws.send(json.dumps({"type": "end"}))
        except ConnectionClosed:
            logger.warning("[%s] end: ConnectionClosed", self.client_id)

    async def get_audio_stream(self) -> AsyncGenerator[bytes, None]:
        """
        Async generator that yields binary PCM16 audio frames.

        Filters out JSON control messages (audio_start, audio_end).
        Stops on 'done', 'error', or ConnectionClosed.
        """
        if not self.ws:
            return
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    yield msg
                else:
                    try:
                        data = json.loads(msg)
                    except json.JSONDecodeError:
                        logger.warning(
                            "[%s] TTS sent unparseable text frame: %r",
                            self.client_id, msg,
                        )
                        continue

                    msg_type = data.get("type")
                    if msg_type == "done":
                        break
                    elif msg_type == "error":
                        logger.warning(
                            "[%s] TTS error: %s — %s",
                            self.client_id,
                            data.get("code"),
                            data.get("message"),
                        )
                        break
                    else:
                        logger.debug("[%s] TTS control frame: %s", self.client_id, msg_type)
        except ConnectionClosed:
            logger.info("[%s] TTS audio stream ended (ConnectionClosed)", self.client_id)

    async def close(self) -> None:
        """Close the WebSocket with code 1000. No-op if ws is None or already closed."""
        if self.ws is None:
            return
        if not self.ws.closed:
            await self.ws.close(1000)
```

- [ ] **Step 2: Run all unit tests**

```bash
cd /home/sito/jota-gateway && python -m pytest tests/unit/test_tts_client.py -v
```

Expected: all tests pass, 0 failures.

---

## Chunk 2: Config and Bridge

### Task 3: Update config.py

**Files:**
- Modify: `src/core/config.py`

- [ ] **Step 1: Add `TTS_TOKEN` and update `TTS_WS_URL` default**

Open `src/core/config.py`. Replace:

```python
    TTS_WS_URL: str = "ws://localhost:8001/synthesize"
```

With:

```python
    TTS_WS_URL: str = "ws://localhost:8005/ws"
    TTS_TOKEN: str = "gateway"
```

The full file should look like:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # URL base HTTP del JotaOrchestrator (sin trailing slash)
    # El cliente usará POST {ORCHESTRATOR_BASE_URL}/api/quick
    ORCHESTRATOR_BASE_URL: str = "http://localhost:8000"
    # Clave de cliente QUICK registrada en JotaDB para este gateway
    ORCHESTRATOR_API_KEY: str = "jota_internal_default_key"

    TRANSCRIBER_WS_URL: str = "ws://localhost:9000"
    TTS_WS_URL: str = "ws://localhost:8005/ws"
    TTS_TOKEN: str = "gateway"

    class Config:
        env_file = ".env"

settings = Settings()
```

- [ ] **Step 2: Verify the settings load without errors**

```bash
cd /home/sito/jota-gateway && python -c "from src.core.config import settings; print(settings.TTS_WS_URL, settings.TTS_TOKEN)"
```

Expected output:
```
ws://localhost:8005/ws gateway
```

---

### Task 4: Update bridge.py

**Files:**
- Modify: `src/services/bridge.py`

These are surgical edits. Apply them in order.

- [ ] **Step 1: Remove `self.tts` from `__init__`**

In `__init__`, remove this line:
```python
        self.tts: Optional[TTSClient] = None
```

- [ ] **Step 2: Remove the TTS block from `connect_internal_services`**

Remove these lines from `connect_internal_services`:
```python
        # 3. TTS (solo si el dispositivo pidió audio de salida)
        if "audio" in self.handshake.output_mode:
            self.tts = TTSClient(url=settings.TTS_WS_URL, client_id=self.client_id)
            connect_tasks.append(self.tts.connect())
```

- [ ] **Step 3: Remove TTS teardown from `close_all`**

Remove this line from `close_all`:
```python
        if self.tts: close_aws.append(self.tts.close())
```

- [ ] **Step 4: Remove the `_tts_to_client_loop` task from `run`**

Remove these lines from `run`:
```python
        # Loop TTS → cliente (solo si hay audio de salida)
        if self.tts:
            self.tasks.append(asyncio.create_task(self._tts_to_client_loop()))
```

- [ ] **Step 5: Remove the `_tts_to_client_loop` method**

Delete the entire method:
```python
    async def _tts_to_client_loop(self):
        """Obtiene bytes del TTS y los envía al cliente."""
        if not self.tts: return
        try:
            async for chunk in self.tts.get_audio_stream():
                await self.client_ws.send_bytes(chunk)
        except Exception as e:
            logger.error(f"[{self.client_id}] Error enviando audio TTS: {e}")
```

- [ ] **Step 6: Replace `_call_orchestrator` with the new implementation**

Replace the entire `_call_orchestrator` method:

```python
    async def _call_orchestrator(self, text: str):
        """
        Sends text to the Orchestrator and dispatches tokens/events.

        If the client requested audio output, concurrently pipes tokens to
        jota-speaker and streams audio back to the client.
        """
        needs_audio = "audio" in self.handshake.output_mode

        tts: Optional[TTSClient] = None
        if needs_audio:
            tts = TTSClient(
                url=settings.TTS_WS_URL,
                token=settings.TTS_TOKEN,
                client_id=self.client_id,
            )
            await tts.connect()

        async def _on_token(token_text: str):
            if "text" in self.handshake.output_mode:
                await self.client_ws.send_json({"type": "token", "content": token_text})
            if tts:
                await tts.send_text_chunk(token_text)

        async def _on_event(data: dict):
            if data.get("type") == "error" or "status" in self.handshake.output_mode:
                await self.client_ws.send_json(data)

        async def pipe_tokens():
            await self.orchestrator.listen_loop(
                text=text,
                on_token=_on_token,
                on_event=_on_event,
            )
            if tts:
                await tts.end()

        async def pipe_audio():
            async for chunk in tts.get_audio_stream():
                await self.client_ws.send_bytes(chunk)

        if tts:
            try:
                await asyncio.gather(pipe_tokens(), pipe_audio())
            finally:
                await tts.close()
        else:
            await pipe_tokens()
```

- [ ] **Step 7: Verify the import of `TTSClient` is still present and `Optional` is imported**

Check top of `bridge.py` has:
```python
from src.services.tts_client import TTSClient
```
And `Optional` in the typing import. If `TTSClient` is no longer used in `__init__` but IS used in `_call_orchestrator`, confirm the import remains.

- [ ] **Step 8: Verify the module imports cleanly**

```bash
cd /home/sito/jota-gateway && python -c "from src.services.bridge import JotaBridge; print('OK')"
```

Expected:
```
OK
```

- [ ] **Step 9: Run the full test suite**

```bash
cd /home/sito/jota-gateway && python -m pytest -v
```

Expected: all tests pass.
