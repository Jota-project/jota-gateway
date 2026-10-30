# CLAUDE.md

Guidance for Claude Code in this repository. Reference docs live elsewhere; link to them, don't copy them here:

- [`README.md`](README.md) — endpoints (`/ws/stream`, `/admin/*`, `/v1/*`, `/healthz`, `/ready`), `/v1` auth, configuration summary.
- [`docs/client-protocol.md`](docs/client-protocol.md) — the WebSocket protocol clients implement (handshake, `ready`, messages, close codes, error codes, timeouts).
- [`docs/openclaw-protocol.md`](docs/openclaw-protocol.md) — OpenClaw wire protocol as observed (minimum server 2026.6.11).
- [`.env.sample`](.env.sample) — every `Settings` field with its default (`tests/unit/test_env_sample.py` keeps them in sync with `src/core/config.py`).

Roadmap, specs, plans, decisions and audits live **only in Obsidian** (`Producto Jota/jota-gateway/`, note `JGW Roadmap`); issues, milestones and releases live in GitHub.

## Commands

```bash
# Dev server
uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload

# Use the repo venv (.venv, `pip install -e .[dev]`), never the system python
PYTHONPATH=. pytest                                   # pytest.ini adds -m "not e2e_real and not live"
PYTHONPATH=. pytest tests/integration/test_admin_clients.py
PYTHONPATH=. pytest -k "test_create_client_minimal"
ruff check src/ tests/
mypy                                                  # [tool.mypy] files = ["src"]

docker compose up

# Clients from the terminal (src/cli.py; runs migrations first)
python3 src/cli.py add-client --name "Jota-Screen" [--key <existing-key>] [--type ha|esp32|web|app] [--agent main]
python3 src/cli.py list-clients
python3 src/cli.py deactivate-client|activate-client|delete-client <client_key>
```

CI (`.github/workflows/test.yml`) runs ruff, mypy and pytest over `tests/unit`, `tests/contract` and `tests/integration`: run all three locally before opening a PR. `tests/e2e` (marker `e2e_real`, real OpenClaw) and `tests/live` (marker `live`, real ElevenLabs, `JGW_LIVE=1`) only run on demand; see the README.

## What this is

A BFF: the single entry point for Jota-Screen, jota-voice (Mac and Android/Termux), the iPhone app and Home Assistant (an ESP32 client is planned, not built). Client identity and per-client config live in a local SQLite DB (`data/gateway.db`, Alembic migrations applied at startup). One `JotaBridge` per WebSocket session talks to OpenClaw (orchestrator, the only fatal dependency), an STT provider and a TTS provider. One process per DB: `SINGLE_PROCESS_LOCK` takes a `flock` on `gateway.lock` next to the DB; multi-worker is not supported.

## Module map (`src/`)

| Path | Role |
|---|---|
| `main.py` | App + lifespan: lock and startup checks, migrations, builds OpenClaw and speech providers into `app.state`, wires state-change broadcasts, ordered shutdown (drain sessions → close speech providers → OpenClaw → dispose DB engine) |
| `api/routes.py` | `/ws/stream`: handshake (size, timeout, auth, agent policy) → `QueuedSender` → `PipelineTracker` → `JotaBridge` → health check → `ready` → `bridge.run()` |
| `api/admin_routes.py` | `/admin/*` (`X-Admin-Token` via `api/deps.py`): client CRUD, sessions, service status, `GET /admin/speech`, `/admin/state` (+ SSE stream) |
| `api/openai_routes.py` | `/v1/models`, `/v1/chat/completions` (Home Assistant); auth in `resolve_ha_caller` + `core/network.py` |
| `api/health_routes.py` | `/healthz` (liveness), `/ready` (503 only if OpenClaw is down) |
| `core/config.py` | `Settings` (pydantic-settings, `.env`) |
| `core/agent_policy.py` | `resolve_agent()`: handshake/`model` → client `default_agent` → OpenClaw default → `"main"`, then `allowed_agents` and roster checks |
| `core/network.py` | `resolve_client_ip()`; `X-Real-IP` trusted only from `TRUSTED_PROXIES` |
| `core/logging.py` | `fingerprint_key()`, text/JSON formatters |
| `core/transport.py` | Captures the uvicorn transport so a slow client can be aborted (`drop_slow_client`) |
| `core/startup_checks.py` | Single-process lock, token/speech settings checks |
| `db/` | `ClientRecord` (`models.py`), engine and `run_migrations()` (`database.py`); Alembic scripts in the top-level `migrations/` |
| `models/` | Handshake/`ClientConfig` (`schemas.py`), admin input validation (`admin_schemas.py`) |
| `services/bridge.py` | `JotaBridge`: input loop, turns, barge-in, push turns, silence/idle watchdogs, session revalidation, `close_all()` |
| `services/outbound.py` | `QueuedSender`: one ordered outbound queue per session; slow-client cut-off (`CLIENT_SEND_TIMEOUT_S`) |
| `services/db_client.py` | `get_session(client_key)` → `(Client, ClientConfig)` straight from the DB (no cache) |
| `services/openclaw/` | `OpenClawClient` (one multiplexed WS v4 connection), `ReconnectingOpenClawClient`, `FrameDispatcher`, `TurnRegistry`/`ClientRegistry`, `ChatTextTracker` |
| `services/speech/` | STT/TTS ports (`ports.py`), `factory.py`, `jota.py` (jota-transcriber/jota-speaker), `elevenlabs/`; `SpeechProviders` holder in `app.state.speech` |
| `services/reconnection.py` | Shared `ConnectionState`, `ServiceHealth`, backoff loop, `describe_error()` |
| `services/pipeline_tracker.py`, `session_registry.py`, `stats.py` | Per-session events/latencies, `/admin/sessions`, in-process counters |
| `cli.py` | Client management from the terminal |

Speech providers are chosen at startup with `STT_PROVIDER`/`TTS_PROVIDER` (`jota` or `elevenlabs`); changing them needs a restart (`GET /admin/speech` only reads). Every remaining ElevenLabs tunable is a module constant in `services/speech/elevenlabs/`.

## Hard invariants

Breaking any of these is a bug, whatever the change is for.

- **Service addresses are `host:port` without protocol** in every `Settings` field; each client adds `ws://`/`http://` itself. Never store full URLs in settings.
- **`client_key` is a credential.** Never log it, in full or as a prefix; correlate with `fingerprint_key(value)` logged as `fp=<8 hex>`, and with `client.id` after auth. Pydantic validation errors must be logged as field + error type only (`input_value` would carry the key).
- **No user text in logs at INFO or above, nor in tracker `meta`** (it is logged at INFO and persisted in `SessionRecord`, visible in `/admin/sessions`): record lengths (`text_len=len(text)`). DEBUG transcripts are truncated (finals: 40 chars).
- **Stable errors toward clients.** A client or a log never sees exception text or OpenClaw's raw error: turn errors use the fixed codes listed in `docs/client-protocol.md` §9 (`orchestrator_*`, `turn_timeout`, `turn_overflow`, `turn_in_progress`, `internal_error`); logs carry the exception type (`describe_error`, `traceback_no_message`) and `last_error` holds a code or a type name.
- **Exactly one `turn_end` per `turn_start`**, normal or push, on every path (end, error, cancel, barge-in, replacing `send`, final transcription, close). `turn_seq` is masked `& 0xFFFF` in both `turn_start` and the audio header.
- **All client output goes through the session's `QueuedSender`** (total order = enqueue order); `notify_service_status()` is the single point for `status` messages.
- **Only the orchestrator is fatal** (health check before `ready` → 1011). STT/TTS failures degrade the session and are reported with `status`; they never close it.
- **OpenClaw drift:** when you discover a protocol change, inconsistency or undocumented event shape, update `docs/openclaw-protocol.md` (dated, server version if known, "Breaking change — vX.Y.Z" style). That file is the source of truth for the protocol; this one only says how the code reacts.
- `.env.sample` must list every `Settings` field with its default; `/admin/*` input is validated in `admin_schemas.py` (new client type → extend `ClientType`).

## Testing notes

- `tests/unit` (bridge and services with light fakes), `tests/contract` (the same battery against every speech adapter, fakes in `tests/fakes/`), `tests/integration` (`TestClient`, fake upstreams; a few use real uvicorn).
- `tests/integration/conftest.py`: `db_engine` (autouse) puts a file-backed SQLite in `tmp_path` into `src.db.database._engine`; `_skip_lifespan_migrations` and `_noop_dispose_engine` (autouse) keep the lifespan off that DB; `seed_client` (not autouse — admin tests need an empty DB) inserts `VALID_KEY`/`CLIENT_ID`; `client` is a full `TestClient` with a mock orchestrator.
- `tests/conftest.py` disables the single-process lock, resets `gateway_stats` and forces `ELEVENLABS_API_KEY=None` for every test.
- If imports of `src`/`tests` fail oddly, check the Python environment for stray `.pth` files or a foreign `tests` package before assuming the code is broken.

## Sincronización obligatoria con Obsidian (ciclo de vida de issues y PRs)

El estado del trabajo de este repo vive en **dos sitios que deben coincidir**: GitHub (issues, PRs, milestones, releases) y el vault de Obsidian J-Project, en `Producto Jota/jota-gateway/` (prefijo `JGW`; ficha `jota-gateway.md`, `JGW Roadmap.md`, carpetas `Fases/`, `Issues/`, `Decisiones/`, `Notas/`). Obsidian no es opcional ni un volcado de una sola vez: **cada cambio de estado de una issue o PR exige contrastar y actualizar los dos**, y **antes de trabajar** hay que leer también Obsidian, no solo GitHub.

Se usa el MCP `obsidian` (requiere Obsidian abierto). Antes de escribir, lee `CLAUDE.md` y `IA y reglas/Guia-MCP-Obsidian.md` del vault (sección «Dar de alta un repo de código»). Edita con `vault_patch` y no sobrescribas con `vault_write` sin leer antes; nunca borres notas (se mueven a `Archivo/` con `status: archivada`).

| Momento | Qué hacer en Obsidian (además de GitHub) |
|---|---|
| **Antes de empezar** una issue | Lee `JGW-<n> …`, `JGW Roadmap`, la fase y las decisiones relacionadas. Contrasta con `gh issue view <n>`: estado, labels, milestone. Si no coinciden, corrige la nota o avisa al usuario antes de seguir. Si la issue no tiene nota, créala. |
| **Se crea una issue nueva** | Crea `Issues/JGW-<n> Título.md` con las propiedades fijas: `repo`, `number`, `url`, `status`, `labels`, `phase`, `synced`, más `type: issue`, `area: producto`, `priority`, `project: J`, `created`, `tags`. Añádela a la tabla de `JGW Roadmap` (en «Sin fase» si no tiene milestone). |
| **Se empieza a trabajar** en ella | `status: en-curso`, `synced` a hoy; anota la rama. |
| **Se abre la PR** | Anota en la nota de la issue el número y la URL de la PR y su alcance. Si la PR cierra varias issues, actualiza todas. |
| **Cambia el alcance o aparece trabajo derivado** | Actualiza la nota; cada issue de seguimiento nueva sigue la fila «Se crea una issue nueva». Una decisión de diseño va a `Decisiones/JGW Decisión - Tema.md`. |
| **La PR se mergea / la issue se cierra** | `status: hecho` (o muévela a `Archivo/` con `status: archivada`), `synced`; quita o marca la fila en `JGW Roadmap`; si cierra una fase, actualiza `Fases/JGW Fase N …` (`status`, `end`, `release`) y la tabla del roadmap. |
| **Sale una release** | Añade la línea en `Notas/JGW Changelog.md` y actualiza `version` y `synced` en la ficha `jota-gateway.md`. |
| **Al terminar la sesión** | Una línea en `Actividad IA` de `Diario/Diarias/YYYY-MM-DD.md`, con enlaces a las notas tocadas. |

Reglas de contraste:

- Si GitHub y Obsidian discrepan en el **estado**, manda GitHub (verifícalo con `gh`), y se corrige Obsidian. La **información de contexto** (decisiones, avances por subfase, notas) la conserva Obsidian: léela antes de decidir.
- No se da una issue o PR por cerrada del todo mientras Obsidian siga mostrando otro estado. Si Obsidian está cerrado o el MCP falla, **no reintentes en bucle**: dilo al usuario y deja constancia explícita en la PR o en la respuesta de que Obsidian queda sin sincronizar.
- Nada de claves, tokens ni transcripts en Obsidian (aplican las reglas de higiene de logs de «Hard invariants»).

