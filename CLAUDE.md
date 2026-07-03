# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the server in development
uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload

# Run all tests
PYTHONPATH=. pytest

# Run a specific test file
PYTHONPATH=. pytest tests/integration/test_admin_clients.py

# Run a specific test by name
PYTHONPATH=. pytest -k "test_create_client_minimal"

# Lint
ruff check src/ tests/

# Docker
docker compose up

# CLI — manage clients from the terminal
python3 src/cli.py add-client --name "ESP32 salón" [--key <existing-key>] [--type ha] [--agent assistant]
python3 src/cli.py list-clients
python3 src/cli.py deactivate-client <client_key>
python3 src/cli.py activate-client <client_key>
python3 src/cli.py delete-client <client_key>
```

## Architecture

jota-gateway is a **BFF (Backend For Frontend)** — the single entry point for all clients (ESP32, web, app, Home Assistant). It has four surfaces:

- **WebSocket** `/ws/stream` — full voice+text session managed by `JotaBridge`
- **Admin REST** `/admin/*` — client CRUD + observability (sessions, orchestrators); requires `X-Admin-Token`
- **OpenAI-compatible REST** `/v1/*` — `GET /v1/models` and `POST /v1/chat/completions` for Home Assistant integration; delegates to the singleton `OpenClawClient`
- **Health** `/healthz`, `/ready` — liveness and readiness probes

There is no longer any dependency on `jota-db`. Client identity and configuration live in the local SQLite database (`data/gateway.db`).

### Environment variables

```
DATABASE_URL=sqlite:///data/gateway.db   # path to SQLite file
ADMIN_TOKEN=<secret>                     # required for /admin/* routes

TRANSCRIBER_WS_URL=localhost:9000
TTS_WS_URL=localhost:8005
TTS_TOKEN=gateway

OPENCLAW_HOST=127.0.0.1
OPENCLAW_PORT=18789
OPENCLAW_TOKEN=<secret>

ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF=1.0
ORCHESTRATOR_RECONNECT_MAX_BACKOFF=60.0
ORCHESTRATOR_RECONNECT_MAX_DURATION=300.0
```

All service addresses are `host:port` **without protocol**. Each client injects the protocol itself (`http://`, `ws://`).

### URL convention in settings

All `Settings` fields for external services are `host:port` **without protocol**. Each service client injects the protocol itself at construction time (`http://`, `ws://`). Never store full URLs in settings.

---

## Local database (`src/db/`)

### `src/db/models.py` — `ClientRecord`

SQLModel table (`__tablename__ = "clients"`) with the following columns:

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | `str` (PK) | `uuid4()` | |
| `name` | `str` | — | Human label |
| `client_key` | `str` (unique, indexed) | — | Auth token the client sends in the handshake |
| `is_active` | `bool` | `True` | Deactivated clients are rejected at handshake |
| `client_type` | `str?` | `None` | Free label (e.g. `ha`, `esp32`) — not used in routing logic |
| `default_agent` | `str?` | `None` | Override OpenClaw agent for this client |
| `allowed_agents` | `str?` | `None` | JSON list of permitted agent names |
| `created_at` | `datetime` | `now(UTC)` | |
| `stt_language` | `str` | `"es"` | Passed to `TranscriberClient.connect()` |
| `stt_vad_thold` | `float` | `0.0` | VAD threshold passed to transcriber |
| `tts_voice` | `str` | `"af_heart"` | Passed to `TTSClient.connect()` |
| `tts_speed` | `float` | `1.0` | Passed to `TTSClient.connect()` |
| `barge_in_enabled` | `bool` | `True` | Whether partial transcriptions can cancel active turn |
| `barge_in_min_chars` | `int` | `5` | Minimum chars in partial before barge-in fires |
| `output_mode` | `str?` | `None` | JSON list — stored default, informational only |
| `silence_timeout_s` | `float` | `2.0` | Seconds of no transcription before a silence event |
| `max_silence_turns` | `int` | `3` | Consecutive silence events before session is closed |
| `push_enabled` | `bool` | `True` | Whether agent-initiated push turns are accepted |
| `tool_calls_enabled` | `bool` | `False` | Whether `tool_call` WS messages are sent for this client (opt-in) |
| `system_prompt_extra` | `str?` | `None` | Appended to the system prompt for all turns |

### `src/db/database.py`

```python
get_engine()            # lazy-init SQLAlchemy engine from DATABASE_URL
create_db_and_tables()  # called once at app startup (lifespan)
get_db_session()        # FastAPI dependency — yields a SQLModel Session
```

The module-level `_engine` variable is intentionally exposed for monkeypatching in tests.

### `src/core/exceptions.py`

```python
class ClientNotFound(Exception): ...   # client_key not in DB
class ClientInactive(Exception): ...   # client exists but is_active=False
```

Both are raised by `db_client.get_session()` and caught in `routes.py` to close the WebSocket with code 1008.

---

## Client management

### Admin REST API (`/admin/clients/*`)

All routes require `X-Admin-Token: <ADMIN_TOKEN>`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/admin/clients` | List all clients |
| `POST` | `/admin/clients` | Create client (returns generated or provided `client_key`) |
| `GET` | `/admin/clients/{id}` | Get client by UUID |
| `PATCH` | `/admin/clients/{id}` | Partial update (exclude_unset semantics) |
| `DELETE` | `/admin/clients/{id}` | Delete client |
| `POST` | `/admin/clients/{id}/rotate-key` | Regenerate `client_key` |

`POST /admin/clients` body: all fields from `ClientCreate` — `name` is required, `client_key` is optional (generated if omitted), everything else defaults to the table defaults above.

After any mutation, `db_client.invalidate(client_key)` is called to evict the 60s session cache.

Schemas: `src/models/admin_schemas.py` — `ClientCreate`, `ClientUpdate`, `ClientResponse`.

### CLI (`src/cli.py`)

```bash
python3 src/cli.py add-client --name "ESP32" [--key <key>] [--type esp32] [--agent main]
python3 src/cli.py list-clients
python3 src/cli.py deactivate-client <client_key>
python3 src/cli.py activate-client <client_key>
python3 src/cli.py delete-client <client_key>
```

`--key` lets you import an existing token verbatim (e.g. migrating from jota-db). Omit it to generate a random key.

The CLI calls `create_db_and_tables()` at startup, so it is safe to run against a fresh `data/gateway.db`.

---

## DbClient (`src/services/db_client.py`)

Replaces the old HTTP client. Reads `ClientRecord` from SQLite and returns `(Client, ClientConfig)`.

```python
await db_client.get_session(client_key) -> (Client, ClientConfig)
db_client.invalidate(client_key)        # evict from 60s TTL cache
```

`ClientConfig` fields mapped from `ClientRecord`:
`stt_language`, `stt_vad_thold`, `tts_voice`, `tts_speed`, `barge_in_enabled`, `barge_in_min_chars`, `system_prompt_extra`, `silence_timeout_s`, `max_silence_turns`, `push_enabled`.

The singleton `db_client = DbClient()` is imported from this module everywhere. An optional `engine` constructor parameter allows injecting a test engine.

---

## WebSocket session lifecycle (`routes.py` → `bridge.py`)

1. Client connects and sends a **Handshake** JSON:
   ```json
   {
     "client_key": "...",
     "input_mode": "audio" | "text",
     "output_mode": ["audio", "text", "status"],
     "agent": "assistant"   // optional — defaults to gateway_info.default_agent_id
   }
   ```
2. Gateway resolves identity via `db_client.get_session(client_key)` → `(Client, ClientConfig)`.
   - `ClientNotFound` or `ClientInactive` → close 1008.
3. If `agent` is specified, `routes.py` validates it against `openclaw.gateway_info.has_agent(agent)` — unknown agents close with code 1008.
4. `JotaBridge` is instantiated with client, config, WebSocket, the singleton `ReconnectingOpenClawClient` (from `app.state.openclaw`), `app.state.client_registry`, and `default_agent`.
5. `bridge.connect_internal_services()` — starts `TranscriberClient` only if `input_mode == "audio"`; registers the bridge in `ClientRegistry`.
6. `bridge.health_check()` — pings each microservice; orchestrator failure is fatal, TTS failure is degraded.
7. `bridge.run()` — launches concurrent tasks: `_client_input_loop` + transcriber `listen_loop` + silence watchdog.

### JotaBridge data flow

- **Audio bytes from client** → `TranscriberClient.send_audio()` → transcription partials/finals via `_on_transcription` callback
- **`{"type":"end"}`** → `transcriber.send_end()` (signals end of utterance)
- **`{"type":"send","text":"..."}`** → `_call_orchestrator(text)` — creates a fresh `TTSClient` per turn, runs `pipe_tokens` + `pipe_audio` concurrently via `asyncio.gather`
- **Barge-in**: partial transcriptions with `len >= config.barge_in_min_chars` cancel the active orchestrator turn via `_cancel_active_turn()`, which cancels the Python task and causes `OpenClawClient` to send `chat.abort` to OpenClaw. Controlled per-client by `barge_in_enabled` and `barge_in_min_chars`.
- **Agent-initiated push**: OpenClaw sends `agent` events with `phase: "start"/"end"` and interleaved `chat` events. `FrameDispatcher` routes these to the bridge via `ClientRegistry`. `on_push_turn_start` checks `config.push_enabled` — if `False`, the push is silently dropped. Otherwise creates a TTS client, pipes audio, and sends `turn_start`/`turn_end` to the client.
- **Tool calls**: when the agent invokes a tool during a turn, OpenClaw emits a `session.tool`
  event (`phase: "start"` with `args`, then `phase: "result"` with `result`/`isError` — the
  intermediate `phase: "update"` streaming partials are dropped). If the client's
  `tool_calls_enabled` config flag is `True` (default `False`), the gateway forwards each as
  `{"type": "tool_call", "turn_id", "phase", "name", "tool_call_id", "args", "result",
  "is_error"}` over the WS — for both regular turns and agent-initiated push turns.

### Silence watchdog

`_transcription_watchdog` runs as a background task during audio sessions:

- Polls every 2s for transcription activity.
- If `elapsed > config.silence_timeout_s` and no new transcription has arrived, increments an internal counter and sends `{"type":"status","service":"transcriber","state":"degraded"}` to the client.
- If the counter reaches `config.max_silence_turns` **consecutively** (reset to 0 whenever a transcription arrives), calls `_close_all()` to terminate the session.

### Session key derivation

Each orchestrator turn uses a session key derived from the Handshake `agent` field and the client UUID:

```
session_key = f"agent:{agent}:{client.id}"
```

If `agent` is not provided in the Handshake, `gateway_info.default_agent_id` (received from OpenClaw at connect time) is used.
The HA REST endpoint (`/v1/chat/completions`) uses a fixed key: `agent:{default_agent_id}:ha`.

`client_id_from_session_key(sk)` extracts the client ID via `sk.rsplit(":", 1)[-1]` — safe for keys containing multiple colons (e.g. Telegram user IDs).

---

## Microservice clients (all in `src/services/`)

| Client | Protocol | Notes |
|---|---|---|
| `DbClient` | SQLite (SQLModel) | Singleton; reads `ClientRecord`; caches sessions 60s via TTLCache |
| `OpenClawClient` | WebSocket v4 | Singleton per app; multiplexed — N concurrent sessions on one connection; sends `chat.abort` on barge-in |
| `ReconnectingOpenClawClient` | — | Wraps `OpenClawClient`; auto-reconnects with exponential backoff; exposes state (CONNECTED/RECONNECTING/DEGRADED) |
| `TranscriberClient` | WebSocket | One instance per session (audio mode only); receives PCM Float32 16kHz |
| `TTSClient` | WebSocket | **Created fresh per turn** (both normal and push turns), not per session; receives tokens, yields PCM16 24kHz |

### OpenClaw package (`src/services/openclaw/`)

Built once in `main.py` lifespan; stored in `app.state`.

- `OpenClawClient` (`client.py`) — WebSocket v4 handshake (challenge → connect → hello-ok → **agents.list → sessions.subscribe**), persistent `_listen` task, `_keepalive_loop` (pings at 80% of `tickIntervalMs`). All events carry `sessionKey`; `stream_response()` registers a `Queue` in `TurnRegistry` per turn, sends `{"sessionKey": key}` and reads the queue until turn completion or `error` (see turn-completion note below).
- `ReconnectingOpenClawClient` (`reconnecting.py`) — wraps `OpenClawClient`; on unexpected disconnect calls `on_disconnect` hook, retries with exponential backoff up to `ORCHESTRATOR_RECONNECT_MAX_DURATION` seconds; after that enters DEGRADED state. `stream_response()` returns an `error` event immediately when not CONNECTED.
- `TurnRegistry` (`registry.py`) — dual-index dict (`session_key → Queue`, `req_id → session_key`); `FrameDispatcher` uses it to route response frames to the correct waiting `stream_response()` call.
- `ClientRegistry` (`registry.py`) — maps `client_id → JotaBridge`; used by `FrameDispatcher` to deliver agent-initiated push events to the right session.
- `FrameDispatcher` (`dispatcher.py`) — called by `_listen` for every incoming frame; routes `res` frames to `TurnRegistry`, `chat` events to active turn queue or bridge push hooks, `agent` phase events to `on_push_turn_start`/`on_push_turn_end`, `session.tool` events (`phase: "start"`/`"result"`, `"update"` dropped) to the active turn queue or `bridge.deliver_push_tool_call`.
- `GatewayInfo` / `AgentInfo` (`models.py`) — `default_agent_id`/`tick_interval_ms`/etc. come from the `hello-ok` payload; the **agent roster itself no longer does** (OpenClaw server 2026.6.11+ stopped embedding it in `hello-ok`'s `snapshot`). `OpenClawClient.connect()` fetches it explicitly via `agents.list` right after `hello-ok` and merges it in via `GatewayInfo.update_agents_from_list()`. Without this, `has_agent()` silently rejects every named agent — clients that omit `agent` in the Handshake are unaffected, since the fallback `default_agent_id` still comes from `sessionDefaults`.
- `ToolCallEvent` (`models.py`) — parsed from a `session.tool` event's `data` sub-object; only `start`/`result` phases are surfaced (`update` streaming-partials are dropped). Forwarded to clients as a `{"type": "tool_call", ...}` WS message only when `ClientConfig.tool_calls_enabled` is `True` (default `False`).

**Turn-completion signal:** OpenClaw server 2026.6.11+ never sends a second `res` for `chat.send` — the real completion signal is a `chat` event with `payload.state == "final"`. `stream_response()` treats that as `status: "done"` and ends the turn; the old `res`-based `done`/`status` handling is kept only as a fallback for older server versions, but production no longer relies on it. Assume this if a turn ever appears to hang after a token is received but before `turn_end` reaches the client — check `state` on the `chat` events, not for a stray `res`.

OpenClaw connection details (loopback backend mode, no device signature required):
- Token: `OPENCLAW_TOKEN`
- Port: `OPENCLAW_PORT` (default 18789)

**Keep the OpenClaw protocol knowledge base in sync.** OpenClaw's wire protocol drifts between
minor server versions with no changelog (the `agents.list`/`chat.state=="final"` breaks above
were both discovered live, by accident, not from release notes). The canonical write-up of the
wire protocol — including every drift found so far — lives in the `openclaw` skill at
`~/.claude/skills/openclaw/references/protocol.md` (see also `tools.md`, `config-schema.md`,
`green-house.md` in that same directory), **not** in this file. Whenever you discover a new
protocol change, inconsistency, or undocumented event shape while working on this codebase
(via raw-frame capture, a failing assumption, a hanging turn, etc.):
1. Update `protocol.md` (or the relevant sibling doc) with what you found, dated, including the
   server version if known — follow the existing "Breaking change — vX.Y.Z" style for anything
   that silently breaks old client code.
2. Only mirror the parts of that finding that are specific to *this repo's* implementation here
   in `CLAUDE.md` (as done above for `agents.list` and turn-completion) — the skill doc is the
   source of truth for the protocol itself, this file is for how jota-gateway's code responds to it.
Skipping this is how the same drift gets silently re-discovered (and re-debugged from scratch)
in a future session.

---

## Admin API authentication

All `/admin/*` routes use `Depends(get_admin_auth)` (`src/api/deps.py`), which reads the `X-Admin-Token` header and compares it against `settings.ADMIN_TOKEN`. Returns 422 if the header is missing, 401 if wrong, 503 if `ADMIN_TOKEN` is not configured.

The `/v1/*` routes have **no auth** — they are exposed only on LAN via nginx.

---

## Cache pattern (`src/core/cache.py`)

`make_cache(maxsize, ttl)` returns `(TTLCache, asyncio.Lock)`. The lock must wrap **only dict access**, never IO calls — acquire lock to check/set, release before any await.

---

## Docker & data persistence

`docker-compose.yml` mounts `./data:/app/data`. The SQLite file at `data/gateway.db` lives on the host and survives container rebuilds. `data/` is in `.gitignore`.

On first startup (`create_db_and_tables()` in the lifespan), the schema is created automatically if the file doesn't exist.

---

## Testing

Tests live in `tests/integration/` and `tests/unit/`.

- **Integration tests** use SQLite in-memory (via `StaticPool`) injected by the `db_engine` fixture. There are no HTTP mocks for client auth — the DB is seeded directly. WebSocket tests use `starlette.testclient.TestClient` with fake WebSocket servers in background threads.
- **Unit tests** (`tests/unit/`) test bridge behavior (barge-in, disconnect, send guards, watchdog, push) using lightweight mock objects without a running app.

### Key fixtures (`tests/integration/conftest.py`)

| Fixture | Scope | autouse | Purpose |
|---|---|---|---|
| `db_engine` | function | ✓ | Creates `sqlite:///:memory:` with `StaticPool`, monkeypatches `src.db.database._engine` |
| `clear_db_cache` | function | ✓ | Clears `db_client._session_cache` before/after each test |
| `configure_admin_token` | function | ✓ | Sets `settings.ADMIN_TOKEN = "test-admin-token"` |
| `seed_client` | function | ✗ | Inserts the standard test `ClientRecord` (`VALID_KEY`, `CLIENT_ID`) — only used where needed |
| `mock_services` | function | ✗ | `respx` mock for transcriber + TTS health checks only |
| `client` | function | ✗ | Full `TestClient(app)` with seeded client + mock orchestrator |

The `StaticPool` is required for SQLite `:memory:` so all connections share one database (the default creates a new empty DB per connection).

`seed_client` is **not** autouse — admin tests need an empty DB. Add it as a parameter only to tests that send a WS handshake with `VALID_KEY`.

### OpenClaw tests

`OpenClawClient` tests (`test_openclaw_client.py`) use `SmartFakeWS` — a queue-backed fake WebSocket that auto-responds to the v4 handshake (challenge → connect → hello-ok → agents.list → subscribe ack) and per-test `chat.send` sequences. Constructor accepts `hello_ok_payload`/`agents_list_payload` overrides (for exercising the agents-roster-fetch path) and `chat_responses`/`tool_calls` (for turn content and `session.tool` events).

`ReconnectingOpenClawClient` tests (`test_reconnecting_openclaw.py`) stub `OpenClawClient` to test backoff, DEGRADED state, and the `on_disconnect` hook contract.
