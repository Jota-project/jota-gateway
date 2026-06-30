# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the server in development
uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload

# Run all tests
PYTHONPATH=. pytest

# Run a specific test file
PYTHONPATH=. pytest tests/integration/test_rest_auth.py

# Run a specific test by name
PYTHONPATH=. pytest -k "test_valid_api_key_passes"

# Lint
ruff check src/ tests/

# Docker
docker compose up
```

## Architecture

jota-gateway is a **BFF (Backend For Frontend)** — the single entry point for all clients (ESP32, web, app, Home Assistant). It has three surfaces:

- **WebSocket** `/ws/stream` — full voice+text session managed by `JotaBridge`
- **REST API** `/api/*` — thin proxy to jota-db for config, conversations, models, health + internal observability endpoints
- **OpenAI-compatible REST** `/v1/*` — `GET /v1/models` and `POST /v1/chat/completions` for Home Assistant integration; delegates to the singleton `OpenClawClient`

### URL convention in settings

All `Settings` fields for external services are `host:port` **without protocol**. Each service client injects the protocol itself at construction time (`http://`, `ws://`). Never store full URLs in settings.

### WebSocket session lifecycle (`routes.py` → `bridge.py`)

1. Client connects and sends a **Handshake** JSON:
   ```json
   {
     "client_key": "...",
     "input_mode": "audio" | "text",
     "output_mode": ["audio", "text", "status"],
     "agent": "assistant"   // optional — OpenClaw agent name; defaults to gateway_info.default_agent_id
   }
   ```
2. Gateway resolves identity via `db_client.get_session(client_key)` → `(Client, ClientConfig)`
3. If `agent` is specified, `routes.py` validates it against `openclaw.gateway_info.has_agent(agent)` — unknown agents close with code 1008.
4. `JotaBridge` is instantiated with client, config, WebSocket, the singleton `ReconnectingOpenClawClient` (from `app.state.openclaw`), `app.state.client_registry`, and `default_agent`.
5. `bridge.connect_internal_services()` — starts `TranscriberClient` only if `input_mode == "audio"`; registers the bridge in `ClientRegistry`
6. `bridge.health_check()` — pings each microservice; orchestrator failure is fatal, TTS failure is degraded
7. `bridge.run()` — launches concurrent tasks: `_client_input_loop` + transcriber `listen_loop` + silence watchdog

### JotaBridge data flow

- **Audio bytes from client** → `TranscriberClient.send_audio()` → transcription partials/finals via `_on_transcription` callback
- **`{"type":"end"}`** → `transcriber.send_end()` (signals end of utterance)
- **`{"type":"send","text":"..."}`** → `_call_orchestrator(text)` — creates a fresh `TTSClient` per turn, runs `pipe_tokens` + `pipe_audio` concurrently via `asyncio.gather`
- **Barge-in**: partial transcriptions with `len >= config.barge_in_min_chars` cancel the active orchestrator turn via `_cancel_active_turn()`, which cancels the Python task and causes `OpenClawClient` to send `chat.abort` to OpenClaw
- **Agent-initiated push**: OpenClaw sends `agent` events with `phase: "start"/"end"` and interleaved `chat` events without a prior client request. `FrameDispatcher` routes these to the bridge via `ClientRegistry`. The bridge hooks (`on_push_turn_start`, `deliver_push`, `on_push_turn_end`) handle TTS creation, audio piping, and teardown.

### Session key derivation

Each orchestrator turn uses a session key derived from the Handshake `agent` field and the client UUID:

```
session_key = f"agent:{agent}:{client.id}"
```

If `agent` is not provided in the Handshake, `gateway_info.default_agent_id` (received from OpenClaw at connect time) is used.
The HA REST endpoint (`/v1/chat/completions`) uses a fixed key: `agent:{default_agent_id}:ha`.

`client_id_from_session_key(sk)` extracts the client ID via `sk.rsplit(":", 1)[-1]` — safe for keys containing multiple colons (e.g. Telegram user IDs).

### Microservice clients (all in `src/services/`)

| Client | Protocol | Notes |
|---|---|---|
| `DbClient` | HTTP (httpx) | Singleton; `connect()`/`close()` in app lifespan; caches sessions 60s, models 300s |
| `OpenClawClient` | WebSocket v4 | Singleton per app; multiplexed — N concurrent sessions on one connection; sends `chat.abort` on barge-in |
| `ReconnectingOpenClawClient` | — | Wraps `OpenClawClient`; auto-reconnects with exponential backoff; exposes state (CONNECTED/RECONNECTING/DEGRADED) |
| `TranscriberClient` | WebSocket | One instance per session (audio mode only); receives PCM Float32 16kHz |
| `TTSClient` | WebSocket | **Created fresh per turn** (both normal and push turns), not per session; receives tokens, yields PCM16 24kHz |

### OpenClaw package (`src/services/openclaw/`)

Built once in `main.py` lifespan; stored in `app.state`.

- `OpenClawClient` (`client.py`) — WebSocket v4 handshake (challenge → connect → hello-ok → sessions.subscribe), persistent `_listen` task, `_keepalive_loop` (pings at 80% of `tickIntervalMs`). All events carry `sessionKey`; `stream_response()` registers a `Queue` in `TurnRegistry` per turn, sends `{"sessionKey": key}` and reads the queue until `done`/`error`.
- `ReconnectingOpenClawClient` (`reconnecting.py`) — wraps `OpenClawClient`; on unexpected disconnect calls `on_disconnect` hook, retries with exponential backoff up to `ORCHESTRATOR_RECONNECT_MAX_DURATION` seconds; after that enters DEGRADED state. `stream_response()` returns an `error` event immediately when not CONNECTED.
- `TurnRegistry` (`registry.py`) — dual-index dict (`session_key → Queue`, `req_id → session_key`); `FrameDispatcher` uses it to route response frames to the correct waiting `stream_response()` call.
- `ClientRegistry` (`registry.py`) — maps `client_id → JotaBridge`; used by `FrameDispatcher` to deliver agent-initiated push events to the right session.
- `FrameDispatcher` (`dispatcher.py`) — called by `_listen` for every incoming frame; routes `res` frames to `TurnRegistry`, `chat` events to active turn queue or bridge push hooks, `agent` phase events to `on_push_turn_start`/`on_push_turn_end`.
- `GatewayInfo` / `AgentInfo` (`models.py`) — parsed from the `hello-ok` payload; `gateway_info.default_agent_id` is used as the fallback agent when the client doesn't specify one.

OpenClaw connection details (loopback backend mode, no device signature required):
- Token: `OPENCLAW_TOKEN`
- Port: `OPENCLAW_PORT` (default 18789)

### REST API authentication

All `/api/*` routes use `Depends(get_verified_client)` (`src/api/deps.py`), which reads the `X-API-Key` header and calls `db_client.get_session()` — same path as the WebSocket handshake. Returns `(Client, ClientConfig)`.

The `/v1/*` routes have **no auth** — they are exposed only on LAN via nginx.

### Cache pattern (`src/core/cache.py`)

`make_cache(maxsize, ttl)` returns `(TTLCache, asyncio.Lock)`. The lock must wrap **only dict access**, never IO calls — acquire lock to check/set, release before any await.

## Testing

Tests live in `tests/integration/` and `tests/unit/`.

- **Integration tests** use `respx` to intercept HTTP calls to jota-db; WebSocket tests use `starlette.testclient.TestClient` with fake WebSocket servers in background threads
- **Unit tests** (`tests/unit/`) test bridge behavior (barge-in, disconnect, send guards) using lightweight mock objects without a running app

The `mock_services` fixture (in `tests/integration/conftest.py`) mocks all downstream HTTP: jota-db auth, config, conversations, models, TTS health. Individual tests can override specific routes.

The `clear_db_cache` fixture runs `autouse=True` for all integration tests to prevent session cache leakage between tests.

`OpenClawClient` tests (`test_openclaw_client.py`) use `SmartFakeWS` — a queue-backed fake WebSocket that auto-responds to the v4 handshake (challenge → connect → hello-ok → subscribe ack) and per-test `chat.send` sequences.

`ReconnectingOpenClawClient` tests (`test_reconnecting_openclaw.py`) stub `OpenClawClient` to test backoff, DEGRADED state, and the `on_disconnect` hook contract.
