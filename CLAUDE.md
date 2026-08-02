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

> 📋 **Estado del proyecto:** consulta [`docs/ROADMAP.md`](docs/ROADMAP.md) para el plan vivo de remediación (40 issues `#99`–`#138`, 5 fases). La auditoría completa está en [`docs/superpowers/plans/2026-07-15-production-readiness-roadmap.md`](docs/superpowers/plans/2026-07-15-production-readiness-roadmap.md). Cuando trabajes en una issue, márcala como `[x]` en el roadmap al cerrarla.

jota-gateway is a **BFF (Backend For Frontend)** — the single entry point for all clients (ESP32, web, app, Home Assistant). It has four surfaces:

- **WebSocket** `/ws/stream` — full voice+text session managed by `JotaBridge`
- **Admin REST** `/admin/*` — client CRUD + observability (sessions, orchestrators); requires `X-Admin-Token`
- **OpenAI-compatible REST** `/v1/*` — `GET /v1/models` and `POST /v1/chat/completions` for Home Assistant integration; delegates to the singleton `OpenClawClient`. Requests from a trusted origin (loopback and/or `TRUSTED_NETWORKS`) need no auth; anyone else must send `Authorization: Bearer <client_key>`, validated against the same `ClientRecord` table the WS handshake uses (`src/core/network.py`, `resolve_ha_caller` in `src/api/openai_routes.py`) — see issue #52.
- **Health** `/healthz`, `/ready` — liveness and readiness probes

There is no longer any dependency on `jota-db`. Client identity and configuration live in the local SQLite database (`data/gateway.db`).

### Environment variables

```
DATABASE_URL=sqlite:///data/gateway.db   # path to SQLite file
ADMIN_TOKEN=<secret>                     # required for /admin/* routes

TRUST_LOOPBACK=true                      # 127.0.0.1/::1 exempt from /v1/* auth
TRUSTED_NETWORKS=                        # CSV of CIDRs exempt from /v1/* auth, e.g. 192.168.1.0/24. Empty = fail-closed.
TRUSTED_PROXIES=127.0.0.1,::1            # CSV of IPs/CIDRs allowed to set X-Real-IP for /v1/*

TRANSCRIBER_WS_URL=localhost:9000
TTS_WS_URL=localhost:8005
TTS_TOKEN=gateway
TTS_AUTH_TIMEOUT_S=10.0

OPENCLAW_HOST=127.0.0.1
OPENCLAW_PORT=18789
OPENCLAW_TOKEN=<secret>

ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF=1.0
ORCHESTRATOR_RECONNECT_MAX_BACKOFF=60.0
ORCHESTRATOR_RECONNECT_MAX_DURATION=300.0

TRANSCRIBER_RECONNECT_INITIAL_BACKOFF=1.0
TRANSCRIBER_RECONNECT_MAX_BACKOFF=60.0
TRANSCRIBER_RECONNECT_MAX_DURATION=300.0
TTS_RECONNECT_INITIAL_BACKOFF=1.0
TTS_RECONNECT_MAX_BACKOFF=60.0

HANDSHAKE_TIMEOUT_S=10.0
TURN_TIMEOUT_S=120.0
IDLE_TIMEOUT_S=300.0
SHUTDOWN_DRAIN_S=30.0
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
`stt_language`, `stt_vad_thold`, `tts_voice`, `tts_speed`, `barge_in_enabled`, `barge_in_min_chars`, `silence_timeout_s`, `max_silence_turns`, `push_enabled`.

The singleton `db_client = DbClient()` is imported from this module everywhere. An optional `engine` constructor parameter allows injecting a test engine.

`invalidate()` must be called **after** the triggering `session.commit()`, never before — calling it first reopens the exact cache-repopulation race the generation-counter guard exists to close (see the Cache pattern section).

---

## WebSocket session lifecycle (`routes.py` → `bridge.py`)

1. Client connects and sends a **Handshake** JSON. The initial `websocket.receive_text()` wait
   for it is bounded by `HANDSHAKE_TIMEOUT_S` (issue #115) — a client that connects and never
   sends anything gets closed with code 1008 instead of holding the connection open forever.
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
4. `JotaBridge` is instantiated with client, config, WebSocket, the singleton `ReconnectingOpenClawClient` (from `app.state.openclaw`), the singleton `ReconnectingTTSClient` (from `app.state.tts`), `app.state.client_registry`, and `default_agent`.
5. `bridge.connect_internal_services()` — starts a `ReconnectingTranscriberClient` only if `input_mode == "audio"`; registers the bridge in `ClientRegistry`. `ReconnectingTranscriberClient.connect()` never raises — a failed initial connect just leaves it in `RECONNECTING` state for `health_check()`/the background `run()` loop to handle, it no longer aborts session setup.
6. `bridge.health_check()` — pings each microservice; **only the orchestrator is fatal** (its failure returns `False`, closing the WebSocket with code 1011 before `ready` is ever sent). Transcriber and TTS failures are both non-fatal — the session opens normally and the client is notified via `status` messages (see "Service reconnection" below).
7. `bridge.run()` — launches concurrent tasks: `_client_input_loop` + idle watchdog + `transcriber.run()` (listen + background reconnect) + silence watchdog.

### JotaBridge data flow

- **Audio bytes from client** → `TranscriberClient.send_audio()` → transcription partials/finals via `_on_transcription` callback
- **`{"type":"end"}`** → `transcriber.send_end()` (signals end of utterance)
- **`{"type":"send","text":"..."}`** → `_call_orchestrator(text)` — creates a fresh `TTSClient` per turn, runs `pipe_tokens` + `pipe_audio` concurrently via `asyncio.gather`
- **Barge-in**: partial transcriptions with `len >= config.barge_in_min_chars` cancel the active orchestrator turn via `_cancel_active_turn()`, which cancels the Python task and causes `OpenClawClient` to send `chat.abort` to OpenClaw. Controlled per-client by `barge_in_enabled` and `barge_in_min_chars`.
- **Agent-initiated push**: OpenClaw sends `agent` events with `phase: "start"/"end"` and interleaved `chat` events. `FrameDispatcher` routes these to the bridge via `ClientRegistry`. `on_push_turn_start` checks `config.push_enabled` — if `False`, the push is silently dropped. Otherwise creates a TTS client, pipes audio, and sends `turn_start`/`turn_end` to the client. **Multiple agent start/end pairs collapse into one client-facing turn** (issue #84): OpenClaw emits N pairs per LLM response when the agent does tool use or multi-step reasoning, and the gateway used to forward them 1:1, flooding the client. `JotaBridge` now tracks `_push_turn_open` — the first `agent` start opens the logical turn; subsequent `agent` starts received while a turn is already open are dropped silently, and `agent` ends received with no open turn are also dropped. The client always sees exactly one `turn_start`/`turn_end` pair per push reply.
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
- If the counter reaches `config.max_silence_turns` **consecutively** (reset to 0 whenever a transcription arrives), calls `close_all()` to terminate the session.
- Only exits permanently when `self.transcriber.state == ConnectionState.DEGRADED` (or the transcriber was never constructed). A transient `RECONNECTING` blip — the transcriber's background reconnect loop retrying after an unexpected drop — no longer kills the watchdog; it skips silence-counting for that tick and resumes normally once the transcriber is back to `CONNECTED`. Before this fix, any drop (even a successfully-recovered one) permanently stopped silence monitoring for the rest of the session.
- **Recovery grace baseline (issue #149):** `TranscriberClient.connect()` never resets `_last_transcription_at` — it only changes inside `listen_loop()` when a real transcription arrives. So the instant the watchdog observes `RECONNECTING → CONNECTED`, that field still holds the pre-outage timestamp; measuring `elapsed` against it would count the whole outage as silence and force-close the session within a couple of ticks of a *successful* recovery. The watchdog tracks its own `recovery_baseline` (reset to `time.monotonic()` the tick it first sees `CONNECTED` again after a drop) and measures `elapsed` against that instead, until a real new transcription supersedes it. Ongoing silence *after* recovery still counts normally — this only removes the outage duration itself from the count.

### Idle watchdog

`_idle_watchdog` runs as a background task for every session, launched by `run()` alongside
`_client_input_loop` regardless of `input_mode` — unlike the silence watchdog above, it applies
uniformly to text and audio sessions, not just audio ones.

- Tracks `_last_client_activity`, updated on every inbound message in `_client_input_loop`
  (audio bytes or text frames alike). If `IDLE_TIMEOUT_S` elapses with no inbound message at
  all, the session is closed via `close_all()` — **with no warning sent to the client first**,
  unlike the silence watchdog's progressive `status: degraded` notices.
- **Gated on activity actually in flight (issue #115 follow-up):** a client can legitimately go
  quiet while the server is still working — the orchestrator streaming a long response, or a
  push-only consumer session that never sends anything by design. Before closing, the watchdog
  checks `self._active_turn` (not done) and `self._push_turn_open`; if either indicates
  something is in flight, it skips the close for that tick and re-checks again after a short
  fixed interval (2s, matching the silence watchdog's poll interval) instead of closing or
  resetting the full idle window. Once nothing is in flight anymore, idle-timeout behavior
  resumes normally, measured from `_last_client_activity` as before.

### Session key derivation

Each orchestrator turn uses a session key derived from the agent name and the client UUID:

```
session_key = f"agent:{agent}:{client.id}"
```

The `agent` is resolved by `src.core.agent_policy.resolve_agent(requested, client_config, gateway_info)` at the call site — WS handshake in `routes.py`, REST `/v1/chat/completions` in `openai_routes.py`. The cascade is:

1. `requested` — `handshake.agent` (WS) or `body.model` (REST), if non-empty after stripping.
2. `client_config.default_agent` — set per-client by admin via the CRUD.
3. `gateway_info.default_agent_id` — server-wide default from OpenClaw's `hello-ok`.
4. `"main"` — last-resort fallback (preserves legacy REST trusted-origin behavior when neither config nor gateway info is available).

Validation runs after the cascade (issue #105):

- If `client_config.allowed_agents` is an explicit list (`None` skips this check), the resolved agent must be in it.
- If the agent was explicitly requested (`requested is not None`) and `gateway_info` is available, the resolved agent must be in `gateway_info.agents`. Cascade defaults are trusted server-side configuration, not user input — they skip the roster check.

WS violation → close 1008 with a specific reason. REST violation → 403 JSON body with `{"error":"forbidden","reason":"agent_not_permitted"|"agent_not_available","message":...}`. The REST legacy trusted-origin path (no Bearer) skips `resolve_agent` entirely and uses the gateway default directly — preserves historical behavior for loopback callers.

The HA REST endpoint (`/v1/chat/completions`) uses `client_id="ha"` for the trusted-origin legacy path and the caller's UUID otherwise.

`client_id_from_session_key(sk)` extracts the client ID via `sk.rsplit(":", 1)[-1]` — safe for keys containing multiple colons (e.g. Telegram user IDs).

---

## Microservice clients (all in `src/services/`)

| Client | Protocol | Notes |
|---|---|---|
| `DbClient` | SQLite (SQLModel) | Singleton; reads `ClientRecord`; caches sessions 60s via TTLCache |
| `OpenClawClient` | WebSocket v4 | Singleton per app; multiplexed — N concurrent sessions on one connection; sends `chat.abort` on barge-in |
| `ReconnectingOpenClawClient` | — | Wraps `OpenClawClient`; auto-reconnects with exponential backoff; exposes state (CONNECTED/RECONNECTING/DEGRADED) |
| `TranscriberClient` | WebSocket | Protocol-only, unchanged; one instance per session (audio mode only); receives PCM Float32 16kHz |
| `ReconnectingTranscriberClient` | — | Wraps `TranscriberClient`; **one per audio session**, constructed in `connect_internal_services()`; same CONNECTED/RECONNECTING/DEGRADED state machine as the orchestrator, background-task-driven (`run()` supervises listen + reconnect for the session's lifetime) |
| `TTSClient` | WebSocket | Protocol-only, unchanged; **created fresh per turn** (both normal and push turns), not per session; receives tokens, yields PCM16 24kHz |
| `ReconnectingTTSClient` | — | Wraps `TTSClient`; **process-level singleton** (`app.state.tts`, one per app, shared across all sessions) since TTS has no persistent connection to hold — a lazy backoff gate checked on each new per-turn attempt instead of a background reconnect loop |

### Service reconnection (`src/services/reconnection.py`)

`ConnectionState` (`CONNECTED`/`RECONNECTING`/`DEGRADED`) and `ServiceStatus` (dataclass: `name`, `state`, `connected_at`, `reconnect_attempts`, `last_error`) are shared across all three reconnection wrappers — `ReconnectingOpenClawClient`, `ReconnectingTranscriberClient`, `ReconnectingTTSClient` — so the pattern reads as one system rather than three accidental variations. `to_wire_state(state)` maps a transition to the client-facing `status` wire vocabulary (`CONNECTED→"restored"`, `RECONNECTING→"reconnecting"`, `DEGRADED→"unavailable"`); callers decide *whether* a transition merits notifying (e.g. never fire "restored" for a session's very first successful connect, since nothing was ever broken), this only decides *what word* to send.

- **Trigger mechanism differs by service lifetime, deliberately**: OpenClaw and Transcriber hold a real socket open continuously, so losing it is an event (`on_disconnect` callback) and recovery is a background task retrying with backoff. TTS has no socket between turns by design (`TTSClient` is reconstructed every turn) — there's nothing to hold open in the background, so `ReconnectingTTSClient.connect()` is a lazy gate: if the last failure was more recent than the current backoff window, it returns `None` immediately without even attempting a socket; otherwise it tries, and records success/failure. No `DEGRADED` terminal state and no max-duration for TTS — every eligible turn always gets a fresh attempt, capped at 60s between attempts.
- **Client notification** — every state-change path funnels through `JotaBridge.notify_service_status(service, state)`, a thin wrapper around `client_ws.send_json({"type":"status",...})`. Never a separate ad-hoc send call site.
  - **Transcriber**: `ReconnectingTranscriberClient.on_state_change` is wired *after* the session's initial `connect()` call (not before) to avoid a spurious `"restored"` notice on a normal first-time success.
  - **TTS**: since the singleton is shared, `JotaBridge._maybe_notify_tts_state()` tracks a per-bridge `_tts_degraded_notified` flag and compares it against `app.state.tts.status().state` after each attempted turn, sending `status` only on an actual transition — avoids spamming a message on every turn while the breaker is open.
  - **Orchestrator**: `ClientRegistry.broadcast_status(service, state)` — wired via `openclaw.on_state_change` in `main.py`'s lifespan, after the initial `connect()` — notifies **every** connected session, not just the one attempting a turn. Closes a gap where an idle-but-connected client only learned about a drop/recovery reactively, on its next turn attempt.
- **Never force-closes a session**: only the orchestrator remains a fatal dependency (in `health_check()`, at session start). Transcriber/TTS failures — at start or mid-session — degrade the session, they never close the WebSocket.
- **Settings are dedicated per service** (`TRANSCRIBER_RECONNECT_*`, `TTS_RECONNECT_*`), not shared with `ORCHESTRATOR_RECONNECT_*` — see Environment variables above.
- **Admin observability**: `GET /admin/transcriber/status` (live reachability via `TranscriberClient.ping()`, since Transcriber has no process-level connection to report — one instance per session) and `GET /admin/tts/status` (`app.state.tts.status()` — real memory of consecutive failures, current backoff, last error), same shape as the pre-existing `GET /admin/orchestrators/{name}/status`.

### OpenClaw package (`src/services/openclaw/`)

Built once in `main.py` lifespan; stored in `app.state`.

- `OpenClawClient` (`client.py`) — WebSocket v4 handshake (challenge → connect → hello-ok → **agents.list → sessions.subscribe**), persistent `_listen` task, `_keepalive_loop` (pings at 80% of `tickIntervalMs`). All events carry `sessionKey`; `stream_response()` registers a `Queue` in `TurnRegistry` per turn, sends `{"sessionKey": key}` and reads the queue until turn completion or `error` (see turn-completion note below). `connect()` is serialized by a `_connect_lock` and always tears down (`_cancel_and_close()`) the previous `_ws`/`_listener_task`/`_keepalive_task` before opening a new socket — otherwise the old listener could start racing the new handshake for frames, or dispatch stale frames to the shared `TurnRegistry`, once it actually got scheduled to run (issue #103). Each `queue.get()` wait inside that loop is bounded by `TURN_TIMEOUT_S` (issue #115) with idle-reset semantics — the timeout resets on every event received, so a turn making steady progress can run indefinitely; only a genuine gap longer than `TURN_TIMEOUT_S` between events yields a `turn_timeout` error.
- `ReconnectingOpenClawClient` (`reconnecting.py`) — wraps `OpenClawClient`; on unexpected disconnect calls `on_disconnect` hook, retries with exponential backoff up to `ORCHESTRATOR_RECONNECT_MAX_DURATION` seconds; after that enters DEGRADED state. `stream_response()` returns an `error` event immediately when not CONNECTED. `trigger_reconnect()` is the admin-facing entry point (`POST /admin/orchestrators/{name}/reconnect`) — it coalesces onto any reconnect already in flight via the same `_reconnect_task`/job-id tracking the background loop uses, and returns immediately with a job id rather than blocking on the handshake, so an admin-triggered reconnect can never race the background loop into opening two sockets. **Circuit breaker (issue #102):** `_reconnect_exhausted` is set once `_reconnect_loop()` hits `max_duration` and enters DEGRADED; while set, `_ensure_reconnecting()` (called from `ping()`, `stream_response()`, and `on_disconnect`) returns immediately without spawning a new `_reconnect_task`, so DEGRADED no longer relaunches a full reconnect window on every probe/request. It's cleared only by a successful `connect()` or by `trigger_reconnect()` — so an admin-triggered reconnect always gets a fresh attempt even mid-DEGRADED. **Nested-generator cleanup (issue #150):** `stream_response()` wraps its own iteration of the inner `OpenClawClient.stream_response()` in its own `contextlib.aclosing()`, not just a plain `async for`. In production `orchestrator` (in `call_orchestrator()`) is always this class, so the `aclosing()` PR #147 added to `call_orchestrator()` only closes *this* generator — without this inner `aclosing()`, closing it early (e.g. `call_orchestrator` raising on an `error` event) throws `GeneratorExit` at this generator's own suspended `yield`, which propagates straight out without ever resuming/closing the inner `OpenClawClient` generator, so its `finally: self._turn_registry.unregister(...)` gets deferred to the asyncgen GC finalizer instead of running synchronously — reopening the exact TurnRegistry race #99/#147 closed, one layer removed.
- `TurnRegistry` (`registry.py`) — dual-index dict (`session_key → Queue`, `req_id → session_key`); `FrameDispatcher` uses it to route response frames to the correct waiting `stream_response()` call.
- `ClientRegistry` (`registry.py`) — maps `client_id → JotaBridge`; used by `FrameDispatcher` to deliver agent-initiated push events to the right session.
- `FrameDispatcher` (`dispatcher.py`) — called by `_listen` for every incoming frame; routes `res` frames to `TurnRegistry`, `chat` events to active turn queue or bridge push hooks, `agent` phase events to `on_push_turn_start`/`on_push_turn_end`, `session.tool` events (`phase: "start"`/`"result"`, `"update"` dropped) to the active turn queue or `bridge.deliver_push_tool_call`.
- `GatewayInfo` / `AgentInfo` (`models.py`) — `default_agent_id`/`tick_interval_ms`/etc. come from the `hello-ok` payload; the **agent roster itself no longer does** (OpenClaw server 2026.6.11+ stopped embedding it in `hello-ok`'s `snapshot`). `OpenClawClient.connect()` fetches it explicitly via `agents.list` right after `hello-ok` and merges it in via `GatewayInfo.update_agents_from_list()`. Without this, `has_agent()` silently rejects every named agent — clients that omit `agent` in the Handshake are unaffected, since the fallback `default_agent_id` still comes from `sessionDefaults`.
- `ToolCallEvent` (`models.py`) — parsed from a `session.tool` event's `data` sub-object; only `start`/`result` phases are surfaced (`update` streaming-partials are dropped). Forwarded to clients as a `{"type": "tool_call", ...}` WS message only when `ClientConfig.tool_calls_enabled` is `True` (default `False`).

**Turn-completion signal:** OpenClaw server 2026.6.11+ never sends a second `res` for `chat.send` — the real completion signal is a `chat` event with `payload.state == "final"`. `stream_response()` treats that as `status: "done"` and ends the turn; the old `res`-based `done`/`status` handling is kept only as a fallback for older server versions, but production no longer relies on it. Assume this if a turn ever appears to hang after a token is received but before `turn_end` reaches the client — check `state` on the `chat` events, not for a stray `res`.

OpenClaw connection details (loopback backend mode, no device signature required):
- Token: `OPENCLAW_TOKEN`
- Port: `OPENCLAW_PORT` (default 18789)

**Keep `docs/openclaw-protocol.md` in sync.** OpenClaw's wire protocol drifts between minor
server versions with no changelog (the `agents.list`/`chat.state=="final"` breaks above were
both discovered live, by accident, not from release notes). `docs/openclaw-protocol.md` in
this repo (git-tracked, versioned) is jota-gateway's own hyper-current technical record of
what the protocol actually does, as observed against the real running instance — separate from
the general-purpose `openclaw` skill (`~/.claude/skills/openclaw/`), which explains what
OpenClaw is project-agnostically and is not meant to track this level of fast-moving detail.
Whenever you discover a new protocol change, inconsistency, or undocumented event shape while
working on this codebase (via raw-frame capture, a failing assumption, a hanging turn, etc.):
1. Update `docs/openclaw-protocol.md` with what you found, dated, including the server version
   if known — follow the existing "Breaking change — vX.Y.Z" style for anything that silently
   breaks old client code.
2. Only mirror the parts of that finding that are specific to *this repo's implementation* here
   in `CLAUDE.md` (as done above for `agents.list` and turn-completion) — `docs/openclaw-protocol.md`
   is the source of truth for the protocol itself, this file is for how jota-gateway's code
   responds to it.
Skipping this is how the same drift gets silently re-discovered (and re-debugged from scratch)
in a future session.

---

## Admin API authentication

All `/admin/*` routes use `Depends(get_admin_auth)` (`src/api/deps.py`), which reads the `X-Admin-Token` header and compares it against `settings.ADMIN_TOKEN`. Returns 422 if the header is missing, 401 if wrong, 503 if `ADMIN_TOKEN` is not configured.

The `/v1/*` routes use `Depends(resolve_ha_caller)` (`src/api/openai_routes.py`) instead — see `src/core/network.py` and the trusted-origin bullet under Architecture above (issue #52).

---

## Cache pattern (`src/core/cache.py`)

`make_cache(maxsize, ttl)` returns `(TTLCache, threading.Lock)`. A real OS-level mutex, not `asyncio.Lock`, because callers span both the event loop (async handlers) and Starlette's threadpool (sync `def` route handlers, e.g. `admin_routes.py`) — `asyncio.Lock` is only safe to acquire from the loop it's bound to. The lock must wrap **only dict access, including TTLCache's internal expiry housekeeping** (`in`, `[]`, `.pop()` all trigger it), never IO calls — acquire lock to check/set, release before any query or await.

Callers that cache the result of an external lookup (e.g. `DbClient._session_cache`) must guard against a stale write racing a concurrent invalidation: capture a per-key generation counter under the lock before the lookup, then only write to cache if the generation is unchanged after the lookup completes. See `DbClient.get_session` / `DbClient.invalidate` for the reference implementation.

---

## Docker & data persistence

`docker-compose.yml` mounts `./data:/app/data`. The SQLite file at `data/gateway.db` lives on the host and survives container rebuilds. `data/` is in `.gitignore`.

On first startup (`create_db_and_tables()` in the lifespan), the schema is created automatically if the file doesn't exist.

---

## Production readiness / log hygiene

`client_key` is a bearer credential and must never be written to logs in full or as a plaintext prefix. When correlation is necessary, use `src.core.logging.fingerprint_key(value)`, which returns the first 8 hexadecimal characters of the value's SHA-256 digest, and log it as `fp=<fingerprint>`. After successful authentication, use `client.id` as the primary correlation ID.

Inbound HTTP requests and WebSocket connections receive a gateway-owned UUID4 in `scope["state"]["request_id"]` via `RequestIdMiddleware`. This identifier is local to the gateway and unrelated to OpenClaw `req_id`, `turn_id`, or `session_key`. Source IP resolution for HTTP and WebSocket must go through `resolve_client_ip()` so `X-Real-IP` is trusted only from `TRUSTED_PROXIES`.

User transcripts must not be logged at INFO or above. DEBUG transcript logs must be deliberately truncated; the current maximum for final transcriptions is 40 characters.

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
