# Orchestrator Reconnect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic reconnection with configurable exponential backoff to orchestrator connections, surfacing state via REST endpoints.

**Architecture:** A `ReconnectingOrchestrator` wrapper transparently wraps any `OrchestratorProtocol` implementation, adding a state machine (CONNECTED → RECONNECTING → DEGRADED) with an exponential-backoff reconnect loop. `OpenClawClient` gains a single `on_disconnect` callback attribute that the wrapper sets at construction time. The registry wraps clients at build time and exposes `get_status()`/`reconnect()`. Two new REST endpoints expose state and trigger manual reconnection.

**Tech Stack:** Python 3.12, FastAPI, asyncio, pydantic-settings, pytest-asyncio (asyncio_mode=auto), respx, unittest.mock.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/core/config.py` | 3 new reconnect settings |
| **Create** | `src/services/orchestrators/reconnecting.py` | `OrchestratorState`, `OrchestratorStatus`, `ReconnectingOrchestrator` |
| Modify | `src/services/orchestrators/openclaw_client.py` | `on_disconnect` callback attribute + call in `_listen()` |
| Modify | `src/services/orchestrators/registry.py` | wrap each client in `ReconnectingOrchestrator`, add `get_status()`/`reconnect()` |
| **Create** | `src/api/orchestrator_routes.py` | `GET /api/orchestrators/{name}/status` + `POST /api/orchestrators/{name}/reconnect` |
| Modify | `src/main.py` | register new router |
| Modify | `tests/integration/conftest.py` | add `get_status`/`reconnect` mocks to `make_mock_registry` |
| **Create** | `tests/integration/test_reconnecting_orchestrator.py` | 7 unit-style async tests |
| **Create** | `tests/integration/test_orchestrator_routes.py` | 4 integration tests for new REST endpoints |

---

## Task 1: Add reconnect settings to config

**Files:**
- Modify: `src/core/config.py`

- [ ] **Step 1: Add the 3 settings fields**

  In `src/core/config.py`, add the three fields inside the `Settings` class, after the existing `OPENCLAW_TOKEN` field:

  ```python
  # Orchestrator reconnect policy
  ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF: float = 1.0   # seconds
  ORCHESTRATOR_RECONNECT_MAX_BACKOFF: float = 60.0      # seconds
  ORCHESTRATOR_RECONNECT_MAX_DURATION: float = 300.0    # seconds before entering DEGRADED
  ```

- [ ] **Step 2: Run lint**

  ```bash
  PYTHONPATH=. ruff check src/core/config.py
  ```
  Expected: no output (no errors).

- [ ] **Step 3: Commit**

  ```bash
  git add src/core/config.py
  git commit -m "feat(config): add orchestrator reconnect settings"
  ```

---

## Task 2: ReconnectingOrchestrator — state types + disconnect detection

Create `src/services/orchestrators/reconnecting.py` with the state enum, status dataclass, and the core wrapper (delegates + `_handle_disconnect`). Tests cover disconnect detection and error surfacing in non-CONNECTED states.

**Files:**
- Create: `src/services/orchestrators/reconnecting.py`
- Create: `tests/integration/test_reconnecting_orchestrator.py`

- [ ] **Step 1: Write the two failing tests**

  Create `tests/integration/test_reconnecting_orchestrator.py`:

  ```python
  import asyncio
  import pytest
  from datetime import datetime, timezone
  from unittest.mock import AsyncMock, MagicMock

  from src.services.orchestrators.protocol import OrchestratorEvent, OrchestratorProtocol
  from src.services.orchestrators.reconnecting import (
      OrchestratorState,
      ReconnectingOrchestrator,
  )


  def _make_client(connect_side_effect=None):
      client = MagicMock(spec=OrchestratorProtocol)
      client.connect = AsyncMock(side_effect=connect_side_effect)
      client.close = AsyncMock()
      client.ping = AsyncMock(return_value=True)

      async def _stream(*args, **kwargs):
          yield OrchestratorEvent(type="token", content="hi")

      client.stream_response = _stream
      return client


  async def test_disconnect_triggers_reconnecting_state():
      client = _make_client(connect_side_effect=OSError("refused"))
      wrapper = ReconnectingOrchestrator(client, name="test")
      wrapper._state = OrchestratorState.CONNECTED

      wrapper._handle_disconnect()

      assert wrapper._state == OrchestratorState.RECONNECTING
      if wrapper._reconnect_task:
          wrapper._reconnect_task.cancel()
          try:
              await wrapper._reconnect_task
          except asyncio.CancelledError:
              pass


  async def test_stream_response_while_reconnecting_yields_error():
      client = _make_client()
      wrapper = ReconnectingOrchestrator(client, name="test")
      wrapper._state = OrchestratorState.RECONNECTING

      events = [e async for e in wrapper.stream_response("hello", "user-1")]

      assert len(events) == 1
      assert events[0].type == "error"
      assert events[0].content == "orchestrator_unavailable"
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  PYTHONPATH=. pytest tests/integration/test_reconnecting_orchestrator.py -v
  ```
  Expected: `ImportError` or `ModuleNotFoundError` — `reconnecting` does not exist yet.

- [ ] **Step 3: Create `reconnecting.py` with state types and basic wrapper**

  Create `src/services/orchestrators/reconnecting.py`:

  ```python
  import asyncio
  import logging
  import time
  from dataclasses import dataclass
  from datetime import datetime, timezone
  from enum import Enum
  from typing import AsyncIterator, Callable, Optional

  from src.core.config import settings
  from src.services.orchestrators.protocol import OrchestratorEvent, OrchestratorProtocol

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
      disconnected_at: Optional[datetime]
      reconnect_attempts: int
      last_error: Optional[str]


  class ReconnectingOrchestrator:
      def __init__(self, client: OrchestratorProtocol, name: str) -> None:
          self._client = client
          self._name = name
          self._state = OrchestratorState.DEGRADED
          self._connected_at: Optional[datetime] = None
          self._disconnected_at: Optional[datetime] = None
          self._reconnect_attempts: int = 0
          self._last_error: Optional[str] = None
          self._reconnect_task: Optional[asyncio.Task] = None

          if hasattr(client, "on_disconnect"):
              client.on_disconnect = self._handle_disconnect

      # ------------------------------------------------------------------
      # OrchestratorProtocol
      # ------------------------------------------------------------------

      async def connect(self) -> None:
          await self._client.connect()
          self._state = OrchestratorState.CONNECTED
          self._connected_at = datetime.now(timezone.utc)
          self._reconnect_attempts = 0
          self._last_error = None

      async def close(self) -> None:
          if self._reconnect_task and not self._reconnect_task.done():
              self._reconnect_task.cancel()
              try:
                  await self._reconnect_task
              except asyncio.CancelledError:
                  pass
          await self._client.close()

      async def ping(self) -> bool:
          if self._state != OrchestratorState.CONNECTED:
              if self._state == OrchestratorState.DEGRADED:
                  self._ensure_reconnecting()
              return False
          return await self._client.ping()

      async def stream_response(
          self,
          text: str,
          user_id: str,
          model_id: Optional[str] = None,
          system_prompt_extra: Optional[str] = None,
      ) -> AsyncIterator[OrchestratorEvent]:
          if self._state != OrchestratorState.CONNECTED:
              if self._state == OrchestratorState.DEGRADED:
                  self._ensure_reconnecting()
              yield OrchestratorEvent(type="error", content="orchestrator_unavailable")
              return

          async for event in self._client.stream_response(
              text=text,
              user_id=user_id,
              model_id=model_id,
              system_prompt_extra=system_prompt_extra,
          ):
              yield event

      # ------------------------------------------------------------------
      # Observability / control
      # ------------------------------------------------------------------

      def status(self) -> OrchestratorStatus:
          return OrchestratorStatus(
              name=self._name,
              state=self._state,
              connected_at=self._connected_at,
              disconnected_at=self._disconnected_at,
              reconnect_attempts=self._reconnect_attempts,
              last_error=self._last_error,
          )

      async def trigger_reconnect(self) -> None:
          if self._reconnect_task and not self._reconnect_task.done():
              self._reconnect_task.cancel()
              try:
                  await self._reconnect_task
              except asyncio.CancelledError:
                  pass
          self._state = OrchestratorState.RECONNECTING
          self._reconnect_task = asyncio.create_task(self._reconnect_loop())

      # ------------------------------------------------------------------
      # Internal
      # ------------------------------------------------------------------

      def _handle_disconnect(self) -> None:
          self._disconnected_at = datetime.now(timezone.utc)
          self._ensure_reconnecting()

      def _ensure_reconnecting(self) -> None:
          if not self._reconnect_task or self._reconnect_task.done():
              self._state = OrchestratorState.RECONNECTING
              self._reconnect_task = asyncio.create_task(self._reconnect_loop())

      async def _reconnect_loop(self) -> None:
          start = time.monotonic()
          backoff = settings.ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF

          while True:
              try:
                  await self._client.connect()
                  self._state = OrchestratorState.CONNECTED
                  self._connected_at = datetime.now(timezone.utc)
                  self._reconnect_attempts = 0
                  logger.info(f"Orchestrator '{self._name}' reconnected.")
                  return
              except asyncio.CancelledError:
                  raise
              except Exception as e:
                  self._reconnect_attempts += 1
                  self._last_error = str(e)
                  logger.warning(
                      f"Orchestrator '{self._name}' reconnect attempt "
                      f"{self._reconnect_attempts} failed: {e}"
                  )

              elapsed = time.monotonic() - start
              if elapsed >= settings.ORCHESTRATOR_RECONNECT_MAX_DURATION:
                  self._state = OrchestratorState.DEGRADED
                  logger.warning(
                      f"Orchestrator '{self._name}' reconnect exhausted "
                      f"after {elapsed:.0f}s — entering DEGRADED state."
                  )
                  return

              await asyncio.sleep(backoff)
              backoff = min(backoff * 2, settings.ORCHESTRATOR_RECONNECT_MAX_BACKOFF)
  ```

- [ ] **Step 4: Run the two tests to verify they pass**

  ```bash
  PYTHONPATH=. pytest tests/integration/test_reconnecting_orchestrator.py::test_disconnect_triggers_reconnecting_state tests/integration/test_reconnecting_orchestrator.py::test_stream_response_while_reconnecting_yields_error -v
  ```
  Expected: both PASSED.

- [ ] **Step 5: Commit**

  ```bash
  git add src/services/orchestrators/reconnecting.py tests/integration/test_reconnecting_orchestrator.py
  git commit -m "feat(orchestrators): add ReconnectingOrchestrator wrapper with state machine"
  ```

---

## Task 3: ReconnectingOrchestrator — reconnect loop tests

Add the remaining 5 tests covering the reconnect loop, lazy/manual reconnect, and `status()` fields. The implementation is already in place from Task 2.

**Files:**
- Modify: `tests/integration/test_reconnecting_orchestrator.py`

- [ ] **Step 1: Append the 5 reconnect-loop tests to the test file**

  Append to `tests/integration/test_reconnecting_orchestrator.py`:

  ```python
  async def test_reconnect_success_restores_connected_state():
      client = _make_client()  # connect() succeeds immediately
      wrapper = ReconnectingOrchestrator(client, name="test")
      wrapper._state = OrchestratorState.RECONNECTING
      wrapper._reconnect_task = asyncio.create_task(wrapper._reconnect_loop())

      await wrapper._reconnect_task

      assert wrapper._state == OrchestratorState.CONNECTED
      assert wrapper._reconnect_attempts == 0
      assert wrapper._connected_at is not None


  async def test_reconnect_exhausted_goes_degraded(monkeypatch):
      monkeypatch.setattr(
          "src.services.orchestrators.reconnecting.settings",
          type("S", (), {
              "ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF": 0.0,
              "ORCHESTRATOR_RECONNECT_MAX_BACKOFF": 0.0,
              "ORCHESTRATOR_RECONNECT_MAX_DURATION": 0.0,
          })(),
      )
      client = _make_client(connect_side_effect=OSError("refused"))
      wrapper = ReconnectingOrchestrator(client, name="test")
      wrapper._state = OrchestratorState.RECONNECTING
      wrapper._reconnect_task = asyncio.create_task(wrapper._reconnect_loop())

      await wrapper._reconnect_task

      assert wrapper._state == OrchestratorState.DEGRADED
      assert wrapper._reconnect_attempts == 1
      assert wrapper._last_error == "refused"


  async def test_lazy_reconnect_on_stream_in_degraded():
      client = _make_client(connect_side_effect=OSError("refused"))
      wrapper = ReconnectingOrchestrator(client, name="test")
      wrapper._state = OrchestratorState.DEGRADED

      events = [e async for e in wrapper.stream_response("hello", "user-1")]

      assert events[0].type == "error"
      assert events[0].content == "orchestrator_unavailable"
      assert wrapper._state == OrchestratorState.RECONNECTING
      assert wrapper._reconnect_task is not None
      wrapper._reconnect_task.cancel()
      try:
          await wrapper._reconnect_task
      except asyncio.CancelledError:
          pass


  async def test_manual_trigger_reconnect():
      client = _make_client(connect_side_effect=OSError("refused"))
      wrapper = ReconnectingOrchestrator(client, name="test")
      wrapper._state = OrchestratorState.DEGRADED

      await wrapper.trigger_reconnect()

      assert wrapper._state == OrchestratorState.RECONNECTING
      assert wrapper._reconnect_task is not None
      wrapper._reconnect_task.cancel()
      try:
          await wrapper._reconnect_task
      except asyncio.CancelledError:
          pass


  async def test_status_fields():
      client = _make_client(connect_side_effect=OSError("refused"))
      wrapper = ReconnectingOrchestrator(client, name="myorch")
      wrapper._state = OrchestratorState.CONNECTED
      wrapper._connected_at = datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc)

      wrapper._handle_disconnect()
      if wrapper._reconnect_task:
          wrapper._reconnect_task.cancel()
          try:
              await wrapper._reconnect_task
          except asyncio.CancelledError:
              pass

      s = wrapper.status()

      assert s.name == "myorch"
      assert s.state == OrchestratorState.RECONNECTING
      assert s.connected_at == datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc)
      assert isinstance(s.disconnected_at, datetime)
      assert s.reconnect_attempts == 0
  ```

- [ ] **Step 2: Run all 7 tests**

  ```bash
  PYTHONPATH=. pytest tests/integration/test_reconnecting_orchestrator.py -v
  ```
  Expected: all 7 PASSED.

- [ ] **Step 3: Commit**

  ```bash
  git add tests/integration/test_reconnecting_orchestrator.py
  git commit -m "test(orchestrators): complete ReconnectingOrchestrator test suite"
  ```

---

## Task 4: OpenClawClient — on_disconnect callback

Add a single `on_disconnect` attribute to `OpenClawClient` and call it from `_listen()` when the connection drops (but not on clean shutdown via `CancelledError`).

**Files:**
- Modify: `src/services/orchestrators/openclaw_client.py`

- [ ] **Step 1: Add the `on_disconnect` attribute to `__init__`**

  In `src/services/orchestrators/openclaw_client.py`, inside `__init__`, after the `_health_futures` line, add:

  ```python
  # Disconnect notification — set by ReconnectingOrchestrator at wrap time
  self.on_disconnect: Optional[Callable[[], None]] = None
  ```

  Also add `Callable` to the import at the top of the file:

  ```python
  from typing import AsyncIterator, Callable, Optional
  ```

- [ ] **Step 2: Call `on_disconnect` in `_listen()` on connection drop**

  Replace the current `_listen()` method body so that after the try/except block (i.e., when the connection drops for any non-CancelledError reason), it calls the callback. The full replacement for `_listen()`:

  ```python
  async def _listen(self) -> None:
      try:
          async for raw in self._ws:
              frame = json.loads(raw)
              ftype = frame.get("type")

              if ftype == "res":
                  req_id = frame.get("id")

                  if req_id in self._health_futures:
                      fut = self._health_futures.pop(req_id)
                      if not fut.done():
                          fut.set_result(frame)

                  elif req_id == self._active_req_id and self._turn_queue is not None:
                      await self._turn_queue.put(("done", frame))

              elif ftype == "event":
                  event_name = frame.get("event")
                  payload = frame.get("payload", {})

                  if event_name == "chat" and self._turn_queue is not None:
                      await self._turn_queue.put(("chat", payload))

      except asyncio.CancelledError:
          return  # clean shutdown — do not notify wrapper
      except Exception as e:
          logger.error(f"OpenClawClient listener error: {e}")
          if self._turn_queue is not None:
              await self._turn_queue.put(("error", str(e)))

      # Connection dropped (not a clean shutdown via close())
      if self.on_disconnect:
          self.on_disconnect()
  ```

- [ ] **Step 3: Guard `stream_response()` send against a closed socket**

  In `stream_response()`, wrap the `await self._ws.send(...)` call so a broken connection yields a clean error instead of raising. Replace the send line with:

  ```python
  try:
      await self._ws.send(json.dumps({
          "type": "req",
          "id": req_id,
          "method": "chat.send",
          "params": {
              "sessionKey": self._session_key,
              "message": text,
              "idempotencyKey": str(uuid.uuid4()),
          },
      }))
  except Exception as e:
      yield OrchestratorEvent(type="error", content=f"orchestrator send failed: {e}")
      return
  ```

- [ ] **Step 4: Run lint and existing tests**

  ```bash
  PYTHONPATH=. ruff check src/services/orchestrators/openclaw_client.py
  PYTHONPATH=. pytest tests/ -v --ignore=tests/integration/test_reconnecting_orchestrator.py
  ```
  Expected: lint clean; all pre-existing tests still PASSED.

- [ ] **Step 5: Commit**

  ```bash
  git add src/services/orchestrators/openclaw_client.py
  git commit -m "feat(openclaw): add on_disconnect callback and guard stream send"
  ```

---

## Task 5: Registry — wrap clients and expose status/reconnect

`build_registry()` wraps each client in `ReconnectingOrchestrator`. `OrchestratorRegistry` gains `get_status()` and `reconnect()`. Update `conftest.py` to include these methods in the mock.

**Files:**
- Modify: `src/services/orchestrators/registry.py`
- Modify: `tests/integration/conftest.py`

- [ ] **Step 1: Update `registry.py`**

  Replace the entire contents of `src/services/orchestrators/registry.py` with:

  ```python
  import logging
  from src.services.orchestrators.protocol import OrchestratorProtocol
  from src.services.orchestrators.reconnecting import (
      OrchestratorStatus,
      ReconnectingOrchestrator,
  )

  logger = logging.getLogger(__name__)


  class OrchestratorRegistry:
      def __init__(self, clients: dict[str, ReconnectingOrchestrator]):
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

      def get_status(self, name: str) -> OrchestratorStatus:
          if name not in self._clients:
              raise KeyError(f"Orchestrator '{name}' not registered.")
          return self._clients[name].status()

      async def reconnect(self, name: str) -> None:
          if name not in self._clients:
              raise KeyError(f"Orchestrator '{name}' not registered.")
          await self._clients[name].trigger_reconnect()


  def build_registry() -> OrchestratorRegistry:
      from src.core.config import settings
      from src.services.orchestrators.openclaw_client import OpenClawClient

      clients: dict[str, ReconnectingOrchestrator] = {}

      if settings.OPENCLAW_TOKEN:
          inner = OpenClawClient(
              host="127.0.0.1",
              port=settings.OPENCLAW_PORT,
              token=settings.OPENCLAW_TOKEN,
              session_key="agent:main:main",
          )
          clients["openclaw"] = ReconnectingOrchestrator(inner, name="openclaw")
          logger.info("OpenClawClient registered (wrapped in ReconnectingOrchestrator).")
      else:
          logger.warning("OPENCLAW_TOKEN not set — openclaw orchestrator not registered.")

      return OrchestratorRegistry(clients)
  ```

- [ ] **Step 2: Update `conftest.py` to add `get_status` and `reconnect` mocks**

  In `tests/integration/conftest.py`, add the import at the top:

  ```python
  from datetime import datetime, timezone
  from src.services.orchestrators.reconnecting import OrchestratorState, OrchestratorStatus
  ```

  Replace the `make_mock_registry` function with:

  ```python
  def make_mock_registry(orchestrator=None) -> OrchestratorRegistry:
      if orchestrator is None:
          orchestrator = make_mock_orchestrator()
      registry = MagicMock(spec=OrchestratorRegistry)
      registry.connect_all = AsyncMock()
      registry.close_all = AsyncMock()
      registry.default = MagicMock(return_value=orchestrator)
      registry.get = MagicMock(return_value=orchestrator)

      _known = {
          "openclaw": OrchestratorStatus(
              name="openclaw",
              state=OrchestratorState.CONNECTED,
              connected_at=datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc),
              disconnected_at=None,
              reconnect_attempts=0,
              last_error=None,
          )
      }

      def _get_status(name: str) -> OrchestratorStatus:
          if name not in _known:
              raise KeyError(f"Orchestrator '{name}' not registered.")
          return _known[name]

      async def _reconnect(name: str) -> None:
          if name not in _known:
              raise KeyError(f"Orchestrator '{name}' not registered.")

      registry.get_status = MagicMock(side_effect=_get_status)
      registry.reconnect = AsyncMock(side_effect=_reconnect)
      return registry
  ```

- [ ] **Step 3: Run all tests**

  ```bash
  PYTHONPATH=. pytest tests/ -v
  ```
  Expected: all tests PASSED (including the 7 reconnect tests from Tasks 2–3).

- [ ] **Step 4: Commit**

  ```bash
  git add src/services/orchestrators/registry.py tests/integration/conftest.py
  git commit -m "feat(registry): wrap orchestrators in ReconnectingOrchestrator, add get_status/reconnect"
  ```

---

## Task 6: REST API — orchestrator status and reconnect endpoints

Create the two new REST endpoints and register the router in `main.py`.

**Files:**
- Create: `src/api/orchestrator_routes.py`
- Modify: `src/main.py`
- Create: `tests/integration/test_orchestrator_routes.py`

- [ ] **Step 1: Write the 4 failing integration tests**

  Create `tests/integration/test_orchestrator_routes.py`:

  ```python
  """
  Integration tests for GET /api/orchestrators/{name}/status
  and POST /api/orchestrators/{name}/reconnect.

  Uses the `client` fixture (mock registry + mock jota-db) from conftest.py.
  """


  def test_get_orchestrator_status_connected(client, auth_headers):
      response = client.get("/api/orchestrators/openclaw/status", headers=auth_headers)

      assert response.status_code == 200
      data = response.json()
      assert data["name"] == "openclaw"
      assert data["state"] == "CONNECTED"
      assert data["reconnect_attempts"] == 0
      assert data["last_error"] is None


  def test_get_orchestrator_status_not_found(client, auth_headers):
      response = client.get("/api/orchestrators/unknown/status", headers=auth_headers)

      assert response.status_code == 404


  def test_post_orchestrator_reconnect_accepted(client, auth_headers):
      response = client.post("/api/orchestrators/openclaw/reconnect", headers=auth_headers)

      assert response.status_code == 202


  def test_post_orchestrator_reconnect_not_found(client, auth_headers):
      response = client.post("/api/orchestrators/unknown/reconnect", headers=auth_headers)

      assert response.status_code == 404
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  PYTHONPATH=. pytest tests/integration/test_orchestrator_routes.py -v
  ```
  Expected: all 4 FAILED with 404 (routes don't exist yet).

- [ ] **Step 3: Create `src/api/orchestrator_routes.py`**

  ```python
  """
  orchestrator_routes.py
  ~~~~~~~~~~~~~~~~~~~~~~
  GET  /api/orchestrators/{name}/status  — current state of a named orchestrator
  POST /api/orchestrators/{name}/reconnect — trigger manual reconnection (202 Accepted)
  """
  from fastapi import APIRouter, Depends, HTTPException, Request

  from src.api.deps import get_verified_client

  router = APIRouter()


  @router.get("/orchestrators/{name}/status")
  async def get_orchestrator_status(
      name: str,
      request: Request,
      _auth: tuple = Depends(get_verified_client),
  ) -> dict:
      registry = request.app.state.orchestrators
      try:
          s = registry.get_status(name)
      except KeyError:
          raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
      return {
          "name": s.name,
          "state": s.state.value,
          "connected_at": s.connected_at.isoformat() if s.connected_at else None,
          "disconnected_at": s.disconnected_at.isoformat() if s.disconnected_at else None,
          "reconnect_attempts": s.reconnect_attempts,
          "last_error": s.last_error,
      }


  @router.post("/orchestrators/{name}/reconnect", status_code=202)
  async def post_orchestrator_reconnect(
      name: str,
      request: Request,
      _auth: tuple = Depends(get_verified_client),
  ) -> dict:
      registry = request.app.state.orchestrators
      try:
          await registry.reconnect(name)
      except KeyError:
          raise HTTPException(status_code=404, detail=f"Orchestrator '{name}' not registered")
      return {"accepted": True}
  ```

- [ ] **Step 4: Register the router in `main.py`**

  In `src/main.py`, add the import after the other route imports:

  ```python
  from src.api.orchestrator_routes import router as orchestrator_router
  ```

  And register it after the existing `app.include_router` calls:

  ```python
  app.include_router(orchestrator_router, prefix="/api")
  ```

- [ ] **Step 5: Run the 4 new tests**

  ```bash
  PYTHONPATH=. pytest tests/integration/test_orchestrator_routes.py -v
  ```
  Expected: all 4 PASSED.

- [ ] **Step 6: Run the full test suite**

  ```bash
  PYTHONPATH=. pytest tests/ -v
  ```
  Expected: all tests PASSED. Note the count — it should be higher than before this plan.

- [ ] **Step 7: Run lint**

  ```bash
  PYTHONPATH=. ruff check src/ tests/
  ```
  Expected: no errors.

- [ ] **Step 8: Commit**

  ```bash
  git add src/api/orchestrator_routes.py src/main.py tests/integration/test_orchestrator_routes.py
  git commit -m "feat(api): add GET /orchestrators/{name}/status and POST .../reconnect endpoints"
  ```
