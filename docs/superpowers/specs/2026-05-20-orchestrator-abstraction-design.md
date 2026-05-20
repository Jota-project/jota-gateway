# Spec: Orchestrator Abstraction + OpenClaw Provider

**Date:** 2026-05-20  
**Status:** Draft — pending OpenClaw protocol investigation  
**Features:** A (Abstraction + OpenClaw client) · B (OpenAI REST endpoint)

---

## Motivation

jota-gateway currently has a single hardcoded backend: `OrchestratorClient` (Jota, custom NDJSON protocol). We want to support multiple orchestrator backends — starting with OpenClaw — selectable without touching application code. The gateway should be agnostic of which backend it is using.

---

## Scope

### Feature A — Orchestrator Abstraction + OpenClawClient
1. Define a unified `OrchestratorProtocol` and `OrchestratorEvent` (the contract)
2. Create `OpenClawClient` implementing that protocol (wire format TBD — see §OpenClaw Protocol)
3. Create `OrchestratorRegistry` that builds and manages client instances from config
4. Adapt `JotaBridge` to depend on the protocol, not on a concrete class
5. Park the existing Jota client in a separate git branch (`legacy/jota-orchestrator`)

### Feature B — OpenAI-compatible REST endpoint
1. `GET  /v1/models`
2. `POST /v1/chat/completions` — text-to-text, no audio
3. Delegates to the registry (whichever orchestrator is the default)
4. No auth required from callers (LAN-trusted path, nginx fronts it)

Feature B is blocked by Feature A.

---

## Architecture

```
Client (HA / ESP32 / any OpenAI client)
        │
        ▼
jota-gateway :8004
  ├── WebSocket /ws/stream  →  JotaBridge
  └── REST /v1/*            →  OpenAI-compatible router
        │                           │
        └──────────┬────────────────┘
                   ▼
          OrchestratorRegistry
            { "openclaw": OpenClawClient, ... }
                   │
                   ▼
          OrchestratorProtocol.stream_response()
                   │
          yields OrchestratorEvent(type, content)
                   │
                   ▼
          OpenClaw (proprietary protocol, :18789)
```

---

## Contracts

### OrchestratorEvent

```python
@dataclass
class OrchestratorEvent:
    type: Literal["token", "status", "error"]
    content: str = ""
```

All orchestrator clients normalize their wire format to this type. The bridge and REST endpoint never see protocol-specific data.

### OrchestratorProtocol

```python
class OrchestratorProtocol(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...
    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: str | None = None,
        system_prompt_extra: str | None = None,
    ) -> AsyncIterator[OrchestratorEvent]: ...
```

---

## File Structure

```
src/services/orchestrators/
    __init__.py
    protocol.py          ← OrchestratorEvent + OrchestratorProtocol
    openclaw_client.py   ← OpenClawClient (proprietary protocol, TBD)
    registry.py          ← build_registry(settings) + OrchestratorRegistry class

src/api/
    openai_routes.py     ← GET /v1/models, POST /v1/chat/completions  [Feature B]
```

Files removed from active path (preserved in `legacy/jota-orchestrator` branch):
- `src/services/orchestrator_client.py`

---

## Configuration

Additions to `.env` and `src/core/config.py`:

```bash
DEFAULT_ORCHESTRATOR=openclaw   # which registry entry to use when none specified

OPENCLAW_PORT=18789
OPENCLAW_TOKEN=<value from openclaw.json → gateway.auth.token>
```

The registry auto-registers any orchestrator whose required config vars are present. If `OPENCLAW_TOKEN` is empty, OpenClaw is not registered.

`DEFAULT_ORCHESTRATOR` is the fallback until per-client selection is implemented in jota-db (future).

---

## OrchestratorRegistry

```python
class OrchestratorRegistry:
    def __init__(self, clients: dict[str, OrchestratorProtocol]): ...

    async def connect_all(self) -> None: ...   # called in app lifespan startup
    async def close_all(self) -> None: ...     # called in app lifespan shutdown

    def get(self, name: str) -> OrchestratorProtocol: ...   # raises KeyError if missing
    def default(self) -> OrchestratorProtocol: ...          # uses DEFAULT_ORCHESTRATOR
```

Built once in `main.py` lifespan, stored in `app.state.orchestrators`, injected into routes via FastAPI dependency.

---

## JotaBridge changes

- Constructor receives `OrchestratorProtocol` instance (injected from registry)
- `self.orchestrator` typed as `OrchestratorProtocol`
- `_call_orchestrator()` iterates `stream_response()` directly — no `listen_loop()`
- No other behavioral changes

---

## OpenAI REST endpoint (Feature B)

Routes registered at `/v1` (no `/api` prefix — nginx maps `/api/gateway/` → `:8004`):

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/v1/models` | Returns static list with default orchestrator name as model id |
| `POST` | `/v1/chat/completions` | Converts messages → text, streams via registry.default(), returns SSE or JSON |

**Message → text conversion:** concatenate `role: content` from the messages array. Only the last user message is sent as the prompt for now (simple pipeline).

**Streaming:** if `stream: true` in request body, response is `text/event-stream` SSE. If false, accumulates tokens and returns a single JSON response.

**No auth** from callers. The endpoint is exposed only on LAN via nginx.

---

## OpenClaw Protocol

**Status: Resolved.** OpenClaw is WebSocket-only (no REST API — issue #27303 closed as "not planned").

### Transport
WebSocket to `ws://127.0.0.1:{OPENCLAW_PORT}`, JSON text frames, protocol v4.

### Auth (loopback backend mode)
Because jota-gateway runs on the same host as OpenClaw, it uses the **backend exception**:
no device signature required. Just token + `client.mode: "backend"`:

```json
{
  "method": "connect",
  "params": {
    "minProtocol": 3, "maxProtocol": 4,
    "client": {"id": "jota-gateway", "version": "1.0.0", "platform": "linux", "mode": "backend"},
    "role": "operator",
    "scopes": ["operator.read", "operator.write"],
    "auth": {"token": "<OPENCLAW_TOKEN>"}
  }
}
```

Handshake: server sends `connect.challenge` event first → client responds with `connect` req → server returns `hello-ok`.

### Sending a prompt
```json
{
  "method": "chat.send",
  "params": {
    "session": {"key": "<session-key>"},
    "message": "<user text>",
    "idempotencyKey": "<uuid-v4>"
  }
}
```

### Streaming response
Server pushes `chat` events before the final `res`:
```json
{"type": "event", "event": "chat", "payload": {"deltaText": "hello", "replace": false, "seq": 1}}
```
`replace: true` means discard previous content and replace. Turn complete when matching `res {ok: true}` arrives.

### Abort
```json
{"method": "chat.abort", "params": {"session": {"key": "<session-key>"}}}
```

### Health check
```json
{"method": "health", "params": {}}
```
Returns ok or error — use as `ping()` implementation.

### Session key strategy
`OpenClawClient` accepts a configurable `session_key`. Default: `"jota-gateway-default"`.
Each concurrent turn must use a unique idempotencyKey to prevent deduplication collisions.

### Connection lifecycle
`OpenClawClient.connect()` opens the WebSocket, performs the full handshake, and keeps the connection alive (persistent — not per-request). The registry calls `connect_all()` in app lifespan startup and `close_all()` on shutdown.

---

## Git Strategy

```
main                        current state (Jota client active)
  │
  ├── legacy/jota-orchestrator   branch created from current main
  │                              preserves orchestrator_client.py intact
  │                              never merged back
  │
  └── feat/openclaw-orchestrator  all Feature A + B work
        merged to main when complete
```

---

## Implementation Order

1. Create `legacy/jota-orchestrator` branch from current main
2. Create `feat/openclaw-orchestrator` branch
3. Implement `protocol.py` — OrchestratorEvent + OrchestratorProtocol
4. Implement `registry.py` — OrchestratorRegistry + build_registry()
5. Adapt `bridge.py` — receive Protocol instance, iterate stream_response()
6. Update `config.py` — add OPENCLAW_* and DEFAULT_ORCHESTRATOR vars
7. Update `main.py` — build registry in lifespan, store in app.state
8. **[blocked: OpenClaw protocol investigation]** Implement `openclaw_client.py`
9. Implement `openai_routes.py` — Feature B
10. Integration tests for both surfaces

---

## Out of Scope

- Per-client orchestrator selection from jota-db (future)
- JotaOrchestratorClient in the new abstraction (future, lives in legacy branch)
- Audio pipeline on the OpenAI REST endpoint
- External auth on `/v1/*`
