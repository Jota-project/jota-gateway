# OpenClaw Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded Jota orchestrator with an abstraction layer and implement OpenClawClient (WebSocket, protocol v4) as the default backend, then expose a `/v1/chat/completions` OpenAI-compatible endpoint.

**Architecture:** A `OrchestratorProtocol` (typing.Protocol) defines the interface; `OpenClawClient` implements it via WebSocket to OpenClaw `:18789`. An `OrchestratorRegistry` manages client lifecycle and is stored in `app.state`. `JotaBridge` receives the client via constructor injection. A new `/v1/` router converts HTTP OpenAI-format requests to orchestrator calls.

**Tech Stack:** Python 3.12, FastAPI, `websockets>=11.0.3` (already in requirements), `pytest-asyncio`, `unittest.mock`

**Spec:** `docs/superpowers/specs/2026-05-20-orchestrator-abstraction-design.md`

---

## File Map

| Action | Path |
|--------|------|
| Create | `src/services/orchestrators/__init__.py` |
| Create | `src/services/orchestrators/protocol.py` |
| Create | `src/services/orchestrators/registry.py` |
| Create | `src/services/orchestrators/openclaw_client.py` |
| Create | `src/api/openai_routes.py` |
| Modify | `src/core/config.py` |
| Modify | `src/services/bridge.py` |
| Modify | `src/api/routes.py` |
| Modify | `src/main.py` |
| Modify | `tests/integration/conftest.py` |
| Create | `tests/integration/test_orchestrator_registry.py` |
| Create | `tests/integration/test_openclaw_client.py` |
| Create | `tests/integration/test_rest_openai.py` |
| Archive | `src/services/orchestrator_client.py` → git branch only |

---

## Task 1: Git Setup — Legacy Branch

- [ ] **Step 1: Create and push legacy branch preserving Jota client**

```bash
git checkout -b legacy/jota-orchestrator
git push -u origin legacy/jota-orchestrator
git checkout main
```

- [ ] **Step 2: Create feature branch**

```bash
git checkout -b feat/openclaw-orchestrator
```

- [ ] **Step 3: Verify current tests pass on the feature branch**

```bash
PYTHONPATH=. pytest tests/integration/ -v
```

Expected: all tests pass (baseline established).

---

## Task 2: OrchestratorEvent + OrchestratorProtocol

**Files:**
- Create: `src/services/orchestrators/__init__.py`
- Create: `src/services/orchestrators/protocol.py`

- [ ] **Step 1: Create the package**

```bash
mkdir -p src/services/orchestrators
touch src/services/orchestrators/__init__.py
```

- [ ] **Step 2: Write `protocol.py`**

```python
# src/services/orchestrators/protocol.py
from dataclasses import dataclass, field
from typing import Literal, AsyncIterator, Optional, Protocol, runtime_checkable


@dataclass
class OrchestratorEvent:
    type: Literal["token", "status", "error"]
    content: str = ""


@runtime_checkable
class OrchestratorProtocol(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]: ...
```

- [ ] **Step 3: Verify syntax**

```bash
python -c "from src.services.orchestrators.protocol import OrchestratorEvent, OrchestratorProtocol; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/services/orchestrators/
git commit -m "feat(orchestrators): add OrchestratorEvent and OrchestratorProtocol"
```

---

## Task 3: OrchestratorRegistry

**Files:**
- Create: `src/services/orchestrators/registry.py`

- [ ] **Step 1: Write `registry.py`**

```python
# src/services/orchestrators/registry.py
import logging
from src.services.orchestrators.protocol import OrchestratorProtocol

logger = logging.getLogger(__name__)


class OrchestratorRegistry:
    def __init__(self, clients: dict[str, OrchestratorProtocol]):
        self._clients = clients

    async def connect_all(self) -> None:
        for name, client in self._clients.items():
            try:
                await client.connect()
                logger.info(f"Orchestrator '{name}' connected.")
            except Exception as e:
                logger.error(f"Orchestrator '{name}' failed to connect: {e}")
                raise

    async def close_all(self) -> None:
        for name, client in self._clients.items():
            try:
                await client.close()
                logger.info(f"Orchestrator '{name}' closed.")
            except Exception as e:
                logger.warning(f"Orchestrator '{name}' error on close: {e}")

    def get(self, name: str) -> OrchestratorProtocol:
        if name not in self._clients:
            available = list(self._clients)
            raise KeyError(f"Orchestrator '{name}' not registered. Available: {available}")
        return self._clients[name]

    def default(self) -> OrchestratorProtocol:
        from src.core.config import settings
        return self.get(settings.DEFAULT_ORCHESTRATOR)


def build_registry() -> OrchestratorRegistry:
    from src.core.config import settings
    from src.services.orchestrators.openclaw_client import OpenClawClient

    clients: dict[str, OrchestratorProtocol] = {}

    if settings.OPENCLAW_TOKEN:
        clients["openclaw"] = OpenClawClient(
            host="127.0.0.1",
            port=settings.OPENCLAW_PORT,
            token=settings.OPENCLAW_TOKEN,
            session_key="jota-gateway-default",
        )
        logger.info("OpenClawClient registered.")
    else:
        logger.warning("OPENCLAW_TOKEN not set — openclaw orchestrator not registered.")

    return OrchestratorRegistry(clients)
```

- [ ] **Step 2: Write the registry test**

```python
# tests/integration/test_orchestrator_registry.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.orchestrators.registry import OrchestratorRegistry
from src.services.orchestrators.protocol import OrchestratorProtocol


def make_mock_client(name: str):
    client = MagicMock(spec=OrchestratorProtocol)
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    return client


@pytest.mark.asyncio
async def test_get_returns_registered_client():
    mock = make_mock_client("openclaw")
    registry = OrchestratorRegistry({"openclaw": mock})
    assert registry.get("openclaw") is mock


@pytest.mark.asyncio
async def test_get_raises_for_unknown():
    registry = OrchestratorRegistry({})
    with pytest.raises(KeyError, match="not registered"):
        registry.get("unknown")


@pytest.mark.asyncio
async def test_connect_all_calls_connect_on_each():
    a = make_mock_client("a")
    b = make_mock_client("b")
    registry = OrchestratorRegistry({"a": a, "b": b})
    await registry.connect_all()
    a.connect.assert_awaited_once()
    b.connect.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_all_calls_close_on_each():
    a = make_mock_client("a")
    b = make_mock_client("b")
    registry = OrchestratorRegistry({"a": a, "b": b})
    await registry.close_all()
    a.close.assert_awaited_once()
    b.close.assert_awaited_once()
```

- [ ] **Step 3: Run tests (they will fail until openclaw_client.py exists)**

```bash
PYTHONPATH=. pytest tests/integration/test_orchestrator_registry.py -v
```

Expected: ImportError or ModuleNotFoundError (openclaw_client not yet created — that's fine).

- [ ] **Step 4: Commit**

```bash
git add src/services/orchestrators/registry.py tests/integration/test_orchestrator_registry.py
git commit -m "feat(orchestrators): add OrchestratorRegistry and build_registry"
```

---

## Task 4: Config — Add OpenClaw Settings

**Files:**
- Modify: `src/core/config.py`

- [ ] **Step 1: Add the new settings to `config.py`**

Open `src/core/config.py` and add three new fields inside `Settings`:

```python
# src/core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # jota-db (fuente de verdad de identidad y configuración)
    JOTA_DB_BASE_URL: str = "localhost:8001"
    JOTA_DB_API_KEY: str = ""

    # URL base del JotaOrchestrator (legacy — kept for reference)
    ORCHESTRATOR_BASE_URL: str = "localhost:8000"
    GATEWAY_KEY: str = ""

    # Transcriber (jota-transcriber)
    TRANSCRIBER_WS_URL: str = "localhost:9000"

    # TTS (jota-speaker)
    TTS_WS_URL: str = "localhost:8005"
    TTS_TOKEN: str = "gateway"

    # Orchestrator selection
    DEFAULT_ORCHESTRATOR: str = "openclaw"

    # OpenClaw orchestrator
    OPENCLAW_PORT: int = 18789
    OPENCLAW_TOKEN: str = ""

    BARGE_IN_MIN_CHARS: int = 5
    TRANSCRIBER_SILENCE_TIMEOUT_S: int = 25

    class Config:
        env_file = ".env"

settings = Settings()
```

- [ ] **Step 2: Add vars to `.env`**

Append to `.env`:

```bash
DEFAULT_ORCHESTRATOR=openclaw
OPENCLAW_PORT=18789
OPENCLAW_TOKEN=<paste value from: cat ~/.openclaw/openclaw.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['gateway']['auth']['token'])">
```

- [ ] **Step 3: Verify settings load**

```bash
python -c "from src.core.config import settings; print(settings.DEFAULT_ORCHESTRATOR, settings.OPENCLAW_PORT)"
```

Expected: `openclaw 18789`

- [ ] **Step 4: Commit**

```bash
git add src/core/config.py
git commit -m "feat(config): add DEFAULT_ORCHESTRATOR, OPENCLAW_PORT, OPENCLAW_TOKEN"
```

---

## Task 5: Update JotaBridge — Inject Orchestrator

**Files:**
- Modify: `src/services/bridge.py`

The bridge receives an `OrchestratorProtocol` instance via constructor. It no longer creates or closes the orchestrator — the registry handles that.

- [ ] **Step 1: Update the constructor signature and remove orchestrator lifecycle from bridge**

In `src/services/bridge.py`, make these changes:

**Change the import block** (add OrchestratorProtocol, remove OrchestratorClient):
```python
# REMOVE this import:
# from src.services.orchestrator_client import OrchestratorClient

# ADD this import:
from src.services.orchestrators.protocol import OrchestratorProtocol, OrchestratorEvent
```

**Change `__init__`** (add `orchestrator` parameter):
```python
def __init__(self, client: Client, config: ClientConfig, client_ws: WebSocket, orchestrator: OrchestratorProtocol):
    self.client = client
    self.config = config
    self.client_id = client.id
    self.client_ws = client_ws
    self.handshake: Optional[Handshake] = None
    self.orchestrator: OrchestratorProtocol = orchestrator   # injected, not created here
    self.transcriber: Optional[TranscriberClient] = None
    self.tasks: list[asyncio.Task] = []
    self._active_turn: Optional[asyncio.Task] = None
    self._session_start: float = 0.0
    self._first_audio_at: Optional[float] = None
    self._last_final_text: Optional[str] = None
```

**Change `connect_internal_services`** (remove orchestrator creation/connect):
```python
async def connect_internal_services(self):
    """Inicializa clientes de microservicios dependiendo del handshake."""
    connect_tasks = []

    # Transcriber (solo si el dispositivo mandará audio)
    if self.handshake.input_mode == "audio":
        self.transcriber = TranscriberClient(
            url=settings.TRANSCRIBER_WS_URL,
            client_id=self.client_id
        )
        connect_tasks.append(self.transcriber.connect(
            language=self.config.stt_language,
            token=self.client.client_key,
            vad_thold=self.config.stt_vad_thold,
        ))

    if connect_tasks:
        await asyncio.gather(*connect_tasks)
```

**Change `close_all`** (remove orchestrator close):
```python
async def close_all(self):
    if self._active_turn and not self._active_turn.done():
        try:
            await self._active_turn
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[{self.client_id}] _active_turn falló: {e}")

    for task in self.tasks:
        if not task.done():
            task.cancel()

    close_aws = []
    if self.transcriber:
        close_aws.append(self.transcriber.close())

    if close_aws:
        await asyncio.gather(*close_aws, return_exceptions=True)

    logger.info(f"[{self.client_id}] Puente asíncrono cerrado.")
```

**Change `health_check`** (ping via protocol interface):
```python
async def health_check(self) -> bool:
    # Orchestrator — always critical
    if not await self.orchestrator.ping():
        await self.client_ws.send_json({
            "type": "service_status",
            "service": "orchestrator",
            "status": "unavailable",
            "message": "Orchestrator unavailable, closing session",
        })
        return False

    # Transcriber — critical only for audio input
    if self.handshake.input_mode == "audio":
        if not self.transcriber or not self.transcriber._is_ready:
            await self.client_ws.send_json({
                "type": "service_status",
                "service": "transcriber",
                "status": "unavailable",
                "message": "Transcriber unavailable, closing session",
            })
            return False

    # TTS — non-critical
    if "audio" in self.handshake.output_mode:
        if not await TTSClient.ping(settings.TTS_WS_URL):
            await self.client_ws.send_json({
                "type": "service_status",
                "service": "tts",
                "status": "unavailable",
                "message": "Audio output unavailable",
            })

    return True
```

**Change `_call_orchestrator`** (iterate `stream_response` directly, no `listen_loop`):
```python
async def _call_orchestrator(self, text: str):
    needs_audio = "audio" in self.handshake.output_mode

    tts: Optional[TTSClient] = None
    if needs_audio:
        tts = TTSClient(
            url=settings.TTS_WS_URL,
            token=settings.TTS_TOKEN,
            client_id=self.client_id,
        )
        try:
            await tts.connect(
                voice=self.config.tts_voice,
                speed=self.config.tts_speed,
            )
        except Exception as e:
            logger.warning(f"[{self.client_id}] TTS no disponible, continuando en modo texto: {e}")
            tts = None

    async def _on_token(token_text: str):
        try:
            if "text" in self.handshake.output_mode:
                await self.client_ws.send_json({"type": "token", "content": token_text})
        except Exception:
            pass
        if tts:
            await tts.send_text_chunk(token_text)

    async def _on_event(data: dict):
        try:
            if data.get("type") == "error" or "status" in self.handshake.output_mode:
                await self.client_ws.send_json(data)
        except Exception:
            pass

    async def pipe_tokens():
        async for event in self.orchestrator.stream_response(
            text=text,
            user_id=self.client_id,
            model_id=self.config.preferred_model_id,
            system_prompt_extra=self.config.system_prompt_extra,
        ):
            if event.type == "token":
                await _on_token(event.content)
            else:
                await _on_event({"type": event.type, "content": event.content})
        if tts:
            await tts.end()

    async def pipe_audio():
        async for chunk in tts.get_audio_stream():
            try:
                await self.client_ws.send_bytes(chunk)
            except Exception:
                return

    if tts:
        try:
            await asyncio.gather(pipe_tokens(), pipe_audio())
        finally:
            await tts.close()
    else:
        await pipe_tokens()
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
python -c "from src.services.bridge import JotaBridge; print('OK')"
```

Expected: `OK` (will fail if OrchestratorClient import is still present)

- [ ] **Step 3: Commit**

```bash
git add src/services/bridge.py
git commit -m "refactor(bridge): inject OrchestratorProtocol, remove orchestrator lifecycle"
```

---

## Task 6: Update routes.py — Inject Orchestrator from Registry

**Files:**
- Modify: `src/api/routes.py`

- [ ] **Step 1: Update the WebSocket route to inject the orchestrator**

In `src/api/routes.py`, change the bridge instantiation block (lines ~47-48):

```python
# BEFORE:
bridge = JotaBridge(client=client, config=config, client_ws=websocket)

# AFTER — add these two lines and change the constructor call:
    orchestrator = websocket.scope["app"].state.orchestrators.default()
    bridge = JotaBridge(client=client, config=config, client_ws=websocket, orchestrator=orchestrator)
```

The full updated block (step 3 in routes.py):
```python
    # 3. INSTANCIAR EL PUENTE DE MICROSERVICIOS
    orchestrator = websocket.scope["app"].state.orchestrators.default()
    bridge = JotaBridge(client=client, config=config, client_ws=websocket, orchestrator=orchestrator)
    bridge.handshake = handshake
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from src.api.routes import router; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/api/routes.py
git commit -m "feat(routes): inject orchestrator from registry into JotaBridge"
```

---

## Task 7: Update main.py — Build Registry in Lifespan

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Update `main.py`**

```python
# src/main.py
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.api.config_routes import router as config_router
from src.api.conversation_routes import router as conversation_router
from src.api.models_routes import router as models_router
from src.api.health_routes import router as health_router
from src.api.openai_routes import router as openai_router
from src.services.db_client import db_client
from src.services.orchestrators.registry import build_registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_client.connect()
    registry = build_registry()
    await registry.connect_all()
    app.state.orchestrators = registry
    yield
    await registry.close_all()
    await db_client.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend - Enrutador principal de WebSockets. Titiritero del Ecosistema IA.",
    version="2.0.0",
    lifespan=lifespan,
)

# WebSocket
app.include_router(stream_router)

# OpenAI-compatible REST (no prefix — /v1/ is in the router itself)
app.include_router(openai_router)

# REST API
app.include_router(config_router, prefix="/api")
app.include_router(conversation_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(health_router, prefix="/api")


@app.get("/health")
def healthcheck():
    return {"status": "online", "service": "JotaGateway BFF"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
```

Note: `openai_routes` is imported here but doesn't exist yet — the import will fail until Task 12. Either create a stub or do Task 12 next.

- [ ] **Step 2: Create a temporary stub for openai_routes to unblock this task**

```python
# src/api/openai_routes.py  (STUB — will be replaced in Task 12)
from fastapi import APIRouter
router = APIRouter(prefix="/v1")
```

- [ ] **Step 3: Verify app loads**

```bash
python -c "from src.main import app; print('OK')"
```

Expected: `OK` (registry won't connect yet — OPENCLAW_TOKEN needed — but imports are clean)

- [ ] **Step 4: Commit**

```bash
git add src/main.py src/api/openai_routes.py
git commit -m "feat(main): build OrchestratorRegistry in lifespan, register openai_router"
```

---

## Task 8: OpenClawClient — WebSocket Client

**Files:**
- Create: `src/services/orchestrators/openclaw_client.py`

OpenClaw protocol v4: WebSocket-only. Handshake: challenge → connect (backend mode) → hello-ok. Messages via `chat.send`. Streaming via `chat` events with `deltaText`. Turn complete when matching `res {ok: true}` arrives.

- [ ] **Step 1: Write `openclaw_client.py`**

```python
# src/services/orchestrators/openclaw_client.py
import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Optional

import websockets
from websockets.legacy.client import WebSocketClientProtocol

from src.services.orchestrators.protocol import OrchestratorEvent

logger = logging.getLogger(__name__)


class OpenClawClient:
    """
    WebSocket client for OpenClaw gateway (protocol v4).

    Maintains a single persistent connection. Each call to stream_response()
    sends one chat.send turn and yields OrchestratorEvent tokens until the
    matching res frame arrives.
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        session_key: str = "jota-gateway-default",
    ):
        self._uri = f"ws://{host}:{port}"
        self._token = token
        self._session_key = session_key
        self._ws: Optional[WebSocketClientProtocol] = None
        self._listener_task: Optional[asyncio.Task] = None
        # Active turn state (one turn at a time)
        self._active_req_id: Optional[str] = None
        self._turn_queue: Optional[asyncio.Queue] = None
        # Health ping state
        self._health_futures: dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._uri)

        # 1. Wait for connect.challenge
        raw = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
        frame = json.loads(raw)
        if frame.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected connect.challenge, got: {frame}")

        # 2. Send connect (backend mode — no device signature needed from loopback)
        req_id = str(uuid.uuid4())
        await self._ws.send(json.dumps({
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 4,
                "client": {
                    "id": "jota-gateway",
                    "version": "1.0.0",
                    "platform": "linux",
                    "mode": "backend",
                },
                "role": "operator",
                "scopes": ["operator.read", "operator.write"],
                "auth": {"token": self._token},
            },
        }))

        # 3. Wait for hello-ok
        raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
        hello = json.loads(raw)
        if not hello.get("ok"):
            raise RuntimeError(f"OpenClaw handshake failed: {hello.get('error')}")

        # 4. Start background listener
        self._listener_task = asyncio.create_task(self._listen())
        logger.info(f"OpenClawClient connected → {self._uri}")

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("OpenClawClient closed.")

    async def ping(self) -> bool:
        if not self._ws:
            return False
        try:
            req_id = str(uuid.uuid4())
            loop = asyncio.get_event_loop()
            fut: asyncio.Future = loop.create_future()
            self._health_futures[req_id] = fut
            await self._ws.send(json.dumps({
                "type": "req",
                "id": req_id,
                "method": "health",
                "params": {},
            }))
            res = await asyncio.wait_for(fut, timeout=5.0)
            return res.get("ok", False)
        except Exception as e:
            logger.debug(f"OpenClawClient ping failed: {e}")
            self._health_futures.pop(req_id, None)
            return False

    # ------------------------------------------------------------------
    # Listener (background task)
    # ------------------------------------------------------------------

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                frame = json.loads(raw)
                ftype = frame.get("type")

                if ftype == "res":
                    req_id = frame.get("id")
                    # Health ping response
                    if req_id in self._health_futures:
                        fut = self._health_futures.pop(req_id)
                        if not fut.done():
                            fut.set_result(frame)
                    # Active turn response
                    elif req_id == self._active_req_id and self._turn_queue is not None:
                        await self._turn_queue.put(("done", frame))

                elif ftype == "event":
                    event_name = frame.get("event")
                    # Chat delta → active turn queue
                    if event_name == "chat" and self._turn_queue is not None:
                        payload = frame.get("payload", {})
                        await self._turn_queue.put(("chat", payload))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"OpenClawClient listener error: {e}")
            if self._turn_queue is not None:
                await self._turn_queue.put(("error", str(e)))

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]:
        if not self._ws:
            yield OrchestratorEvent(type="error", content="OpenClawClient not connected")
            return

        req_id = str(uuid.uuid4())
        self._active_req_id = req_id
        self._turn_queue = asyncio.Queue()

        try:
            await self._ws.send(json.dumps({
                "type": "req",
                "id": req_id,
                "method": "chat.send",
                "params": {
                    "session": {"key": self._session_key},
                    "message": text,
                    "idempotencyKey": str(uuid.uuid4()),
                },
            }))

            while True:
                kind, data = await self._turn_queue.get()

                if kind == "chat":
                    delta = data.get("deltaText", "")
                    if delta:
                        yield OrchestratorEvent(type="token", content=delta)

                elif kind == "done":
                    if not data.get("ok"):
                        yield OrchestratorEvent(type="error", content=str(data.get("error", {})))
                    else:
                        yield OrchestratorEvent(type="status", content="done")
                    break

                elif kind == "error":
                    yield OrchestratorEvent(type="error", content=str(data))
                    break

        finally:
            self._active_req_id = None
            self._turn_queue = None
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "from src.services.orchestrators.openclaw_client import OpenClawClient; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/services/orchestrators/openclaw_client.py
git commit -m "feat(orchestrators): add OpenClawClient (WebSocket, protocol v4)"
```

---

## Task 9: Tests — OpenClawClient

**Files:**
- Create: `tests/integration/test_openclaw_client.py`

These tests mock `websockets.connect` to avoid needing a real OpenClaw instance.

- [ ] **Step 1: Write the tests**

```python
# tests/integration/test_openclaw_client.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.orchestrators.openclaw_client import OpenClawClient
from src.services.orchestrators.protocol import OrchestratorEvent


def challenge_frame():
    return json.dumps({"type": "event", "event": "connect.challenge", "payload": {"nonce": "abc", "ts": 0}})


def hello_ok_frame(req_id: str):
    return json.dumps({
        "type": "res", "id": req_id, "ok": True,
        "payload": {"type": "hello-ok", "protocol": 4, "policy": {"tickIntervalMs": 15000}}
    })


class FakeWebSocket:
    """Simulates a WebSocket server for testing OpenClawClient."""

    def __init__(self, recv_sequence: list[str]):
        self._recv_iter = iter(recv_sequence)
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        return next(self._recv_iter)

    async def close(self) -> None:
        pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._recv_iter)
        except StopIteration:
            raise StopAsyncIteration


@pytest.mark.asyncio
async def test_connect_handshake():
    """Client performs challenge → connect → hello-ok handshake."""
    connect_req_id = None

    async def fake_connect(uri, **kwargs):
        ws = FakeWebSocket([challenge_frame()])
        # Capture the connect req_id so we can build hello-ok
        original_send = ws.send
        async def capturing_send(data):
            nonlocal connect_req_id
            frame = json.loads(data)
            if frame.get("method") == "connect":
                connect_req_id = frame["id"]
                # Inject hello-ok into the queue
                ws._recv_iter = iter([hello_ok_frame(connect_req_id)])
            await original_send(data)
        ws.send = capturing_send
        return ws

    with patch("websockets.connect", side_effect=fake_connect):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()
        assert client._ws is not None
        await client.close()


@pytest.mark.asyncio
async def test_stream_response_yields_tokens():
    """stream_response yields OrchestratorEvent tokens from chat events."""
    req_id_holder = {}

    class SmartFakeWS(FakeWebSocket):
        def __init__(self):
            self._queue = asyncio.Queue()
            self.sent = []
            # Pre-load handshake frames
            self._handshake = iter([challenge_frame()])
            self._handshake_done = False

        async def recv(self):
            if not self._handshake_done:
                try:
                    return next(self._handshake)
                except StopIteration:
                    self._handshake_done = True
            return await self._queue.get()

        async def send(self, data):
            frame = json.loads(data)
            self.sent.append(frame)
            method = frame.get("method")
            req_id = frame.get("id")
            if method == "connect":
                await self._queue.put(hello_ok_frame(req_id))
            elif method == "chat.send":
                req_id_holder["id"] = req_id
                # Send two chat deltas then the res
                await self._queue.put(json.dumps({
                    "type": "event", "event": "chat",
                    "payload": {"deltaText": "Hello", "replace": False, "seq": 1}
                }))
                await self._queue.put(json.dumps({
                    "type": "event", "event": "chat",
                    "payload": {"deltaText": " world", "replace": False, "seq": 2}
                }))
                await self._queue.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True, "payload": {}
                }))

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self._queue.get()

        async def close(self): pass

    fake_ws = SmartFakeWS()
    with patch("websockets.connect", return_value=fake_ws):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()

        events = []
        async for event in client.stream_response(text="Hi", user_id="test"):
            events.append(event)

        await client.close()

    tokens = [e for e in events if e.type == "token"]
    status = [e for e in events if e.type == "status"]
    assert [t.content for t in tokens] == ["Hello", " world"]
    assert len(status) == 1
    assert status[0].content == "done"


@pytest.mark.asyncio
async def test_ping_returns_true_on_ok_response():
    """ping() sends health req and returns True when res.ok is True."""
    class HealthFakeWS(FakeWebSocket):
        def __init__(self):
            self._queue = asyncio.Queue()
            self.sent = []
            self._handshake = [challenge_frame()]
            self._idx = 0

        async def recv(self):
            if self._idx < len(self._handshake):
                val = self._handshake[self._idx]
                self._idx += 1
                return val
            return await self._queue.get()

        async def send(self, data):
            frame = json.loads(data)
            self.sent.append(frame)
            if frame.get("method") == "connect":
                await self._queue.put(hello_ok_frame(frame["id"]))
            elif frame.get("method") == "health":
                await self._queue.put(json.dumps({"type": "res", "id": frame["id"], "ok": True, "payload": {}}))

        def __aiter__(self): return self
        async def __anext__(self): return await self._queue.get()
        async def close(self): pass

    fake_ws = HealthFakeWS()
    with patch("websockets.connect", return_value=fake_ws):
        client = OpenClawClient(host="127.0.0.1", port=18789, token="test-token")
        await client.connect()
        result = await client.ping()
        await client.close()

    assert result is True
```

- [ ] **Step 2: Run the tests**

```bash
PYTHONPATH=. pytest tests/integration/test_openclaw_client.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 3: Run registry tests now that openclaw_client exists**

```bash
PYTHONPATH=. pytest tests/integration/test_orchestrator_registry.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_openclaw_client.py
git commit -m "test(openclaw_client): handshake, token streaming, ping"
```

---

## Task 10: Update conftest — Wire Mock Orchestrator in Tests

The existing bridge/WS tests rely on the HTTP orchestrator mocks. Those are now replaced by a registry with a mock `OrchestratorProtocol`.

**Files:**
- Modify: `tests/integration/conftest.py`

- [ ] **Step 1: Update `conftest.py`**

```python
"""
Fixtures de integración.

HTTP (jota-db) interceptado por respx.
WebSocket (transcriber, TTS) con fake servers en hilos de background.
Orchestrator inyectado via MockOrchestrator (OrchestratorProtocol).
"""
import pytest
import httpx
import respx
from unittest.mock import AsyncMock, MagicMock
from starlette.testclient import TestClient

from src.main import app
from src.services.db_client import db_client
from src.services.orchestrators.protocol import OrchestratorProtocol, OrchestratorEvent
from src.services.orchestrators.registry import OrchestratorRegistry

# ---------------------------------------------------------------------------
# Datos de test estándar
# ---------------------------------------------------------------------------

VALID_KEY = "valid-key-abc"
CLIENT_UUID = "uuid-client-123"

SESSION_RESPONSE = {
    "client": {"id": CLIENT_UUID, "client_key": VALID_KEY, "is_active": True},
    "config": {
        "stt_language": "es",
        "stt_vad_thold": 0.0,
        "tts_voice": "af_heart",
        "tts_speed": 1.0,
        "preferred_model_id": None,
        "system_prompt_extra": None,
        "barge_in_enabled": True,
        "barge_in_min_chars": 5,
        "conversation_memory_limit": 20,
    },
}

CONFIG_RESPONSE = SESSION_RESPONSE["config"]

# ---------------------------------------------------------------------------
# Cache cleanup
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_db_cache():
    db_client._session_cache.clear()
    db_client._models_cache.clear()
    yield
    db_client._session_cache.clear()
    db_client._models_cache.clear()

# ---------------------------------------------------------------------------
# Mock Orchestrator
# ---------------------------------------------------------------------------

def make_mock_orchestrator(tokens: list[str] = None) -> OrchestratorProtocol:
    """Creates a mock orchestrator that yields the given tokens then status:done."""
    if tokens is None:
        tokens = ["Hola"]

    async def _stream(*args, **kwargs):
        for t in tokens:
            yield OrchestratorEvent(type="token", content=t)
        yield OrchestratorEvent(type="status", content="done")

    mock = MagicMock(spec=OrchestratorProtocol)
    mock.connect = AsyncMock()
    mock.close = AsyncMock()
    mock.ping = AsyncMock(return_value=True)
    mock.stream_response = _stream
    return mock


def make_mock_registry(orchestrator=None) -> OrchestratorRegistry:
    if orchestrator is None:
        orchestrator = make_mock_orchestrator()
    registry = MagicMock(spec=OrchestratorRegistry)
    registry.connect_all = AsyncMock()
    registry.close_all = AsyncMock()
    registry.default = MagicMock(return_value=orchestrator)
    registry.get = MagicMock(return_value=orchestrator)
    return registry

# ---------------------------------------------------------------------------
# respx: intercepta tráfico HTTP hacia jota-db
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_services():
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        # --- jota-db: auth ---
        router.get("http://localhost:8001/auth/session").mock(
            side_effect=lambda req: (
                httpx.Response(200, json=SESSION_RESPONSE)
                if req.headers.get("x-api-key") == VALID_KEY
                else httpx.Response(401, json={"detail": "Invalid key"})
            )
        )
        # --- jota-db: config ---
        router.get("http://localhost:8001/config/me").mock(
            return_value=httpx.Response(200, json=CONFIG_RESPONSE)
        )
        router.put("http://localhost:8001/config/me").mock(
            return_value=httpx.Response(200, json=CONFIG_RESPONSE)
        )
        router.post("http://localhost:8001/config/me/reset").mock(
            return_value=httpx.Response(200, json=CONFIG_RESPONSE)
        )
        # --- jota-db: conversations ---
        router.get("http://localhost:8001/conversations").mock(
            return_value=httpx.Response(200, json=[{"id": "conv-1", "title": "Test"}])
        )
        router.get(url__regex=r"http://localhost:8001/conversations/.+/messages").mock(
            return_value=httpx.Response(200, json=[{"id": "msg-1", "content": "hola"}])
        )
        router.patch(url__regex=r"http://localhost:8001/conversations/.+").mock(
            return_value=httpx.Response(200, json={"id": "conv-1", "status": "archived"})
        )
        # --- jota-db: models ---
        router.get("http://localhost:8001/models").mock(
            return_value=httpx.Response(200, json=[{"id": "llama3", "name": "LLaMA 3"}])
        )
        # --- transcriber: health ---
        router.get("http://localhost:9000/health").mock(
            return_value=httpx.Response(200)
        )
        # --- TTS: health ---
        router.get("http://localhost:8005/health").mock(
            return_value=httpx.Response(200)
        )
        yield router


@pytest.fixture
def mock_orchestrator():
    return make_mock_orchestrator()


@pytest.fixture
def mock_registry(mock_orchestrator):
    return make_mock_registry(mock_orchestrator)


@pytest.fixture
def client(mock_services, mock_registry, monkeypatch):
    """TestClient con jota-db mockeado y orchestrator mock inyectado."""
    monkeypatch.setattr("src.main.build_registry", lambda: mock_registry)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"x-api-key": VALID_KEY}
```

- [ ] **Step 2: Run all existing tests to see what breaks**

```bash
PYTHONPATH=. pytest tests/integration/ -v 2>&1 | head -80
```

Expected: most tests pass; some bridge/WS tests may fail if they reference the old orchestrator HTTP mock.

- [ ] **Step 3: Fix any failing tests**

If any test calls `mock_services` router for `http://localhost:8000/*` routes (old Jota orchestrator), remove those route overrides — they're no longer used. The `mock_orchestrator` / `mock_registry` fixtures replace them.

- [ ] **Step 4: Run full suite**

```bash
PYTHONPATH=. pytest tests/integration/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test(conftest): replace HTTP orchestrator mock with MockOrchestrator protocol"
```

---

## Task 11: Archive Jota Orchestrator Client

The file `src/services/orchestrator_client.py` is no longer imported. Archive it.

- [ ] **Step 1: Remove from active path**

```bash
mkdir -p src/services/_legacy
mv src/services/orchestrator_client.py src/services/_legacy/jota_orchestrator_client.py
```

- [ ] **Step 2: Verify nothing imports it**

```bash
grep -r "orchestrator_client" src/ tests/
```

Expected: no results (or only in `_legacy/`).

- [ ] **Step 3: Run tests**

```bash
PYTHONPATH=. pytest tests/integration/ -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/services/_legacy/ src/services/orchestrator_client.py
git commit -m "chore: archive JotaOrchestratorClient to _legacy (preserved for reference)"
```

---

## Task 12: OpenAI REST Endpoint — Feature B

**Files:**
- Modify: `src/api/openai_routes.py` (replace the stub from Task 7)

- [ ] **Step 1: Write the failing tests first**

```python
# tests/integration/test_rest_openai.py
import json
import pytest
from starlette.testclient import TestClient

from tests.integration.conftest import VALID_KEY


def test_get_models_returns_list(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 1
    assert body["data"][0]["id"] == "openclaw"


def test_chat_completions_non_streaming_returns_content(client):
    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "Hola"}],
        "stream": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Hola"  # mock returns ["Hola"]


def test_chat_completions_uses_last_user_message(client):
    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Answer"},
            {"role": "user", "content": "Second"},
        ],
        "stream": False,
    })
    assert r.status_code == 200


def test_chat_completions_streaming_returns_sse(client):
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": True,
    }) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        body = r.text
    assert "data:" in body
    assert "[DONE]" in body


def test_chat_completions_no_user_message_returns_empty(client):
    r = client.post("/v1/chat/completions", json={
        "model": "openclaw",
        "messages": [{"role": "system", "content": "Be helpful"}],
        "stream": False,
    })
    assert r.status_code == 200
```

- [ ] **Step 2: Run tests (they should fail — stub has no routes)**

```bash
PYTHONPATH=. pytest tests/integration/test_rest_openai.py -v
```

Expected: `404 Not Found` or assertion errors.

- [ ] **Step 3: Implement `openai_routes.py`**

```python
# src/api/openai_routes.py
import json
import uuid
import logging
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1")


@router.get("/models")
async def list_models(request: Request):
    registry = request.app.state.orchestrators
    try:
        name = registry.default().__class__.__name__
    except Exception:
        name = "openclaw"
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": "openclaw",
                "object": "model",
                "created": 0,
                "owned_by": "openclaw",
            }
        ]
    })


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    # Extract last user message as prompt
    text = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            text = msg.get("content", "")
            break

    registry = request.app.state.orchestrators
    orchestrator = registry.default()
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if stream:
        async def generate():
            async for event in orchestrator.stream_response(text=text, user_id="ha"):
                if event.type == "token":
                    chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "choices": [{
                            "delta": {"content": event.content},
                            "index": 0,
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
            # Final chunk
            final_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    else:
        tokens = []
        async for event in orchestrator.stream_response(text=text, user_id="ha"):
            if event.type == "token":
                tokens.append(event.content)

        content = "".join(tokens)
        return JSONResponse({
            "id": completion_id,
            "object": "chat.completion",
            "choices": [{
                "message": {"role": "assistant", "content": content},
                "index": 0,
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(tokens), "total_tokens": len(tokens)},
        })
```

- [ ] **Step 4: Run the tests**

```bash
PYTHONPATH=. pytest tests/integration/test_rest_openai.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Run full test suite**

```bash
PYTHONPATH=. pytest tests/integration/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/api/openai_routes.py tests/integration/test_rest_openai.py
git commit -m "feat(api): add OpenAI-compatible /v1/chat/completions and /v1/models endpoints"
```

---

## Task 13: Lint and Final Check

- [ ] **Step 1: Lint**

```bash
ruff check src/ tests/
```

Fix any issues reported. Common: unused imports, missing type annotations where required.

- [ ] **Step 2: Full test suite**

```bash
PYTHONPATH=. pytest tests/integration/ -v
```

Expected: all tests pass, no warnings.

- [ ] **Step 3: Manual smoke test (requires running OpenClaw)**

```bash
# Start jota-gateway
uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload

# In another terminal — test models endpoint
curl http://localhost:8004/v1/models

# Test chat completions (non-streaming)
curl -X POST http://localhost:8004/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"Hola, ¿qué hora es?"}],"stream":false}'

# Test streaming
curl -X POST http://localhost:8004/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"Hola"}],"stream":true}'
```

Expected: responses from OpenClaw via the WebSocket bridge.

- [ ] **Step 4: Final commit**

```bash
git add -p   # review any remaining changes
git commit -m "chore: final lint and integration check"
```

---

## Task 14: Merge to Main

- [ ] **Step 1: Push feature branch**

```bash
git push -u origin feat/openclaw-orchestrator
```

- [ ] **Step 2: Create PR**

```bash
gh pr create \
  --title "feat: OpenClaw orchestrator abstraction + OpenAI REST endpoint" \
  --body "$(cat <<'EOF'
## Summary
- Adds OrchestratorProtocol + OrchestratorEvent abstraction (typing.Protocol)
- Implements OpenClawClient (WebSocket, protocol v4, backend loopback mode)
- OrchestratorRegistry manages client lifecycle in app lifespan
- JotaBridge receives orchestrator via injection (no more hardcoded Jota client)
- Exposes GET /v1/models and POST /v1/chat/completions (streaming + non-streaming)
- Jota orchestrator client archived in _legacy/ and preserved in legacy/jota-orchestrator branch

## Test plan
- [ ] All integration tests pass
- [ ] Manual smoke test against live OpenClaw instance
- [ ] HA configured to use http://green-house/api/gateway/v1

Closes #(issue number if applicable)
EOF
)"
```

---

## Reference

- Spec: `docs/superpowers/specs/2026-05-20-orchestrator-abstraction-design.md`
- OpenClaw protocol: `.claude/skills/openclaw/references/protocol.md`
- OpenClaw WS: `ws://127.0.0.1:18789`, backend mode (no device signature from loopback)
- Token: `cat ~/.openclaw/openclaw.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['gateway']['auth']['token'])"`
