# Spec: OpenClaw Multiplexed Connection & Push Routing

**Date:** 2026-06-29
**Status:** Approved

---

## Context

jota-gateway currently connects to OpenClaw with a **singleton** `OpenClawClient` that serializes all turns: only one `chat.send` can be in-flight at a time. With multiple simultaneous clients (kitchen, 3 bedrooms, living room, dedicated devices), this causes head-of-line blocking.

An empirical probe confirmed that **OpenClaw processes multiple concurrent sessions on a single WebSocket connection** — both `chat.send` requests received `status: started` within 1ms and streamed in parallel. Events from different sessions are tagged with `sessionKey` and `runId`, enabling correct routing on the gateway side.

The probe also revealed two additional facts:
- `sessions.subscribe` (no params) delivers ALL session events — including agent-initiated pushes — with `sessionKey` in every payload.
- The `chat.send` API changed: it now uses `sessionKey` at the top level, not `session: {key: ...}`. The current codebase uses the old format.

This spec covers **Plan 1**: restructuring the OpenClaw layer to support multiplexed concurrent sessions and agent-initiated push delivery. **Plan 2** (auth abstraction, jota-db proxy removal, ClientConfig decoupling) is a separate spec.

---

## Confirmed Decisions

| # | Decision |
|---|---|
| 1 | Single WebSocket connection to OpenClaw, multiplexed across all clients — no pool, no per-client connections |
| 2 | `sessions.subscribe` called once on connect — all events carry `sessionKey` |
| 3 | Routing table replaces `_active_req_id + _turn_queue`: `{req_id → Queue}` for res frames, `{session_key → Queue}` for chat events |
| 4 | `client_id` extracted from session key as last segment after last colon: `sk.rsplit(":", 1)[-1]` |
| 5 | Default agent comes from `hello-ok.payload.defaultAgentId` — `OPENCLAW_DEFAULT_AGENT` setting removed |
| 6 | `openclaw_client.py` is refactored into a `src/services/openclaw/` package to keep files focused |
| 7 | Push delivery respects the target client's `output_mode` from their Handshake |
| 8 | Agent-initiated turn (`phase: start`) pre-prepares TTS client before first token arrives |
| 9 | `chat.abort` and all other session methods updated to the new `sessionKey` param format |
| 10 | `OPENCLAW_DEFAULT_AGENT` removed from `.env`, `.env.example`, `config.py`, and all references |

---

## Architecture

### Package structure

```
src/services/openclaw/
├── __init__.py          # public exports: OpenClawClient, ClientRegistry, GatewayInfo
├── client.py            # WS connection, handshake, send methods, keepalive loop
│                        #   _listen: receives frames and calls dispatcher
├── dispatcher.py        # frame routing: chat→registry, res→turns, agent→push
├── registry.py          # SessionRegistry + ClientRegistry, session key helpers
├── models.py            # GatewayInfo, typed frame dataclasses
└── reconnecting.py      # ReconnectingOpenClawClient (replaces orchestrators/reconnecting.py)
```

The old `src/services/orchestrators/` directory is removed and replaced by this package. The `OrchestratorProtocol` in `src/services/orchestrators/protocol.py` is kept at `src/services/protocol.py` and updated to reflect the new interface.

---

### `models.py`

```python
@dataclass
class GatewayInfo:
    protocol_version: int
    server_version: str
    conn_id: str
    default_agent_id: str
    agents: dict[str, AgentInfo]   # agentId → AgentInfo
    tick_interval_ms: int
    max_payload: int

@dataclass
class AgentInfo:
    agent_id: str
    name: str
    is_default: bool
```

Populated from `hello-ok` on every connect. Available via `client.gateway_info` after connect.

---

### `registry.py`

```python
class SessionRegistry:
    """Maps active session keys and req_ids to their delivery queues.

    A single queue per session_key carries both chat events and the final res:
      ("chat", payload)   — streaming token
      ("done", frame)     — final res frame (ok or error)
      ("error", str)      — internal error (e.g. reconnect)

    req_id → session_key lets the dispatcher find the right queue when
    the final res arrives (res frames carry req_id, not sessionKey).
    """
    _sessions: dict[str, asyncio.Queue]   # session_key → queue
    _req_to_session: dict[str, str]       # req_id → session_key

    def register(self, req_id: str, session_key: str) -> asyncio.Queue: ...
    def unregister(self, session_key: str, req_id: str) -> None: ...
    def get_queue_by_session(self, session_key: str) -> asyncio.Queue | None: ...
    def get_queue_by_req(self, req_id: str) -> asyncio.Queue | None: ...


class ClientRegistry:
    """Maps active client_id → JotaBridge for push routing."""
    _clients: dict[str, "JotaBridge"]

    def register(self, client_id: str, bridge: "JotaBridge") -> None: ...
    def unregister(self, client_id: str) -> None: ...
    def get(self, client_id: str) -> "JotaBridge | None": ...


def client_id_from_session_key(session_key: str) -> str:
    """Extract client_id as the last segment after the last colon.

    Examples:
        "agent:main:hab_sito"                     → "hab_sito"
        "agent:plants:telegram:direct:5239228928" → "5239228928"
    """
    return session_key.rsplit(":", 1)[-1]
```

Both registries held in `app.state` (created in `main.py` lifespan).

---

### `client.py`

Responsibilities: WS connection, handshake, keepalive, raw send. Does NOT know about routing.

```python
class OpenClawClient:
    def __init__(self, host, port, token, session_registry: SessionRegistry): ...

    async def connect(self) -> GatewayInfo: ...   # returns parsed hello-ok
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...

    async def send_chat(
        self,
        session_key: str,
        message: str,
        req_id: str,
        idempotency_key: str,
    ) -> None: ...

    async def abort_chat(self, session_key: str) -> None: ...

    # _listen receives frames → calls self._dispatcher.dispatch(frame)
    async def _listen(self) -> None: ...
    async def _keepalive_loop(self) -> None: ...
```

`connect()` flow:
1. WS connect
2. Receive `connect.challenge`
3. Send `connect` (backend mode)
4. Receive `hello-ok` → parse into `GatewayInfo`, store as `self.gateway_info`
5. Send `sessions.subscribe`
6. Start `_listen` and `_keepalive_loop` tasks

`send_chat` uses the new API format:
```json
{"type": "req", "id": "<req_id>", "method": "chat.send",
 "params": {"sessionKey": "<key>", "message": "...", "idempotencyKey": "..."}}
```

---

### `dispatcher.py`

Responsibilities: parse incoming frames and route them. Knows about `SessionRegistry` and `ClientRegistry`. Does NOT know about WebSocket or TTS.

```python
class FrameDispatcher:
    def __init__(self, session_registry: SessionRegistry, client_registry: ClientRegistry): ...

    async def dispatch(self, frame: dict) -> None:
        ftype = frame["type"]
        if ftype == "res":
            await self._handle_res(frame)
        elif ftype == "event":
            await self._handle_event(frame)

    async def _handle_res(self, frame):
        # res with matching req_id → find session queue → put ("done", frame)
        q = self._session_registry.get_queue_by_req(frame["id"])
        if q and frame.get("payload", {}).get("status") != "started":
            await q.put(("done", frame))

    async def _handle_event(self, frame): ...
        event = frame["event"]
        if event == "chat":       await self._handle_chat(frame["payload"])
        elif event == "agent":    await self._handle_agent_lifecycle(frame["payload"])
        elif event == "sessions.changed": ...  # observability only
        # health, tick, presence → ignored

    async def _handle_chat(self, payload):
        # payload has sessionKey (always, because sessions.subscribe is active)
        sk = payload["sessionKey"]
        q = self._session_registry.get_queue_by_session(sk)
        if q:
            await q.put(("chat", payload))
        else:
            # push for a session with no active turn — route to ClientRegistry
            client_id = client_id_from_session_key(sk)
            bridge = self._client_registry.get(client_id)
            if bridge:
                await bridge.deliver_push(payload)

    async def _handle_agent_lifecycle(self, payload):
        # phase "start" → pre-warm TTS for that session's client
        if payload["data"]["phase"] == "start":
            sk = payload["sessionKey"]
            client_id = client_id_from_session_key(sk)
            bridge = self._client_registry.get(client_id)
            if bridge:
                await bridge.on_push_turn_start(sk)
```

---

### `stream_response()` — new concurrent design

Replaces the current method. Now:
1. Generates a `req_id`
2. Registers `(req_id, session_key)` in `session_registry`
3. Sends `chat.send`
4. Reads from the session queue until `res` with `status != "started"` arrives
5. Yields `OrchestratorEvent` tokens
6. In `finally`: unregisters from session_registry; sends `chat.abort` if cancelled

```python
async def stream_response(self, text, session_key, user_id, ...) -> AsyncIterator[OrchestratorEvent]:
    req_id = str(uuid.uuid4())
    queue = self._session_registry.register(req_id, session_key)
    try:
        await self.send_chat(session_key, text, req_id, str(uuid.uuid4()))
        async for event in self._read_turn(queue):
            yield event
    finally:
        self._session_registry.unregister(session_key, req_id)
```

Multiple concurrent `stream_response()` calls now work because each uses its own `session_key` queue.

---

### `JotaBridge` — push integration

Two new methods:

```python
async def on_push_turn_start(self, session_key: str) -> None:
    """Called by dispatcher when agent lifecycle 'start' arrives for our client.
    Pre-creates TTSClient if output_mode includes audio."""

async def deliver_push(self, chat_payload: dict) -> None:
    """Called by dispatcher when a chat event arrives for our client
    but no active user-initiated turn is in progress.
    Routes token through existing TTS+WS pipeline with type='push'."""
```

`JotaBridge` registers itself in `ClientRegistry` in `connect_internal_services()` and deregisters in `close_all()`.

Push messages sent to the client WebSocket use `{"type": "push", ...}` instead of `{"type": "text", ...}` so the client can distinguish solicited vs unsolicited messages.

---

### `hello-ok` — agent validation

In `connect()`, after parsing `GatewayInfo`:

```python
def validate_agent(self, agent_id: str) -> bool:
    return agent_id in self.gateway_info.agents
```

In `routes.py`, Handshake handling:
- If `handshake.agent` is None → use `gateway_info.default_agent_id`
- If `handshake.agent` is set but not in `gateway_info.agents` → reject WS with error

---

### `reconnecting.py`

Same backoff logic as `orchestrators/reconnecting.py`, adapted for the new `OpenClawClient`. On reconnect, `connect()` automatically re-subscribes via `sessions.subscribe` (it's part of the connect flow). In-flight `_sessions` entries are cleared and their queues receive an `("error", "reconnecting")` item so callers can surface the error to clients.

---

## What Is Removed

| Item | Reason |
|------|--------|
| `src/services/orchestrators/` directory | Replaced by `src/services/openclaw/` |
| `OPENCLAW_DEFAULT_AGENT` setting | Comes from `hello-ok.defaultAgentId` now |
| `_active_req_id`, `_turn_queue` in `OpenClawClient` | Replaced by routing tables |
| `session: {key: ...}` format in all sends | Updated to `sessionKey: ...` |
| `OrchestratorRegistry` (was in `registry.py`) | No longer needed; single client |

---

## What Does NOT Change

- `JotaBridge` public interface (connect, run, close, health_check) — unchanged
- TTS + transcriber pipeline — unchanged
- `/ws/stream` route and Handshake schema — Handshake gains optional fields, no breaking changes
- `/v1/chat/completions` endpoint — uses the same `stream_response()`, gains session_key from its own logic
- All existing integration tests that don't touch `OpenClawClient` internals — must remain green

---

## File Size Guidelines

No file in `src/services/openclaw/` should exceed ~200 lines. If a file approaches that, split further. The `_listen` loop in `client.py` is intentionally minimal — it calls `dispatcher.dispatch(frame)` and nothing else.

---

## Plan 2 (Out of Scope Here)

- Auth abstraction: `AuthProvider` protocol with JotaDB / env-token / no-auth implementations
- Remove jota-db proxy: `/api/*` routes to jota-db for config/conversations/models/history
- `ClientConfig` decoupling from jota-db
- `.env` and `.env.example` cleanup of removed settings
