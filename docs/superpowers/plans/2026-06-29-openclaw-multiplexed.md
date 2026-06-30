# OpenClaw Multiplexed Connection & Push Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the singleton serializing `OpenClawClient` with a multiplexed concurrent connection that routes simultaneous sessions by `sessionKey` and delivers agent-initiated push events to connected clients.

**Architecture:** A single persistent WebSocket to OpenClaw handles all clients concurrently — `sessions.subscribe` ensures every event carries `sessionKey`, and a `TurnRegistry` routes tokens to the right caller. A `ClientRegistry` maps `client_id → JotaBridge` so agent-initiated pushes reach the correct physical client. `JotaBridge` gets push lifecycle hooks (`on_push_turn_start`, `deliver_push`, `on_push_turn_end`) that reuse the existing TTS pipeline.

**Tech Stack:** Python 3.12, FastAPI, websockets 13, asyncio, pytest, respx

## Global Constraints

- Every file in `src/services/openclaw/` must stay under ~200 lines
- TDD: write the failing test first, then the minimal implementation
- Run `PYTHONPATH=. pytest` after each task — all previously-passing tests must stay green
- `session_key` format: `make_session_key(agent, client_id)` → `"agent:{agent}:{client_id}"` (unchanged)
- `client_id` from any session key: `sk.rsplit(":", 1)[-1]` (last segment after last colon)
- New `chat.send` API format: `{"sessionKey": "..."}` not `{"session": {"key": "..."}}`
- New `chat.abort` API format: `{"sessionKey": "..."}` not `{"session": {"key": "..."}}`
- Do NOT modify `src/services/session_registry.py` (pipeline tracker — unrelated to routing)
- Do NOT modify `src/services/orchestration.py` (call_orchestrator helper — unchanged)
- Commit after every task

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/services/openclaw/__init__.py` | Public exports |
| Create | `src/services/openclaw/models.py` | `GatewayInfo`, `AgentInfo` parsed from `hello-ok` |
| Create | `src/services/openclaw/registry.py` | `TurnRegistry`, `ClientRegistry`, `client_id_from_session_key` |
| Create | `src/services/openclaw/dispatcher.py` | `FrameDispatcher` — routes frames to registries |
| Create | `src/services/openclaw/client.py` | `OpenClawClient` — WS connection, send, `_listen` → dispatcher |
| Create | `src/services/openclaw/reconnecting.py` | `ReconnectingOpenClawClient` — backoff wrapper |
| Move | `src/services/orchestrators/protocol.py` → `src/services/protocol.py` | `OrchestratorEvent`, `OrchestratorProtocol` |
| Modify | `src/services/bridge.py` | Add push hooks, `client_registry` param, remove `OPENCLAW_DEFAULT_AGENT` |
| Modify | `src/main.py` | Replace `build_registry()` with new client + registries in lifespan |
| Modify | `src/api/routes.py` | Agent validation, `default_agent` from `gateway_info`, new `app.state` keys |
| Modify | `src/core/config.py` | Remove `OPENCLAW_DEFAULT_AGENT`, `DEFAULT_ORCHESTRATOR` |
| Delete | `src/services/orchestrators/` | Replaced by `src/services/openclaw/` |
| Create | `tests/unit/test_openclaw_models.py` | Tests for `GatewayInfo.from_hello_ok` |
| Create | `tests/unit/test_openclaw_registry.py` | Tests for `TurnRegistry`, `ClientRegistry`, `client_id_from_session_key` |
| Create | `tests/unit/test_openclaw_dispatcher.py` | Tests for `FrameDispatcher` routing logic |
| Rewrite | `tests/integration/test_openclaw_client.py` | Tests for new `OpenClawClient` |
| Rewrite | `tests/integration/test_reconnecting_openclaw.py` | Tests for `ReconnectingOpenClawClient` |
| Delete | `tests/integration/test_orchestrator_registry.py` | Replaced by new tests |
| Create | `tests/unit/test_bridge_push.py` | Tests for push delivery hooks |
| Modify | `tests/integration/test_rest_openai.py` | Update `app.state` references |
| Modify | `tests/integration/conftest.py` | Replace orchestrator fixtures |

---

## Task 1: Move protocol.py + create package scaffold

**Files:**
- Create: `src/services/protocol.py` (moved from `orchestrators/protocol.py`)
- Create: `src/services/openclaw/__init__.py`
- Modify: all files that `from src.services.orchestrators.protocol import ...`

**Interfaces:**
- Produces: `OrchestratorEvent`, `OrchestratorProtocol` at `src.services.protocol`

- [ ] **Step 1: Move protocol.py**

```bash
cp src/services/orchestrators/protocol.py src/services/protocol.py
```

Content of `src/services/protocol.py` (identical to the original):

```python
from dataclasses import dataclass
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
        session_key: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]: ...
```

- [ ] **Step 2: Update all imports of the old location**

Find every file that imports from `orchestrators.protocol`:

```bash
grep -r "orchestrators.protocol" src/ tests/ --include="*.py" -l
```

In each file found, replace:
```python
from src.services.orchestrators.protocol import OrchestratorEvent
from src.services.orchestrators.protocol import OrchestratorProtocol
```
with:
```python
from src.services.protocol import OrchestratorEvent
from src.services.protocol import OrchestratorProtocol
```

- [ ] **Step 3: Create package**

```bash
mkdir -p src/services/openclaw
touch src/services/openclaw/__init__.py
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest -x -q
```

Expected: all previously passing tests still pass (no logic changed, only moved).

- [ ] **Step 5: Commit**

```bash
git add src/services/protocol.py src/services/openclaw/__init__.py
git add $(grep -r "orchestrators.protocol" src/ tests/ --include="*.py" -l) 2>/dev/null || true
git commit -m "refactor: move OrchestratorProtocol to src/services/protocol.py, scaffold openclaw package"
```

---

## Task 2: `models.py`

**Files:**
- Create: `src/services/openclaw/models.py`
- Create: `tests/unit/test_openclaw_models.py`

**Interfaces:**
- Produces: `GatewayInfo`, `AgentInfo`; `GatewayInfo.from_hello_ok(payload: dict) -> GatewayInfo`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_openclaw_models.py`:

```python
import pytest
from src.services.openclaw.models import GatewayInfo, AgentInfo

HELLO_OK_PAYLOAD = {
    "type": "hello-ok",
    "protocol": 4,
    "server": {"version": "2026.6.6", "connId": "abc-123"},
    "policy": {"tickIntervalMs": 30000, "maxPayload": 26214400, "maxBufferedBytes": 52428800},
    "snapshot": {
        "defaultAgentId": "main",
        "agents": [
            {"agentId": "main", "name": "Main Agent", "isDefault": True, "heartbeat": {}},
            {"agentId": "assistant", "name": "Jota Voice", "isDefault": False, "heartbeat": {}},
        ],
        "sessionDefaults": {"defaultAgentId": "main"},
    },
    "auth": {"role": "operator", "scopes": ["operator.read", "operator.write"]},
}


def test_from_hello_ok_basic():
    info = GatewayInfo.from_hello_ok(HELLO_OK_PAYLOAD)
    assert info.protocol_version == 4
    assert info.server_version == "2026.6.6"
    assert info.conn_id == "abc-123"
    assert info.tick_interval_ms == 30000
    assert info.max_payload == 26214400
    assert info.default_agent_id == "main"


def test_from_hello_ok_agents():
    info = GatewayInfo.from_hello_ok(HELLO_OK_PAYLOAD)
    assert "main" in info.agents
    assert "assistant" in info.agents
    assert info.agents["main"].is_default is True
    assert info.agents["main"].name == "Main Agent"
    assert info.agents["assistant"].is_default is False


def test_has_agent():
    info = GatewayInfo.from_hello_ok(HELLO_OK_PAYLOAD)
    assert info.has_agent("main") is True
    assert info.has_agent("assistant") is True
    assert info.has_agent("nonexistent") is False


def test_from_hello_ok_minimal():
    """Handles missing optional fields gracefully."""
    info = GatewayInfo.from_hello_ok({})
    assert info.default_agent_id == "main"
    assert info.agents == {}
    assert info.tick_interval_ms == 15000
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=. pytest tests/unit/test_openclaw_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.services.openclaw.models'`

- [ ] **Step 3: Implement `models.py`**

Create `src/services/openclaw/models.py`:

```python
from dataclasses import dataclass, field


@dataclass
class AgentInfo:
    agent_id: str
    name: str
    is_default: bool


@dataclass
class GatewayInfo:
    protocol_version: int
    server_version: str
    conn_id: str
    default_agent_id: str
    agents: dict[str, AgentInfo]
    tick_interval_ms: int
    max_payload: int

    def has_agent(self, agent_id: str) -> bool:
        return agent_id in self.agents

    @classmethod
    def from_hello_ok(cls, payload: dict) -> "GatewayInfo":
        server = payload.get("server", {})
        policy = payload.get("policy", {})
        snapshot = payload.get("snapshot", {})

        agents: dict[str, AgentInfo] = {}
        for a in snapshot.get("agents", []):
            aid = a["agentId"]
            agents[aid] = AgentInfo(
                agent_id=aid,
                name=a.get("name", aid),
                is_default=a.get("isDefault", False),
            )

        default_agent_id = (
            snapshot.get("defaultAgentId")
            or snapshot.get("sessionDefaults", {}).get("defaultAgentId")
            or "main"
        )

        return cls(
            protocol_version=payload.get("protocol", 4),
            server_version=server.get("version", ""),
            conn_id=server.get("connId", ""),
            default_agent_id=default_agent_id,
            agents=agents,
            tick_interval_ms=policy.get("tickIntervalMs", 15000),
            max_payload=policy.get("maxPayload", 26214400),
        )
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest tests/unit/test_openclaw_models.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Update `__init__.py`**

```python
# src/services/openclaw/__init__.py
from src.services.openclaw.models import GatewayInfo, AgentInfo

__all__ = ["GatewayInfo", "AgentInfo"]
```

- [ ] **Step 6: Commit**

```bash
git add src/services/openclaw/models.py src/services/openclaw/__init__.py tests/unit/test_openclaw_models.py
git commit -m "feat(openclaw): GatewayInfo + AgentInfo models parsed from hello-ok"
```

---

## Task 3: `registry.py`

**Files:**
- Create: `src/services/openclaw/registry.py`
- Create: `tests/unit/test_openclaw_registry.py`

**Interfaces:**
- Produces:
  - `TurnRegistry.register(req_id, session_key) -> asyncio.Queue`
  - `TurnRegistry.unregister(session_key, req_id) -> None`
  - `TurnRegistry.get_queue_by_session(session_key) -> asyncio.Queue | None`
  - `TurnRegistry.get_queue_by_req(req_id) -> asyncio.Queue | None`
  - `TurnRegistry.error_all(message) -> None`
  - `ClientRegistry.register(client_id, bridge) -> None`
  - `ClientRegistry.unregister(client_id) -> None`
  - `ClientRegistry.get(client_id) -> Any | None`
  - `client_id_from_session_key(session_key: str) -> str`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_openclaw_registry.py`:

```python
import asyncio
import pytest
from src.services.openclaw.registry import TurnRegistry, ClientRegistry, client_id_from_session_key


# --- client_id_from_session_key ---

def test_client_id_simple():
    assert client_id_from_session_key("agent:main:hab_sito") == "hab_sito"

def test_client_id_with_colons():
    assert client_id_from_session_key("agent:plants:telegram:direct:5239228928") == "5239228928"

def test_client_id_two_parts():
    assert client_id_from_session_key("agent:assistant:probe-test") == "probe-test"


# --- TurnRegistry ---

def test_register_returns_queue():
    reg = TurnRegistry()
    q = reg.register("req-1", "agent:main:client-a")
    assert isinstance(q, asyncio.Queue)

def test_get_queue_by_session():
    reg = TurnRegistry()
    q = reg.register("req-1", "agent:main:client-a")
    assert reg.get_queue_by_session("agent:main:client-a") is q

def test_get_queue_by_req():
    reg = TurnRegistry()
    q = reg.register("req-1", "agent:main:client-a")
    assert reg.get_queue_by_req("req-1") is q

def test_get_queue_missing_returns_none():
    reg = TurnRegistry()
    assert reg.get_queue_by_session("nonexistent") is None
    assert reg.get_queue_by_req("nonexistent") is None

def test_unregister_clears_both():
    reg = TurnRegistry()
    reg.register("req-1", "agent:main:client-a")
    reg.unregister("agent:main:client-a", "req-1")
    assert reg.get_queue_by_session("agent:main:client-a") is None
    assert reg.get_queue_by_req("req-1") is None

def test_two_sessions_independent():
    reg = TurnRegistry()
    qa = reg.register("req-a", "agent:main:client-a")
    qb = reg.register("req-b", "agent:main:client-b")
    assert reg.get_queue_by_session("agent:main:client-a") is qa
    assert reg.get_queue_by_session("agent:main:client-b") is qb
    assert qa is not qb

def test_error_all_notifies_and_clears():
    reg = TurnRegistry()
    qa = reg.register("req-a", "agent:main:client-a")
    qb = reg.register("req-b", "agent:main:client-b")
    reg.error_all("reconnecting")
    assert qa.get_nowait() == ("error", "reconnecting")
    assert qb.get_nowait() == ("error", "reconnecting")
    assert reg.get_queue_by_session("agent:main:client-a") is None
    assert reg.get_queue_by_req("req-a") is None


# --- ClientRegistry ---

def test_client_registry_register_get():
    reg = ClientRegistry()
    bridge = object()
    reg.register("hab_sito", bridge)
    assert reg.get("hab_sito") is bridge

def test_client_registry_unregister():
    reg = ClientRegistry()
    reg.register("hab_sito", object())
    reg.unregister("hab_sito")
    assert reg.get("hab_sito") is None

def test_client_registry_missing_returns_none():
    reg = ClientRegistry()
    assert reg.get("nonexistent") is None
```

- [ ] **Step 2: Run to verify failures**

```bash
PYTHONPATH=. pytest tests/unit/test_openclaw_registry.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `registry.py`**

Create `src/services/openclaw/registry.py`:

```python
import asyncio
from typing import Any, Optional


def client_id_from_session_key(session_key: str) -> str:
    """Extract client_id as the last segment after the last colon.

    "agent:main:hab_sito"                     → "hab_sito"
    "agent:plants:telegram:direct:5239228928" → "5239228928"
    """
    return session_key.rsplit(":", 1)[-1]


class TurnRegistry:
    """Routes active OpenClaw turns (req_id / session_key) to asyncio queues.

    Queue message protocol:
      ("chat", payload_dict)  — streaming token
      ("done", frame_dict)    — final res frame
      ("error", str)          — internal error (e.g. reconnect)
    """

    def __init__(self) -> None:
        self._sessions: dict[str, asyncio.Queue] = {}
        self._req_to_session: dict[str, str] = {}

    def register(self, req_id: str, session_key: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._sessions[session_key] = queue
        self._req_to_session[req_id] = session_key
        return queue

    def unregister(self, session_key: str, req_id: str) -> None:
        self._sessions.pop(session_key, None)
        self._req_to_session.pop(req_id, None)

    def get_queue_by_session(self, session_key: str) -> Optional[asyncio.Queue]:
        return self._sessions.get(session_key)

    def get_queue_by_req(self, req_id: str) -> Optional[asyncio.Queue]:
        sk = self._req_to_session.get(req_id)
        return self._sessions.get(sk) if sk else None

    def error_all(self, message: str) -> None:
        for queue in self._sessions.values():
            queue.put_nowait(("error", message))
        self._sessions.clear()
        self._req_to_session.clear()


class ClientRegistry:
    """Maps active client_id → JotaBridge for push delivery."""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}

    def register(self, client_id: str, bridge: Any) -> None:
        self._clients[client_id] = bridge

    def unregister(self, client_id: str) -> None:
        self._clients.pop(client_id, None)

    def get(self, client_id: str) -> Optional[Any]:
        return self._clients.get(client_id)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest tests/unit/test_openclaw_registry.py -v
```

Expected: 14 PASSED

- [ ] **Step 5: Update `__init__.py`**

```python
# src/services/openclaw/__init__.py
from src.services.openclaw.models import GatewayInfo, AgentInfo
from src.services.openclaw.registry import TurnRegistry, ClientRegistry, client_id_from_session_key

__all__ = ["GatewayInfo", "AgentInfo", "TurnRegistry", "ClientRegistry", "client_id_from_session_key"]
```

- [ ] **Step 6: Commit**

```bash
git add src/services/openclaw/registry.py src/services/openclaw/__init__.py tests/unit/test_openclaw_registry.py
git commit -m "feat(openclaw): TurnRegistry + ClientRegistry + client_id_from_session_key"
```

---

## Task 4: `dispatcher.py`

**Files:**
- Create: `src/services/openclaw/dispatcher.py`
- Create: `tests/unit/test_openclaw_dispatcher.py`

**Interfaces:**
- Consumes: `TurnRegistry`, `ClientRegistry` (from Task 3)
- Produces: `FrameDispatcher.dispatch(frame: dict) -> None`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_openclaw_dispatcher.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import TurnRegistry, ClientRegistry


def make_dispatcher():
    turn_reg = TurnRegistry()
    client_reg = ClientRegistry()
    dispatcher = FrameDispatcher(turn_reg, client_reg)
    return dispatcher, turn_reg, client_reg


@pytest.mark.asyncio
async def test_res_started_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    frame = {"type": "res", "id": "req-1", "ok": True, "payload": {"status": "started", "runId": "r1"}}
    await dispatcher.dispatch(frame)
    assert q.empty()


@pytest.mark.asyncio
async def test_res_done_routes_to_turn_queue():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    frame = {"type": "res", "id": "req-1", "ok": True, "payload": {"status": "done"}}
    await dispatcher.dispatch(frame)
    kind, data = q.get_nowait()
    assert kind == "done"
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_res_unknown_req_id_is_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    frame = {"type": "res", "id": "unknown-id", "ok": True, "payload": {}}
    await dispatcher.dispatch(frame)  # must not raise


@pytest.mark.asyncio
async def test_chat_event_routes_to_session_queue():
    dispatcher, turn_reg, _ = make_dispatcher()
    q = turn_reg.register("req-1", "agent:main:client-a")
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1", "seq": 1, "state": "delta",
        "deltaText": "Hola",
    }
    frame = {"type": "event", "event": "chat", "payload": payload}
    await dispatcher.dispatch(frame)
    kind, data = q.get_nowait()
    assert kind == "chat"
    assert data["deltaText"] == "Hola"


@pytest.mark.asyncio
async def test_chat_event_no_active_turn_routes_to_client_registry():
    dispatcher, turn_reg, client_reg = make_dispatcher()
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1", "seq": 1, "deltaText": "Push!",
    }
    frame = {"type": "event", "event": "chat", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.deliver_push.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_chat_event_no_session_key_ignored():
    dispatcher, turn_reg, _ = make_dispatcher()
    frame = {"type": "event", "event": "chat", "payload": {"deltaText": "no key"}}
    await dispatcher.dispatch(frame)  # must not raise


@pytest.mark.asyncio
async def test_agent_lifecycle_start_calls_on_push_turn_start():
    dispatcher, _, client_reg = make_dispatcher()
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1", "seq": 1,
        "data": {"phase": "start", "startedAt": 123},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.on_push_turn_start.assert_awaited_once_with("agent:main:client-a")


@pytest.mark.asyncio
async def test_agent_lifecycle_end_calls_on_push_turn_end():
    dispatcher, _, client_reg = make_dispatcher()
    bridge = AsyncMock()
    client_reg.register("client-a", bridge)
    payload = {
        "sessionKey": "agent:main:client-a",
        "runId": "r1", "seq": 2,
        "data": {"phase": "end", "stopReason": "stop"},
    }
    frame = {"type": "event", "event": "agent", "payload": payload}
    await dispatcher.dispatch(frame)
    bridge.on_push_turn_end.assert_awaited_once_with("agent:main:client-a")


@pytest.mark.asyncio
async def test_health_and_tick_ignored():
    dispatcher, _, _ = make_dispatcher()
    for event_name in ("health", "tick", "presence", "sessions.changed"):
        frame = {"type": "event", "event": event_name, "payload": {}}
        await dispatcher.dispatch(frame)  # must not raise
```

- [ ] **Step 2: Run to verify failures**

```bash
PYTHONPATH=. pytest tests/unit/test_openclaw_dispatcher.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `dispatcher.py`**

Create `src/services/openclaw/dispatcher.py`:

```python
import logging
from src.services.openclaw.registry import TurnRegistry, ClientRegistry, client_id_from_session_key

logger = logging.getLogger(__name__)


class FrameDispatcher:
    """Routes incoming OpenClaw frames to the correct queue or bridge.

    Knows about TurnRegistry and ClientRegistry. Does not touch WebSocket or TTS.
    """

    def __init__(self, turn_registry: TurnRegistry, client_registry: ClientRegistry) -> None:
        self._turns = turn_registry
        self._clients = client_registry

    async def dispatch(self, frame: dict) -> None:
        ftype = frame.get("type")
        if ftype == "res":
            await self._handle_res(frame)
        elif ftype == "event":
            await self._handle_event(frame)

    async def _handle_res(self, frame: dict) -> None:
        payload = frame.get("payload", {})
        if payload.get("status") == "started":
            return
        q = self._turns.get_queue_by_req(frame.get("id", ""))
        if q is not None:
            await q.put(("done", frame))

    async def _handle_event(self, frame: dict) -> None:
        event = frame.get("event", "")
        payload = frame.get("payload", {})
        if event == "chat":
            await self._handle_chat(payload)
        elif event == "agent":
            await self._handle_agent_lifecycle(payload)

    async def _handle_chat(self, payload: dict) -> None:
        sk = payload.get("sessionKey")
        if sk is None:
            return
        q = self._turns.get_queue_by_session(sk)
        if q is not None:
            await q.put(("chat", payload))
            return
        client_id = client_id_from_session_key(sk)
        bridge = self._clients.get(client_id)
        if bridge is not None:
            await bridge.deliver_push(payload)

    async def _handle_agent_lifecycle(self, payload: dict) -> None:
        sk = payload.get("sessionKey")
        if sk is None:
            return
        phase = payload.get("data", {}).get("phase")
        client_id = client_id_from_session_key(sk)
        bridge = self._clients.get(client_id)
        if bridge is None:
            return
        if phase == "start":
            await bridge.on_push_turn_start(sk)
        elif phase == "end":
            await bridge.on_push_turn_end(sk)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest tests/unit/test_openclaw_dispatcher.py -v
```

Expected: 9 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/services/openclaw/dispatcher.py tests/unit/test_openclaw_dispatcher.py
git commit -m "feat(openclaw): FrameDispatcher routes frames to TurnRegistry and ClientRegistry"
```

---

## Task 5: `client.py` — new OpenClawClient

**Files:**
- Create: `src/services/openclaw/client.py`
- Rewrite: `tests/integration/test_openclaw_client.py`

**Interfaces:**
- Consumes: `GatewayInfo` (Task 2), `TurnRegistry` (Task 3), `FrameDispatcher` (Task 4)
- Produces:
  - `OpenClawClient(host, port, token, turn_registry, dispatcher)`
  - `await client.connect() -> GatewayInfo`
  - `await client.close()`
  - `await client.ping() -> bool`
  - `async for event in client.stream_response(text, user_id, session_key=...) -> OrchestratorEvent`
  - `client.gateway_info: GatewayInfo | None`
  - `client.on_disconnect: Callable | None`

- [ ] **Step 1: Write the test helper (SmartFakeWS)**

At the top of `tests/integration/test_openclaw_client.py` (full file — replace existing):

```python
import asyncio
import json
import uuid
from typing import Optional
from unittest.mock import patch, AsyncMock

import pytest

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import TurnRegistry, ClientRegistry
from src.services.protocol import OrchestratorEvent

HELLO_OK_PAYLOAD = {
    "type": "hello-ok", "protocol": 4,
    "server": {"version": "2026.6.6", "connId": "test-conn"},
    "policy": {"tickIntervalMs": 30000, "maxPayload": 26214400, "maxBufferedBytes": 0},
    "snapshot": {
        "defaultAgentId": "main",
        "agents": [
            {"agentId": "main", "name": "Main Agent", "isDefault": True, "heartbeat": {}},
            {"agentId": "assistant", "name": "Jota Voice", "isDefault": False, "heartbeat": {}},
        ],
        "sessionDefaults": {"defaultAgentId": "main"},
    },
    "auth": {"role": "operator", "scopes": ["operator.read", "operator.write"]},
}


class SmartFakeWS:
    """Queue-backed fake WebSocket that auto-responds to OpenClaw protocol v4.

    chat_responses: {sessionKey → [list of deltaText strings]}
    """

    def __init__(self, chat_responses: Optional[dict] = None):
        self.chat_responses: dict[str, list[str]] = chat_responses or {}
        self._to_client: asyncio.Queue = asyncio.Queue()
        self._from_client: asyncio.Queue = asyncio.Queue()
        self.sent_frames: list[dict] = []
        self.closed = False
        self._handler: Optional[asyncio.Task] = None

    async def start(self):
        self._handler = asyncio.create_task(self._auto_respond())

    async def recv(self) -> str:
        msg = await self._to_client.get()
        if msg is None:
            raise Exception("connection closed")
        return msg

    async def send(self, data: str) -> None:
        frame = json.loads(data)
        self.sent_frames.append(frame)
        await self._from_client.put(data)

    async def close(self) -> None:
        self.closed = True
        await self._to_client.put(None)
        if self._handler:
            self._handler.cancel()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        msg = await self._to_client.get()
        if msg is None:
            raise StopAsyncIteration
        return msg

    async def _auto_respond(self):
        # 1. Send challenge
        await self._to_client.put(json.dumps({
            "type": "event", "event": "connect.challenge",
            "payload": {"nonce": "test-nonce", "ts": 1234567890},
        }))

        while True:
            raw = await self._from_client.get()
            frame = json.loads(raw)
            method = frame.get("method")
            req_id = frame.get("id", "")
            params = frame.get("params", {})

            if method == "connect":
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": HELLO_OK_PAYLOAD,
                }))

            elif method == "sessions.subscribe":
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"subscribed": True},
                }))

            elif method == "chat.send":
                sk = params.get("sessionKey", "")
                run_id = str(uuid.uuid4())
                chunks = self.chat_responses.get(sk, ["Hello"])

                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"runId": run_id, "status": "started"},
                }))
                for i, chunk in enumerate(chunks):
                    await self._to_client.put(json.dumps({
                        "type": "event", "event": "chat",
                        "payload": {
                            "runId": run_id, "sessionKey": sk,
                            "seq": i + 1, "state": "delta", "deltaText": chunk,
                        },
                    }))
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"status": "done", "runId": run_id},
                }))

            elif method == "health":
                await self._to_client.put(json.dumps({
                    "type": "res", "id": req_id, "ok": True,
                    "payload": {"ok": True},
                }))

            elif method == "chat.abort":
                pass


def make_client(fake_ws: SmartFakeWS) -> OpenClawClient:
    turn_reg = TurnRegistry()
    client_reg = ClientRegistry()
    dispatcher = FrameDispatcher(turn_reg, client_reg)
    client = OpenClawClient("127.0.0.1", 18789, "test-token", turn_reg, dispatcher)
    return client


@pytest.fixture
def fake_ws():
    return SmartFakeWS({"agent:main:client-a": ["Hola ", "mundo"]})


async def connected_client(fake_ws: SmartFakeWS) -> OpenClawClient:
    client = make_client(fake_ws)
    await fake_ws.start()
    with patch("websockets.connect", return_value=fake_ws):
        await client.connect()
    return client
```

- [ ] **Step 2: Write the failing tests (append to same file)**

```python
@pytest.mark.asyncio
async def test_connect_returns_gateway_info(fake_ws):
    client = await connected_client(fake_ws)
    assert client.gateway_info is not None
    assert client.gateway_info.default_agent_id == "main"
    assert client.gateway_info.has_agent("assistant")
    await client.close()


@pytest.mark.asyncio
async def test_connect_sends_sessions_subscribe(fake_ws):
    client = await connected_client(fake_ws)
    methods = [f["method"] for f in fake_ws.sent_frames]
    assert "sessions.subscribe" in methods
    await client.close()


@pytest.mark.asyncio
async def test_stream_response_tokens(fake_ws):
    client = await connected_client(fake_ws)
    tokens = []
    async for event in client.stream_response(
        "hola", "client-a", session_key="agent:main:client-a"
    ):
        if event.type == "token":
            tokens.append(event.content)
    assert tokens == ["Hola ", "mundo"]
    await client.close()


@pytest.mark.asyncio
async def test_stream_response_ends_with_status_done(fake_ws):
    client = await connected_client(fake_ws)
    events = []
    async for event in client.stream_response(
        "hola", "client-a", session_key="agent:main:client-a"
    ):
        events.append(event)
    assert events[-1].type == "status"
    assert events[-1].content == "done"
    await client.close()


@pytest.mark.asyncio
async def test_stream_response_uses_sessionKey_format(fake_ws):
    client = await connected_client(fake_ws)
    async for _ in client.stream_response(
        "hola", "client-a", session_key="agent:main:client-a"
    ):
        pass
    chat_sends = [f for f in fake_ws.sent_frames if f.get("method") == "chat.send"]
    assert len(chat_sends) == 1
    assert "sessionKey" in chat_sends[0]["params"]
    assert "session" not in chat_sends[0]["params"]
    await client.close()


@pytest.mark.asyncio
async def test_ping_returns_true_when_connected(fake_ws):
    client = await connected_client(fake_ws)
    result = await client.ping()
    assert result is True
    await client.close()


@pytest.mark.asyncio
async def test_ping_returns_false_when_not_connected():
    client = make_client(SmartFakeWS())
    result = await client.ping()
    assert result is False


@pytest.mark.asyncio
async def test_session_key_required(fake_ws):
    client = await connected_client(fake_ws)
    with pytest.raises(ValueError, match="session_key is required"):
        async for _ in client.stream_response("hola", "client-a"):
            pass
    await client.close()


@pytest.mark.asyncio
async def test_on_disconnect_called_on_ws_drop(fake_ws):
    client = await connected_client(fake_ws)
    disconnected = []
    client.on_disconnect = lambda: disconnected.append(True)
    await fake_ws.close()
    await asyncio.sleep(0.05)
    assert disconnected == [True]
```

- [ ] **Step 3: Run to verify failures**

```bash
PYTHONPATH=. pytest tests/integration/test_openclaw_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.services.openclaw.client'`

- [ ] **Step 4: Implement `client.py`**

Create `src/services/openclaw/client.py`:

```python
import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Callable, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.models import GatewayInfo
from src.services.openclaw.registry import TurnRegistry
from src.services.protocol import OrchestratorEvent

logger = logging.getLogger(__name__)


class OpenClawClient:
    """Single persistent WebSocket to OpenClaw, multiplexed across all sessions.

    _listen receives every frame and calls dispatcher.dispatch() — no routing logic here.
    Health pings bypass the dispatcher via _health_futures (they have no session key).
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        turn_registry: TurnRegistry,
        dispatcher: FrameDispatcher,
    ) -> None:
        self._uri = f"ws://{host}:{port}"
        self._token = token
        self._turn_registry = turn_registry
        self._dispatcher = dispatcher
        self._ws: Optional[ClientConnection] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._health_futures: dict[str, asyncio.Future] = {}
        self.gateway_info: Optional[GatewayInfo] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

    async def connect(self) -> GatewayInfo:
        self._ws = await websockets.connect(self._uri)

        raw = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
        frame = json.loads(raw)
        if frame.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected connect.challenge, got: {frame}")

        req_id = str(uuid.uuid4())
        await self._ws.send(json.dumps({
            "type": "req", "id": req_id, "method": "connect",
            "params": {
                "minProtocol": 3, "maxProtocol": 4,
                "client": {
                    "id": "gateway-client", "version": "1.0.0",
                    "platform": "linux", "mode": "backend",
                },
                "role": "operator",
                "scopes": ["operator.read", "operator.write"],
                "auth": {"token": self._token},
            },
        }))

        raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
        hello = json.loads(raw)
        if not hello.get("ok"):
            raise RuntimeError(f"OpenClaw handshake failed: {hello.get('error')}")
        self.gateway_info = GatewayInfo.from_hello_ok(hello.get("payload", {}))

        sub_id = str(uuid.uuid4())
        await self._ws.send(json.dumps({
            "type": "req", "id": sub_id, "method": "sessions.subscribe", "params": {},
        }))

        self._listener_task = asyncio.create_task(self._listen())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.info(
            f"OpenClawClient connected → {self._uri} "
            f"(tick {self.gateway_info.tick_interval_ms}ms, "
            f"default_agent={self.gateway_info.default_agent_id})"
        )
        return self.gateway_info

    async def close(self) -> None:
        for task in (self._keepalive_task, self._listener_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._keepalive_task = None
        self._listener_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def ping(self) -> bool:
        if not self._ws:
            return False
        req_id = str(uuid.uuid4())
        try:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._health_futures[req_id] = fut
            await self._ws.send(json.dumps({
                "type": "req", "id": req_id, "method": "health", "params": {},
            }))
            res = await asyncio.wait_for(fut, timeout=5.0)
            return res.get("ok", False)
        except Exception as e:
            logger.debug(f"ping failed: {e}")
            self._health_futures.pop(req_id, None)
            return False

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]:
        if not self._ws:
            yield OrchestratorEvent(type="error", content="not connected")
            return
        if not session_key:
            raise ValueError("session_key is required — callers must provide it via make_session_key()")

        req_id = str(uuid.uuid4())
        queue = self._turn_registry.register(req_id, session_key)
        _sent = False
        _finished = False

        try:
            try:
                await self._ws.send(json.dumps({
                    "type": "req", "id": req_id, "method": "chat.send",
                    "params": {
                        "sessionKey": session_key,
                        "message": text,
                        "idempotencyKey": str(uuid.uuid4()),
                    },
                }))
                _sent = True
            except Exception as e:
                yield OrchestratorEvent(type="error", content=f"send failed: {e}")
                _finished = True
                return

            while True:
                kind, data = await queue.get()
                if kind == "chat":
                    delta = data.get("deltaText", "")
                    if delta:
                        yield OrchestratorEvent(type="token", content=delta)
                elif kind == "done":
                    if not data.get("ok"):
                        yield OrchestratorEvent(type="error", content=str(data.get("error", {})))
                    else:
                        yield OrchestratorEvent(type="status", content="done")
                    _finished = True
                    break
                elif kind == "error":
                    yield OrchestratorEvent(type="error", content=str(data))
                    _finished = True
                    break
        finally:
            self._turn_registry.unregister(session_key, req_id)
            if _sent and not _finished and self._ws:
                try:
                    await asyncio.shield(self._ws.send(json.dumps({
                        "type": "req", "id": str(uuid.uuid4()),
                        "method": "chat.abort",
                        "params": {"sessionKey": session_key},
                    })))
                except Exception:
                    pass

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                frame = json.loads(raw)
                fid = frame.get("id", "")
                if fid in self._health_futures:
                    fut = self._health_futures.pop(fid)
                    if not fut.done():
                        fut.set_result(frame)
                    continue
                await self._dispatcher.dispatch(frame)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"_listen error: {e}")
            self._turn_registry.error_all(str(e))
        if self.on_disconnect:
            self.on_disconnect()

    async def _keepalive_loop(self) -> None:
        interval = self.gateway_info.tick_interval_ms * 0.8 / 1000 if self.gateway_info else 12.0
        try:
            while True:
                await asyncio.sleep(interval)
                if self._ws:
                    await self.ping()
        except asyncio.CancelledError:
            return
```

- [ ] **Step 5: Run tests**

```bash
PYTHONPATH=. pytest tests/integration/test_openclaw_client.py -v
```

Expected: 9 PASSED

- [ ] **Step 6: Run full suite**

```bash
PYTHONPATH=. pytest -x -q
```

Expected: all passing.

- [ ] **Step 7: Commit**

```bash
git add src/services/openclaw/client.py tests/integration/test_openclaw_client.py
git commit -m "feat(openclaw): OpenClawClient — multiplexed concurrent sessions via TurnRegistry"
```

---

## Task 6: `reconnecting.py`

**Files:**
- Create: `src/services/openclaw/reconnecting.py`
- Rewrite: `tests/integration/test_reconnecting_openclaw.py` (rename from `test_reconnecting_orchestrator.py`)

**Interfaces:**
- Consumes: `OpenClawClient` (Task 5), `OrchestratorProtocol` (Task 1)
- Produces: `ReconnectingOpenClawClient` implementing `OrchestratorProtocol`
  - `.connect()`, `.close()`, `.ping()`, `.stream_response(...)`
  - `.gateway_info: GatewayInfo | None`
  - `.status() -> OrchestratorStatus`

- [ ] **Step 1: Write failing tests**

Create `tests/integration/test_reconnecting_openclaw.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient, OrchestratorState
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.models import GatewayInfo
from src.services.protocol import OrchestratorEvent


def make_mock_client(gateway_info=None):
    client = AsyncMock(spec=OpenClawClient)
    client.gateway_info = gateway_info
    client.on_disconnect = None

    async def fake_connect():
        if gateway_info:
            client.gateway_info = gateway_info
        return gateway_info

    client.connect.side_effect = fake_connect

    async def fake_stream(*args, **kwargs):
        yield OrchestratorEvent(type="token", content="hi")
        yield OrchestratorEvent(type="status", content="done")

    client.stream_response = fake_stream
    return client


GATEWAY_INFO = GatewayInfo(
    protocol_version=4, server_version="2026.6.6", conn_id="c1",
    default_agent_id="main", agents={}, tick_interval_ms=30000, max_payload=26214400,
)


@pytest.mark.asyncio
async def test_connect_sets_connected_state():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    await roc.connect()
    assert roc.state == OrchestratorState.CONNECTED
    assert roc.gateway_info is GATEWAY_INFO


@pytest.mark.asyncio
async def test_stream_response_delegates():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    await roc.connect()
    events = []
    async for e in roc.stream_response("hello", "user", session_key="agent:main:u"):
        events.append(e)
    assert any(e.type == "token" for e in events)


@pytest.mark.asyncio
async def test_stream_response_when_not_connected_yields_error():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    # NOT calling connect() first
    events = []
    async for e in roc.stream_response("hello", "user", session_key="agent:main:u"):
        events.append(e)
    assert events[0].type == "error"
    assert "unavailable" in events[0].content


@pytest.mark.asyncio
async def test_disconnect_triggers_reconnect():
    inner = make_mock_client(GATEWAY_INFO)
    roc = ReconnectingOpenClawClient(inner, "test")
    await roc.connect()
    inner._on_disconnect_cb()  # simulate disconnect
    await asyncio.sleep(0.05)
    assert inner.connect.call_count >= 2  # initial + at least one retry


@pytest.mark.asyncio
async def test_reconnect_exhausted_enters_degraded():
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None
    inner.connect.side_effect = RuntimeError("refused")
    roc = ReconnectingOpenClawClient(inner, "test", max_duration=0.1, initial_backoff=0.05)
    await roc.connect()  # this will fail → starts reconnect loop
    await asyncio.sleep(0.3)
    assert roc.state == OrchestratorState.DEGRADED
```

- [ ] **Step 2: Run to verify failures**

```bash
PYTHONPATH=. pytest tests/integration/test_reconnecting_openclaw.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement `reconnecting.py`**

Create `src/services/openclaw/reconnecting.py`:

```python
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Optional

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.models import GatewayInfo
from src.services.protocol import OrchestratorEvent

logger = logging.getLogger(__name__)


class OrchestratorState(Enum):
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DEGRADED = "DEGRADED"


@dataclass
class OrchestratorStatus:
    name: str
    state: OrchestratorState
    connected_at: Optional[datetime]
    reconnect_attempts: int
    last_error: Optional[str]


class ReconnectingOpenClawClient:
    """Wraps OpenClawClient with exponential backoff reconnection.

    Implements OrchestratorProtocol so JotaBridge needs no changes.
    Exposes gateway_info after successful connect.
    """

    def __init__(
        self,
        client: OpenClawClient,
        name: str,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        max_duration: float = 300.0,
    ) -> None:
        self._client = client
        self._name = name
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._max_duration = max_duration
        self.state = OrchestratorState.DEGRADED
        self.gateway_info: Optional[GatewayInfo] = None
        self._connected_at: Optional[datetime] = None
        self._reconnect_attempts: int = 0
        self._last_error: Optional[str] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        # Expose the callback slot so tests can trigger it
        self._client.on_disconnect = self._handle_disconnect

    @property
    def _on_disconnect_cb(self):
        return self._handle_disconnect

    async def connect(self) -> None:
        try:
            self.gateway_info = await self._client.connect()
            self.state = OrchestratorState.CONNECTED
            self._connected_at = datetime.now(timezone.utc)
            self._reconnect_attempts = 0
            self._last_error = None
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"[{self._name}] initial connect failed: {e} — starting retry")
            self._ensure_reconnecting()

    async def close(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        await self._client.close()

    async def ping(self) -> bool:
        if self.state != OrchestratorState.CONNECTED:
            if self.state == OrchestratorState.DEGRADED:
                self._ensure_reconnecting()
            return False
        return await self._client.ping()

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]:
        if self.state != OrchestratorState.CONNECTED:
            if self.state == OrchestratorState.DEGRADED:
                self._ensure_reconnecting()
            yield OrchestratorEvent(type="error", content="orchestrator_unavailable")
            return
        try:
            async for event in self._client.stream_response(
                text=text, user_id=user_id, model_id=model_id,
                system_prompt_extra=system_prompt_extra, session_key=session_key,
            ):
                yield event
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[{self._name}] stream_response error: {e}")
            yield OrchestratorEvent(type="error", content=str(e))

    def status(self) -> OrchestratorStatus:
        return OrchestratorStatus(
            name=self._name,
            state=self.state,
            connected_at=self._connected_at,
            reconnect_attempts=self._reconnect_attempts,
            last_error=self._last_error,
        )

    def _handle_disconnect(self) -> None:
        self._ensure_reconnecting()

    def _ensure_reconnecting(self) -> None:
        if not self._reconnect_task or self._reconnect_task.done():
            self.state = OrchestratorState.RECONNECTING
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        start = time.monotonic()
        backoff = self._initial_backoff
        while True:
            try:
                self.gateway_info = await self._client.connect()
                self.state = OrchestratorState.CONNECTED
                self._connected_at = datetime.now(timezone.utc)
                self._reconnect_attempts = 0
                logger.info(f"[{self._name}] reconnected.")
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._reconnect_attempts += 1
                self._last_error = str(e)
                logger.warning(f"[{self._name}] reconnect attempt {self._reconnect_attempts} failed: {e}")

            if time.monotonic() - start >= self._max_duration:
                self.state = OrchestratorState.DEGRADED
                logger.warning(f"[{self._name}] reconnect exhausted — DEGRADED.")
                return

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)
```

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=. pytest tests/integration/test_reconnecting_openclaw.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/services/openclaw/reconnecting.py tests/integration/test_reconnecting_openclaw.py
git commit -m "feat(openclaw): ReconnectingOpenClawClient with backoff reconnect"
```

---

## Task 7: `bridge.py` — push support

**Files:**
- Modify: `src/services/bridge.py`
- Create: `tests/unit/test_bridge_push.py`

**Interfaces:**
- Consumes: `ClientRegistry` (Task 3)
- Produces (new in `JotaBridge`):
  - `__init__(..., client_registry: ClientRegistry, default_agent: str)`
  - `async on_push_turn_start(session_key: str) -> None`
  - `async deliver_push(payload: dict) -> None`
  - `async on_push_turn_end(session_key: str) -> None`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_bridge_push.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.models.schemas import Client, ClientConfig, Handshake


def make_bridge(output_mode=("text",), client_id="hab_sito"):
    client = Client(id=client_id, client_key="key-123", is_active=True)
    config = ClientConfig()
    ws = AsyncMock()
    orchestrator = AsyncMock()
    orchestrator.ping = AsyncMock(return_value=True)
    tracker = AsyncMock()
    handshake = Handshake(
        client_key="key-123",
        input_mode="text",
        output_mode=list(output_mode),
        agent="main",
    )
    client_registry = ClientRegistry()
    bridge = JotaBridge(
        client=client, config=config, client_ws=ws,
        orchestrator=orchestrator, tracker=tracker, handshake=handshake,
        client_registry=client_registry, default_agent="main",
    )
    return bridge, client_registry


@pytest.mark.asyncio
async def test_connect_registers_in_client_registry():
    bridge, registry = make_bridge()
    await bridge.connect_internal_services()
    assert registry.get("hab_sito") is bridge


@pytest.mark.asyncio
async def test_close_unregisters_from_client_registry():
    bridge, registry = make_bridge()
    await bridge.connect_internal_services()
    bridge.tracker.close = AsyncMock()
    await bridge.close_all()
    assert registry.get("hab_sito") is None


@pytest.mark.asyncio
async def test_deliver_push_text_sends_push_message():
    bridge, _ = make_bridge(output_mode=("text",))
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": "Buenos días!"}
    await bridge.deliver_push(payload)
    bridge.client_ws.send_json.assert_awaited_once_with({
        "type": "push", "content": "Buenos días!"
    })


@pytest.mark.asyncio
async def test_deliver_push_empty_delta_ignored():
    bridge, _ = make_bridge(output_mode=("text",))
    payload = {"sessionKey": "agent:main:hab_sito", "deltaText": ""}
    await bridge.deliver_push(payload)
    bridge.client_ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_push_turn_start_no_audio_does_nothing():
    bridge, _ = make_bridge(output_mode=("text",))
    await bridge.on_push_turn_start("agent:main:hab_sito")
    assert bridge._push_tts is None


@pytest.mark.asyncio
async def test_on_push_turn_end_closes_push_tts():
    bridge, _ = make_bridge(output_mode=("audio", "text"))
    mock_tts = AsyncMock()
    bridge._push_tts = mock_tts
    await bridge.on_push_turn_end("agent:main:hab_sito")
    mock_tts.end.assert_awaited_once()
    mock_tts.close.assert_awaited_once()
    assert bridge._push_tts is None
```

- [ ] **Step 2: Run to verify failures**

```bash
PYTHONPATH=. pytest tests/unit/test_bridge_push.py -v
```

Expected: failures about missing `client_registry` param and missing methods.

- [ ] **Step 3: Update `bridge.py`**

Make these changes to `src/services/bridge.py`:

**a) Update imports:**
```python
# Replace:
from src.services.orchestrators.protocol import OrchestratorProtocol
# With:
from src.services.protocol import OrchestratorProtocol
from src.services.openclaw.registry import ClientRegistry
```

**b) Update `__init__` signature** (add two params at the end):
```python
def __init__(
    self,
    client: Client,
    config: ClientConfig,
    client_ws: WebSocket,
    orchestrator: OrchestratorProtocol,
    tracker: PipelineTracker,
    handshake: Handshake,
    client_registry: ClientRegistry,
    default_agent: str,
):
    self.client = client
    self.config = config
    self.client_id = client.id
    self.client_ws = client_ws
    self.handshake: Handshake = handshake
    self.orchestrator: OrchestratorProtocol = orchestrator
    self.tracker: PipelineTracker = tracker
    self._client_registry = client_registry
    self._default_agent = default_agent
    self.transcriber: Optional[TranscriberClient] = None
    self._push_tts = None
    self.tasks: list[asyncio.Task] = []
    self._active_turn: Optional[asyncio.Task] = None
    self._session_start: float = 0.0
    self._first_audio_at: Optional[float] = None
    self._last_final_text: Optional[str] = None
```

**c) Update `connect_internal_services`** — add registration at the end:
```python
async def connect_internal_services(self):
    connect_tasks = []
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
    self._client_registry.register(self.client_id, self)
```

**d) Update `close_all`** — unregister at the start, before awaiting the active turn:
```python
async def close_all(self):
    self._client_registry.unregister(self.client_id)
    # ... rest of close_all unchanged ...
```

**e) Update `_call_orchestrator`** — replace `settings.OPENCLAW_DEFAULT_AGENT`:
```python
# Replace this line:
agent = self.handshake.agent or settings.OPENCLAW_DEFAULT_AGENT
# With:
agent = self.handshake.agent or self._default_agent
```

**f) Add push methods** at the end of the class:
```python
async def on_push_turn_start(self, session_key: str) -> None:
    if "audio" not in self.handshake.output_mode:
        return
    tts = TTSClient(
        url=settings.TTS_WS_URL, token=settings.TTS_TOKEN, client_id=self.client_id
    )
    try:
        await tts.connect(voice=self.config.tts_voice, speed=self.config.tts_speed)
        self._push_tts = tts
    except Exception as e:
        logger.warning(f"[{self.client_id}] Push TTS unavailable: {e}")

async def deliver_push(self, payload: dict) -> None:
    delta = payload.get("deltaText", "")
    if not delta:
        return
    if "text" in self.handshake.output_mode:
        try:
            await self.client_ws.send_json({"type": "push", "content": delta})
        except Exception:
            pass
    if self._push_tts:
        await self._push_tts.send_text_chunk(delta)

async def on_push_turn_end(self, session_key: str) -> None:
    if self._push_tts:
        try:
            await self._push_tts.end()
            await self._push_tts.close()
        except Exception:
            pass
        self._push_tts = None
```

- [ ] **Step 4: Run push tests**

```bash
PYTHONPATH=. pytest tests/unit/test_bridge_push.py -v
```

Expected: 7 PASSED

- [ ] **Step 5: Run full test suite**

```bash
PYTHONPATH=. pytest -x -q
```

The existing bridge unit tests (`test_bridge_disconnect.py`, `test_bridge_input_loop.py`, `test_bridge_send_guards.py`) will fail because they construct `JotaBridge` without the new params. Fix each one by adding to their `JotaBridge(...)` call:
```python
client_registry=ClientRegistry(),
default_agent="main",
```

And add the import at the top of each file:
```python
from src.services.openclaw.registry import ClientRegistry
```

- [ ] **Step 6: Commit**

```bash
git add src/services/bridge.py tests/unit/test_bridge_push.py tests/unit/test_bridge_*.py
git commit -m "feat(bridge): push delivery hooks + client_registry registration + default_agent param"
```

---

## Task 8: Wire — `main.py`, `routes.py`, `config.py`

**Files:**
- Modify: `src/main.py`
- Modify: `src/api/routes.py`
- Modify: `src/core/config.py`
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_rest_openai.py` and any test using `app.state.orchestrators`

**Interfaces:**
- Consumes: `ReconnectingOpenClawClient` (Task 6), `TurnRegistry`, `ClientRegistry` (Task 3)
- Produces: `app.state.openclaw`, `app.state.turn_registry`, `app.state.client_registry` (alongside existing `app.state.session_registry`)

- [ ] **Step 1: Update `src/core/config.py`**

Remove `OPENCLAW_DEFAULT_AGENT` and `DEFAULT_ORCHESTRATOR`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    JOTA_DB_BASE_URL: str = "localhost:8001"
    JOTA_DB_API_KEY: str = ""
    TRANSCRIBER_WS_URL: str = "localhost:9000"
    TTS_WS_URL: str = "localhost:8005"
    TTS_TOKEN: str = "gateway"
    OPENCLAW_HOST: str = "127.0.0.1"
    OPENCLAW_PORT: int = 18789
    OPENCLAW_TOKEN: str = ""
    ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF: float = 1.0
    ORCHESTRATOR_RECONNECT_MAX_BACKOFF: float = 60.0
    ORCHESTRATOR_RECONNECT_MAX_DURATION: float = 300.0
    TRANSCRIBER_SILENCE_TIMEOUT_S: int = 25

settings = Settings()
```

- [ ] **Step 2: Update `src/main.py`**

Replace the lifespan and imports:

```python
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.api.routes import router as stream_router
from src.api.openai_routes import router as openai_router
from src.api.health_routes import router as health_router
from src.api.orchestrator_routes import router as orchestrator_router
from src.api.sessions_routes import router as sessions_router
from src.core.config import settings
from src.services.db_client import db_client
from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import TurnRegistry, ClientRegistry
from src.services.session_registry import SessionRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_client.connect()

    turn_registry = TurnRegistry()
    client_registry = ClientRegistry()
    dispatcher = FrameDispatcher(turn_registry, client_registry)
    inner = OpenClawClient(
        host=settings.OPENCLAW_HOST,
        port=settings.OPENCLAW_PORT,
        token=settings.OPENCLAW_TOKEN,
        turn_registry=turn_registry,
        dispatcher=dispatcher,
    )
    openclaw = ReconnectingOpenClawClient(
        inner,
        name="openclaw",
        initial_backoff=settings.ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF,
        max_backoff=settings.ORCHESTRATOR_RECONNECT_MAX_BACKOFF,
        max_duration=settings.ORCHESTRATOR_RECONNECT_MAX_DURATION,
    )
    try:
        await openclaw.connect()
    except Exception as e:
        logger.error(f"Initial OpenClaw connect failed: {e}")

    app.state.openclaw = openclaw
    app.state.turn_registry = turn_registry
    app.state.client_registry = client_registry
    app.state.session_registry = SessionRegistry()

    yield

    await openclaw.close()
    await db_client.close()


app = FastAPI(
    title="JotaGateway (BFF)",
    description="Backend For Frontend — OpenClaw gateway with push routing.",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(stream_router)
app.include_router(openai_router)
app.include_router(health_router, prefix="/api")
app.include_router(orchestrator_router, prefix="/api")
app.include_router(sessions_router, prefix="/api")


@app.get("/health")
def healthcheck():
    return {"status": "online", "service": "JotaGateway BFF"}
```

Note: `config_router`, `conversation_router`, `models_router` are removed (jota-db proxy — Plan 2 cleanup).

- [ ] **Step 3: Update `src/api/routes.py`**

Replace the orchestrator retrieval and `JotaBridge` construction:

```python
# Replace:
try:
    orchestrator = websocket.scope["app"].state.orchestrators.default()
except KeyError as e:
    logger.error(f"[{client.id}] No hay orquestador disponible: {e}")
    await websocket.close(code=1011, reason="No orchestrator available.")
    return

# With:
app_state = websocket.scope["app"].state
openclaw = app_state.openclaw

# Validate agent from hello-ok agent list
requested_agent = handshake.agent
if requested_agent and not openclaw.gateway_info.has_agent(requested_agent):
    logger.warning(f"[{client.id}] Requested agent '{requested_agent}' not in OpenClaw")
    await websocket.close(code=1008, reason=f"Agent '{requested_agent}' not available.")
    return

default_agent = openclaw.gateway_info.default_agent_id if openclaw.gateway_info else "main"
```

Then update the `JotaBridge` constructor call:
```python
bridge = JotaBridge(
    client=client,
    config=config,
    client_ws=websocket,
    orchestrator=openclaw,
    tracker=tracker,
    handshake=handshake,
    client_registry=app_state.client_registry,
    default_agent=default_agent,
)
```

- [ ] **Step 4: Update integration test fixtures**

In `tests/integration/conftest.py`, find the fixture that provides the app state for the orchestrator and replace it. Search for `orchestrators` references:

```bash
grep -n "orchestrators\|build_registry\|DEFAULT_ORCHESTRATOR\|OPENCLAW_DEFAULT_AGENT" tests/ -r --include="*.py"
```

For each file found, replace `app.state.orchestrators` with `app.state.openclaw`. The mock orchestrator in tests should be a `ReconnectingOpenClawClient` mock or a plain `AsyncMock` that satisfies `OrchestratorProtocol`.

In `conftest.py` the app fixture typically creates the app and sets state. Update it to set:
```python
app.state.openclaw = mock_orchestrator
app.state.turn_registry = TurnRegistry()
app.state.client_registry = ClientRegistry()
```

- [ ] **Step 5: Run full suite**

```bash
PYTHONPATH=. pytest -x -q 2>&1 | head -60
```

Fix any remaining import errors or missing fixture references. Common fixes:
- Any test importing `from src.services.orchestrators.*` → update to `src.services.openclaw.*` or `src.services.protocol`
- Any test using `app.state.orchestrators.default()` → `app.state.openclaw`
- Any test checking `OPENCLAW_DEFAULT_AGENT` → use `"main"` directly or `openclaw.gateway_info.default_agent_id`

- [ ] **Step 6: Commit**

```bash
git add src/main.py src/api/routes.py src/core/config.py
git add tests/integration/conftest.py tests/integration/test_rest_openai.py
git commit -m "feat: wire ReconnectingOpenClawClient into app lifespan, update routes + config"
```

---

## Task 9: Delete `orchestrators/` and clean up

**Files:**
- Delete: `src/services/orchestrators/` (entire directory)
- Delete: `tests/integration/test_orchestrator_registry.py`
- Delete: `tests/integration/test_reconnecting_orchestrator.py` (superseded by Task 6's file)
- Modify: `.env` (remove `OPENCLAW_DEFAULT_AGENT`)
- Modify: `.env.example` if present

**Interfaces:**
- None — pure deletion and cleanup

- [ ] **Step 1: Verify no remaining imports from orchestrators/**

```bash
grep -r "services.orchestrators" src/ tests/ --include="*.py"
```

Expected: zero results. If any remain, fix them before proceeding.

- [ ] **Step 2: Delete orchestrators directory**

```bash
rm -rf src/services/orchestrators/
```

- [ ] **Step 3: Delete superseded test files**

```bash
rm -f tests/integration/test_orchestrator_registry.py
rm -f tests/integration/test_reconnecting_orchestrator.py
```

- [ ] **Step 4: Remove settings from .env**

Remove these lines from `.env`:
```
OPENCLAW_DEFAULT_AGENT=assistant
DEFAULT_ORCHESTRATOR=openclaw   # if present
```

If `.env.example` exists, remove the same lines from it.

- [ ] **Step 5: Run full suite**

```bash
PYTHONPATH=. pytest -q
```

Expected: all tests pass, no import errors.

- [ ] **Step 6: Run linter**

```bash
ruff check src/ tests/
```

Fix any reported issues.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: remove orchestrators/ dir, delete superseded tests, clean .env settings"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|-----------------|------|
| Single WS connection, multiplexed | Task 5 (`client.py`) |
| `sessions.subscribe` on connect | Task 5 (`connect()`) |
| `TurnRegistry` replaces `_active_req_id` | Task 3 + Task 5 |
| `client_id` = last segment after last `:` | Task 3 (`client_id_from_session_key`) |
| Default agent from `hello-ok` | Task 2 (`GatewayInfo.default_agent_id`) + Task 7 |
| `openclaw/` package, files under 200 lines | Tasks 1-6 |
| Push: `deliver_push`, `on_push_turn_start`, `on_push_turn_end` | Task 7 |
| `ClientRegistry` registration in bridge | Task 7 |
| Agent validation in Handshake | Task 8 (`routes.py`) |
| `chat.send` uses `sessionKey` | Task 5 |
| `chat.abort` uses `sessionKey` | Task 5 (in `stream_response` finally) |
| Remove `OPENCLAW_DEFAULT_AGENT` from settings | Task 8 |
| Remove `orchestrators/` | Task 9 |
| Push message type `"push"` (not `"text"`) | Task 7 |

All spec requirements covered. ✓

### Placeholder scan

No TBDs, TODOs, or "similar to Task N" references. ✓

### Type consistency

- `TurnRegistry.register(req_id, session_key) -> Queue` — used consistently in Task 5 (`client.py`) ✓
- `TurnRegistry.unregister(session_key, req_id)` — param order matches throughout ✓
- `client_id_from_session_key` — imported from `registry.py` in `dispatcher.py` ✓
- `GatewayInfo.from_hello_ok(payload)` — called in `client.py` with `hello.get("payload", {})` ✓
- `JotaBridge(..., client_registry, default_agent)` — new params added in Task 7, tests updated in Task 7 ✓
- `app.state.openclaw` (not `orchestrators`) — consistent in `routes.py` and `main.py` ✓

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-29-openclaw-multiplexed.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks sequentially in this session with checkpoints

**Which approach?**
