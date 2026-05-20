# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the server in development
uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload

# Run all tests
PYTHONPATH=. pytest

# Run a single test file
PYTHONPATH=. pytest tests/integration/test_rest_auth.py

# Run a single test by name
PYTHONPATH=. pytest -k "test_valid_api_key_passes"

# Lint
ruff check src/ tests/

# Docker
docker compose up
```

## Architecture

jota-gateway is a **BFF (Backend For Frontend)** — the single entry point for all clients (ESP32, web, app). It has two surfaces:

- **WebSocket** `/ws/stream` — full voice+text session managed by `JotaBridge`
- **REST API** `/api/*` — thin proxy to jota-db for config, conversations, models, health

### URL convention in settings

All `Settings` fields for external services are `host:port` **without protocol**. Each service client injects the protocol itself at construction time (`http://`, `ws://`). Never store full URLs in settings.

### WebSocket session lifecycle (`routes.py` → `bridge.py`)

1. Client connects and sends a **Handshake** JSON (declares `input_mode` + `output_mode`)
2. Gateway resolves identity via `db_client.get_session(client_key)` → `(Client, ClientConfig)`
3. `JotaBridge` is instantiated with the client, config, and WebSocket
4. `bridge.connect_internal_services()` — always starts `OrchestratorClient`; starts `TranscriberClient` only if `input_mode == "audio"`
5. `bridge.health_check()` — pings each microservice; orchestrator failure is fatal, TTS failure is degraded
6. `bridge.run()` — launches concurrent tasks: `_client_input_loop` + transcriber `listen_loop` + silence watchdog

### JotaBridge data flow

- **Audio bytes from client** → `TranscriberClient.send_audio()` → transcription partials/finals via `_on_transcription` callback
- **`{"type":"end"}`** → `transcriber.send_end()` (signals end of utterance)
- **`{"type":"send","text":"..."}`** → `_call_orchestrator()` — creates a fresh `TTSClient` per turn, runs `pipe_tokens` + `pipe_audio` concurrently via `asyncio.gather`
- **Barge-in**: partial transcriptions with `len >= config.barge_in_min_chars` cancel the active orchestrator turn via `_cancel_active_turn()`

### Microservice clients (all in `src/services/`)

| Client | Protocol | Notes |
|---|---|---|
| `DbClient` | HTTP (httpx) | Singleton; `connect()`/`close()` in app lifespan; caches sessions 60s, models 300s |
| `OrchestratorClient` | HTTP NDJSON streaming | One instance per session; `POST /api/quick` returns NDJSON token stream |
| `TranscriberClient` | WebSocket | One instance per session (audio mode only); receives PCM Float32 16kHz |
| `TTSClient` | WebSocket | **Created fresh per orchestrator turn**, not per session; receives tokens, yields PCM16 24kHz |

### REST API authentication

All `/api/*` routes use `Depends(get_verified_client)` (`src/api/deps.py`), which reads the `X-API-Key` header and calls `db_client.get_session()` — same path as the WebSocket handshake. Returns `(Client, ClientConfig)`.

### Cache pattern (`src/core/cache.py`)

`make_cache(maxsize, ttl)` returns `(TTLCache, asyncio.Lock)`. The lock must wrap **only dict access**, never IO calls — acquire lock to check/set, release before any await.

## Testing

Tests live in `tests/integration/`. HTTP calls are intercepted with `respx`; there are no unit tests for individual service clients yet (see issue #8).

The `mock_services` fixture (in `tests/integration/conftest.py`) mocks all downstream HTTP: jota-db auth, config, conversations, models, orchestrator health + streaming, transcriber health, TTS health. Individual tests can override specific routes.

WebSocket tests use `starlette.testclient.TestClient` with fake WebSocket servers running in background threads.

The `clear_db_cache` fixture runs `autouse=True` for all integration tests to prevent session cache leakage between tests.
