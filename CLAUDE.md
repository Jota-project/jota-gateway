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
- **OpenAI-compatible REST** `/v1/*` — `GET /v1/models` and `POST /v1/chat/completions` for Home Assistant integration; delegates to the orchestrator registry

### URL convention in settings

All `Settings` fields for external services are `host:port` **without protocol**. Each service client injects the protocol itself at construction time (`http://`, `ws://`). Never store full URLs in settings.

### WebSocket session lifecycle (`routes.py` → `bridge.py`)

1. Client connects and sends a **Handshake** JSON:
   ```json
   {
     "client_key": "...",
     "input_mode": "audio" | "text",
     "output_mode": ["audio", "text", "status"],
     "agent": "assistant"   // optional — OpenClaw agent name; defaults to OPENCLAW_DEFAULT_AGENT
   }
   ```
2. Gateway resolves identity via `db_client.get_session(client_key)` → `(Client, ClientConfig)`
3. `JotaBridge` is instantiated with client, config, WebSocket, and an injected `OrchestratorProtocol` instance (from `app.state.orchestrators.default()`)
4. `bridge.connect_internal_services()` — starts `TranscriberClient` only if `input_mode == "audio"`; the orchestrator is already connected (it's a singleton managed in app lifespan)
5. `bridge.health_check()` — pings each microservice; orchestrator failure is fatal, TTS failure is degraded
6. `bridge.run()` — launches concurrent tasks: `_client_input_loop` + transcriber `listen_loop` + silence watchdog

### JotaBridge data flow

- **Audio bytes from client** → `TranscriberClient.send_audio()` → transcription partials/finals via `_on_transcription` callback
- **`{"type":"end"}`** → `transcriber.send_end()` (signals end of utterance)
- **`{"type":"send","text":"..."}`** → `_call_orchestrator(text)` — creates a fresh `TTSClient` per turn, runs `pipe_tokens` + `pipe_audio` concurrently via `asyncio.gather`
- **Barge-in**: partial transcriptions with `len >= config.barge_in_min_chars` cancel the active orchestrator turn via `_cancel_active_turn()`, which cancels the Python task and causes `OpenClawClient` to send `chat.abort` to OpenClaw

### Session key derivation

Each orchestrator turn uses a session key derived from the Handshake `agent` field and the client UUID:

```
session_key = f"agent:{agent}:{client.id}"
```

If `agent` is not provided in the Handshake, `settings.OPENCLAW_DEFAULT_AGENT` is used.
The HA REST endpoint (`/v1/chat/completions`) uses a fixed key: `agent:{OPENCLAW_DEFAULT_AGENT}:ha`.

### Microservice clients (all in `src/services/`)

| Client | Protocol | Notes |
|---|---|---|
| `DbClient` | HTTP (httpx) | Singleton; `connect()`/`close()` in app lifespan; caches sessions 60s, models 300s |
| `OpenClawClient` | WebSocket v4 | Persistent connection per app instance; `chat.send` + streaming `chat` events; sends `chat.abort` on barge-in |
| `ReconnectingOrchestrator` | — | Wraps `OpenClawClient`; auto-reconnects with exponential backoff on disconnect; exposes state (CONNECTED/RECONNECTING/DEGRADED) |
| `TranscriberClient` | WebSocket | One instance per session (audio mode only); receives PCM Float32 16kHz |
| `TTSClient` | WebSocket | **Created fresh per orchestrator turn**, not per session; receives tokens, yields PCM16 24kHz |

### Orchestrator registry (`src/services/orchestrators/`)

Built once in `main.py` lifespan, stored in `app.state.orchestrators`.

- `OrchestratorRegistry` — holds named `ReconnectingOrchestrator` instances; `registry.default()` returns the one named by `DEFAULT_ORCHESTRATOR`
- `ReconnectingOrchestrator` — wraps any `OrchestratorProtocol` impl; on disconnect it tries reconnect with backoff up to `ORCHESTRATOR_RECONNECT_MAX_DURATION` seconds; after that enters DEGRADED state
- `OpenClawClient` — the concrete implementation: WebSocket v4 handshake (challenge → connect → hello-ok), persistent `_listen` task, `_keepalive_loop` (pings at 80% of `tickIntervalMs` to prevent idle timeout), `stream_response()` per turn

OpenClaw connection details (loopback backend mode, no device signature required):
- Token: `OPENCLAW_TOKEN`
- Port: `OPENCLAW_PORT` (default 18789)
- Default agent: `OPENCLAW_DEFAULT_AGENT` (default `"main"`)

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

`OpenClawClient` tests (`test_openclaw_client.py`) use `SmartFakeWS` — a queue-backed fake WebSocket that auto-responds to handshake and per-test `chat.send` sequences.
