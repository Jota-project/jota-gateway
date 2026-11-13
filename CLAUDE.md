# CLAUDE.md

Guidance for Claude Code in this repository. Reference docs live elsewhere; link to them, don't copy them here:

- [`README.md`](README.md) — endpoints (`/ws/stream`, `/admin/*`, `/v1/*`, `/healthz`, `/ready`), `/v1` auth, configuration summary.
- [`docs/client-protocol.md`](docs/client-protocol.md) — the WebSocket protocol clients implement (handshake, `ready`, messages, close codes, error codes, timeouts).
- [`docs/openclaw-protocol.md`](docs/openclaw-protocol.md) — OpenClaw wire protocol as observed (minimum server 2026.6.11).
- [`.env.sample`](.env.sample) — every `Settings` field with its default (`tests/unit/test_env_sample.py` keeps them in sync with `src/fogar_gateway/core/config.py`).

Roadmap, specs, plans, decisions and audits live **only in Obsidian** (the gateway's folder and its roadmap note; see «Sincronización obligatoria con Obsidian» below for how to find them); issues, milestones and releases live in GitHub.

## Commands

```bash
# Dev server. Config and DB live in <base> (FOGAR_GATEWAY_HOME or ~/.fogar-gateway), never the cwd;
# the gateway refuses to start without a DB, so run `fogar-gw init` once per base.
fogar-gw init
uvicorn fogar_gateway.main:app --port 8004 --reload    # or `fogar-gw serve` (production settings)

# New migration (Alembic lives inside the package)
alembic -c src/fogar_gateway/alembic.ini revision --autogenerate -m "…"

# Use the repo venv (.venv, `uv sync --extra dev`: exact versions from uv.lock), never the system python
# Changed a dependency in pyproject.toml? Run `uv lock` and commit uv.lock too (CI uses `uv sync --locked`)
PYTHONPATH=. pytest                                   # pytest.ini adds -m "not e2e_real and not live"
PYTHONPATH=. pytest tests/integration/test_admin_clients.py
PYTHONPATH=. pytest -k "test_create_client_minimal"
ruff check src/ tests/
mypy                                                  # [tool.mypy] files = ["src"]

docker compose up

# Admin CLI (talks to /admin, never writes the DB; doctor/config also work with the gateway stopped)
fogar-gw                                               # interactive menu on a TTY, help otherwise
fogar-gw [--home DIR] init | serve [--create-db]      # new install; start the gateway
fogar-gw onboard [--answers FILE]                     # setup wizard: OpenClaw, voice, service, dashboard handoff
fogar-gw token                                         # dashboard token (ADMIN_TOKEN), only to a terminal
fogar-gw doctor | config show|get|validate|set|unset | status | version
fogar-gw install [--exe PATH] | uninstall | start | stop | restart   # systemd user service
fogar-gw migrate --from-docker DIR                    # copy a Docker install (.env + DB) to native; see README
fogar-gw logs [-f] [-n N] [--level L] [--session ID]
fogar-gw clients list | show <ref> | add --name … | edit <ref> | enable|disable|rotate-key|remove <ref>
fogar-gw pair [--type …] [--set …] [--no-wait]        # pairing code for a client to self-register
docker exec fogar_gateway fogar-gw …                    # inside the container
```

CI (`.github/workflows/test.yml`, installs from `uv.lock`) runs ruff, mypy and pytest over `tests/unit`, `tests/contract` and `tests/integration`: run all three locally before opening a PR. `tests/e2e` (marker `e2e_real`, real OpenClaw) and `tests/live` (marker `live`, real ElevenLabs, `FOGAR_GW_LIVE=1`) only run on demand; see the README. The `installer-smoke` job runs `install.sh` (native installer at the repo root, published as a release asset) and `fogar-gw onboard --answers` against fake OpenClaw/TTS under a real systemd user session.

## What this is

Fogar's front door at home: every device (Fogar TV, the voice client for Mac and Android/Termux, the iPhone app, Home Assistant; an ESP32 client is planned, not built) discovers it, pairs with a code and talks to it; it bridges them to OpenClaw and to speech, and it is administered from its own web panel (`/`) and the `fogar-gw` CLI, installed by `install.sh` + `fogar-gw onboard`. Client identity and per-client config live in a local SQLite DB (`<base>/data/gateway.db`, Alembic migrations applied at startup). One `FogarBridge` per WebSocket session talks to OpenClaw (orchestrator, the only fatal dependency), an STT provider and a TTS provider. One process per DB: `SINGLE_PROCESS_LOCK` takes a `flock` on `gateway.lock` next to the DB; multi-worker is not supported.

## Module map (`src/fogar_gateway/`)

| Path | Role |
|---|---|
| `access.py` | Stdlib only at top level (imported by `core/config.py`, so never import `core.config` here): `parse_public_urls()` validates/normalizes `PUBLIC_URLS`; `verify` checks a route against `/.well-known/fogar-gateway` `instance_id`; detects LAN, Tailscale and Docker (shells out to `tailscale` with timeouts, never raises); `collect`/`recommend` build the route list with one recommended |
| `paths.py` | Stdlib only, shared by gateway and CLI: `home()` (`FOGAR_GATEWAY_HOME`/`--home` or `~/.fogar-gateway`), `config_file()`, `openclaw_device_file()`, `absolute_database_url()`/`sqlite_file()` (a relative SQLite path hangs from the base, URL-encoded), `LOCK_FILENAME` |
| `main.py` | App + lifespan: logs the resolved paths, lock and startup checks, migrations, builds OpenClaw and speech providers into `app.state`, wires state-change broadcasts, ordered shutdown under `Teardown` (unhook broadcasts → close speech providers → OpenClaw → dispose DB engine; uvicorn has already closed every session with 1012) |
| `api/routes.py` | `/ws/stream`: handshake (size, timeout, auth, agent policy) → `QueuedSender` → `PipelineTracker` → `FogarBridge` → health check → `ready` → `bridge.run()` |
| `api/admin_routes.py` | `/admin/*` (`X-Admin-Token` via `api/deps.py`): client CRUD, sessions, service status, `GET /admin/speech`, `GET /admin/access` (routes to the gateway), `/admin/state` (+ SSE stream) |
| `api/openai_routes.py` | `/v1/models`, `/v1/chat/completions` (Home Assistant); auth in `resolve_ha_caller` + `core/network.py` |
| `api/pairing_routes.py` | Public `GET /.well-known/fogar-gateway` (identity only, never whether codes are open) and `POST /pair` (redeem a code → `client_key` once; template fields win over client-offered `name`/`client_type`/`output_mode`); `/admin/pairing` to open/list/get/cancel codes (#317) |
| `api/health_routes.py` | `/healthz` (liveness), `/ready` (503 only if OpenClaw is down; an `off` speech provider reports `unavailable` but does not degrade `status`) |
| `api/web_routes.py` | The web panel (`web/` build) at `/`, from `fogar_gateway/web/` inside the package (never the cwd): `assets/*` immutable, `index.html` no-cache, CSP and security headers only on web responses, any non-API GET falls back to `index.html`; API prefixes keep their JSON 404. Mounted last in `main.py`; without a build `/` is 404 and the lifespan warns once |
| `core/config.py` | `Settings` (pydantic-settings, env → `<base>/config.env`); `DATABASE_URL` made absolute against the base, default included |
| `core/agent_policy.py` | `resolve_agent()`: handshake/`model` → client `default_agent` → OpenClaw default → `"main"`, then `allowed_agents` and roster checks |
| `core/network.py` | `resolve_client_ip()`; `X-Real-IP` trusted only from `TRUSTED_PROXIES` |
| `core/logging.py` | `fingerprint_key()`, text/JSON formatters |
| `core/transport.py` | Captures the uvicorn transport so a slow client can be aborted (`drop_slow_client`) |
| `core/startup_checks.py` | Single-process lock, token/speech settings checks |
| `db/` | `ClientRecord` (`models.py`), engine and `run_migrations()` (`database.py`, raises `DatabaseMissing` unless the DB exists or `CREATE_DB` is set by `init`/`serve --create-db`); Alembic in `alembic.ini` + `migrations/` inside the package (`script_location = fogar_gateway:migrations`) |
| `models/` | Handshake/`ClientConfig` (`schemas.py`), admin input validation (`admin_schemas.py`) |
| `services/bridge.py` | `FogarBridge`: input loop, turns, barge-in, push turns, silence/idle watchdogs, session revalidation, `close_all()` |
| `services/outbound.py` | `QueuedSender`: one ordered outbound queue per session; slow-client cut-off (`CLIENT_SEND_TIMEOUT_S`) |
| `services/pacing.py` | `AudioPacer`: envía el audio TTS a 1× con `AUDIO_LEAD_S` de adelanto |
| `services/db_client.py` | `get_session(client_key)` → `(Client, ClientConfig)` straight from the DB (no cache) |
| `services/openclaw/` | `OpenClawClient` (one multiplexed WS v4 connection; connects as `gateway-client`/`backend` asking `operator.read`/`write`/`admin`, signs every challenge with the device identity, prefers the stored device token for the configured `host:port` and falls back once to `OPENCLAW_TOKEN` when it is refused, maps `PAIRING_REQUIRED` to `OrchestratorPairingRequired`, or to `OrchestratorDeviceIdentityRequired` when no device proof was sent; keeps `OrchestratorAccess` in `app.state.openclaw_access` for `/admin/state`, reset to `unknown` on a failed connect, a drop or `close()`; `use_device_token=False` for onboard), `device.py` (Ed25519 identity + device token in `<base>/data/openclaw-device.json`, 0600, atomic writes, read-modify-write under `flock` on `.openclaw-device.json.lock`; blocking, so the client calls it via `asyncio.to_thread`; stdlib at top level, `cryptography` imported lazily), `ReconnectingOpenClawClient`, `FrameDispatcher`, `TurnRegistry`/`ClientRegistry`, `ChatTextTracker` |
| `services/speech/` | STT/TTS ports (`ports.py`), `factory.py`, `fogar.py` (fogar-transcriber/fogar-speaker), `elevenlabs/`, `off.py` (voice disabled, no upstream); `SpeechProviders` holder in `app.state.speech` |
| `services/pairing.py` | `PairingStore`: in-memory pairing codes (6 digits, 10 min, single use, ≤ 5 open); `redeem` creates the client under the lock, `MAX_FAILED` wrong codes burn every open code, a failed create keeps the code open |
| `services/reconnection.py` | Shared `ConnectionState`, `ServiceHealth`, backoff loop, `describe_error()` |
| `services/pipeline_tracker.py`, `session_registry.py`, `stats.py` | Per-session events/latencies, `/admin/sessions`, in-process counters |
| `cli/` | `fogar-gw` admin CLI over `/admin` (click; `rich`, `questionary`, `pydantic` and every command imported lazily, `--help` < 150 ms guarded by `tests/unit/test_cli_startup.py`). Reads config from env → `<FOGAR_GATEWAY_HOME or ~/.fogar-gateway>/config.env` → defaults, never the cwd `.env`; `doctor` opens the DB read-only and never takes the lock. `serve` owns the uvicorn settings (port 8004, no proxy headers, 1 MiB WS cap, 35 s graceful shutdown) and passes the app as an import string so uvicorn configures its loggers first (`core/logging.py` relies on that order). `service.py` owns the systemd user unit (`install` needs the DB and a free 8004, never creates the DB; rejects a clone/editable exe without `--exe`; `uninstall` leaves data alone). `logs.py` reads the journal and filters `--level`/`--session` itself for both log formats. `config set` takes secrets only by prompt or stdin (`-`). `migrate.py` (`migrate --from-docker DIR`) copies `DIR/.env` + `DIR/data/gateway.db` (and `DIR/data/openclaw-device.json` if present, 0600) into `<base>`: read-only source, same Alembic revision required, refuses while the container holds the lock or `<base>` is already populated, never leaves partial files. `verify.py` checks each provider against the real service, reusing the gateway's clients: never raises, `rejected` only for real auth refusals (`pairing_required` is ok: the token was accepted), OpenClaw only on loopback and always with the pasted shared token (never a stored device token); its OpenClaw verdict says the granted access. `openclaw_access.py` is the single wording of that access for `status`, `doctor` and `verify`. `onboard.py` (`onboard [--answers FILE]`) writes only values verified in that run, through `config._apply`, and ends with the dashboard URL (never loopback as the only way in), the token (terminal only) and, with a terminal, waits for `gateway.dashboards > 0` in `/admin/state` (open `/admin/state/stream` count). `access.py` is `fogar-gw access` (list routes via `access.collect`, also served by `GET /admin/access`, 15 s cache) and `access setup` (Tailscale serve helpers: never overwrite an existing handler, never touch Tailscale unasked; PUBLIC_URLS); `token.py` prints `ADMIN_TOKEN` only to a TTY |

Speech providers are chosen at startup with `STT_PROVIDER`/`TTS_PROVIDER` (`fogar`, `elevenlabs` or `off`: no voice, the session opens degraded and text still works, `services/speech/off.py`); changing them needs a restart (`GET /admin/speech` only reads). Every remaining ElevenLabs tunable is a module constant in `services/speech/elevenlabs/`.

## Hard invariants

Breaking any of these is a bug, whatever the change is for.

- **Service addresses are `host:port` without protocol** in every `Settings` field; each client adds `ws://`/`http://` itself. Never store full URLs in settings. Sole exception: `PUBLIC_URLS` (the user's own `https://` URLs, validated by `access.parse_public_urls`).
- **The OpenClaw device identity is a credential** (M19): the private key and the device token in `<base>/data/openclaw-device.json` never reach logs, `/admin` or Obsidian, not even as a prefix (the token correlates as `fp=`); the deviceId and the pairing request id are public. The file is never overwritten when it is unreadable (it may hold a paired identity).
- **`client_key` is a credential.** `/admin` returns it only on create and rotate (`ClientWithKey`); reads carry `key_fp`. Never log it, in full or as a prefix; correlate with `fingerprint_key(value)` logged as `fp=<8 hex>`, and with `client.id` after auth. Pydantic validation errors must be logged as field + error type only (`input_value` would carry the key).
- **No user text in logs at INFO or above, nor in tracker `meta`** (it is logged at INFO and persisted in `SessionRecord`, visible in `/admin/sessions`): record lengths (`text_len=len(text)`). DEBUG transcripts are truncated (finals: 40 chars).
- **Stable errors toward clients.** A client or a log never sees exception text or OpenClaw's raw error: turn errors use the fixed codes listed in `docs/client-protocol.md` §9 (`orchestrator_*`, `turn_timeout`, `turn_overflow`, `turn_in_progress`, `internal_error`); logs carry the exception type (`describe_error`, `traceback_no_message`) and `last_error` holds a code or a type name.
- **Exactly one `turn_end` per `turn_start`**, normal or push, on every path (end, error, cancel, barge-in, replacing `send`, final transcription, close). `turn_seq` is masked `& 0xFFFF` in both `turn_start` and the audio header.
- **All client output goes through the session's `QueuedSender`** (total order = enqueue order); `notify_service_status()` is the single point for `status` messages.
- **Only the orchestrator is fatal** (health check before `ready` → 1011). STT/TTS failures degrade the session and are reported with `status`; they never close it.
- **No error is lost, no foreign cancellation is swallowed** (#221). Catch only at boundaries (service wrappers, session, lifespan); everything else propagates. A broad `except` logs the type (`describe_error`) and carries `# noqa: BLE001 — <why it is a boundary>`; `except: pass` and `contextlib.suppress` are lint errors. Teardown goes through `services/teardown.py` (`Teardown`): it finishes every step and re-raises a foreign `CancelledError` at the end.
- **OpenClaw drift:** when you discover a protocol change, inconsistency or undocumented event shape, update `docs/openclaw-protocol.md` (dated, server version if known, "Breaking change — vX.Y.Z" style). That file is the source of truth for the protocol; this one only says how the code reacts.
- **Nothing resolves against the working directory, and the gateway never creates a DB silently** (M14c). Paths come from `paths.py`; only `fogar-gw init`, `fogar-gw migrate` (copies it from Docker) and `fogar-gw serve --create-db` (the Docker image) may create `gateway.db`.
- **Pairing codes and the keys they mint never reach logs** (#317), not even masked; `/pair` is public (it may be reachable from the Internet through the proxy), so its only defence is `MAX_FAILED` burning every open code, and there is no loopback auto-approval (a same-host proxy makes the Internet look like 127.0.0.1). New clients go through `admin_routes.create_client_record`, the single creation path.
- `.env.sample` must list every `Settings` field with its default; `/admin/*` input is validated in `admin_schemas.py` (new client type → extend `ClientType`).

## Testing notes

- `tests/unit` (bridge and services with light fakes), `tests/contract` (the same battery against every networked speech adapter, fakes in `tests/fakes/`; `off` has no upstream and is covered by `tests/unit/test_speech_off.py`), `tests/integration` (`TestClient`, fake upstreams; a few use real uvicorn).
- `tests/integration/conftest.py`: `db_engine` (autouse) puts a file-backed SQLite in `tmp_path` into `fogar_gateway.db.database._engine`; `_skip_lifespan_migrations` and `_noop_dispose_engine` (autouse) keep the lifespan off that DB; `seed_client` (not autouse — admin tests need an empty DB) inserts `VALID_KEY`/`CLIENT_ID`; `client` is a full `TestClient` with a mock orchestrator.
- `tests/conftest.py` points `FOGAR_GATEWAY_HOME` at a temp dir **before** the first `fogar_gateway` import (so the suite never reads or writes the real `~/.fogar-gateway`; subprocesses inherit it), disables the single-process lock, resets `gateway_stats` and forces `ELEVENLABS_API_KEY=None` for every test.
- If imports of `src`/`tests` fail oddly, check the Python environment for stray `.pth` files or a foreign `tests` package before assuming the code is broken.

## Sincronización obligatoria con Obsidian (ciclo de vida de issues y PRs)

El estado del trabajo de este repo vive en **dos sitios que deben coincidir**: GitHub (issues, PRs, milestones, releases) y el vault de Obsidian del equipo, en la carpeta del gateway (con su ficha, su roadmap y las carpetas `Fases/`, `Issues/`, `Decisiones/`, `Notas/`). La carpeta, el prefijo de sus notas y la ficha conservan el nombre anterior hasta la fase 3 de la migración a Fogar: la nota del gateway en `IA y reglas/Migración Fogar/Repos/` los enlaza. Abajo, `<P>` es ese prefijo y «la ficha», la ficha del repo. Obsidian no es opcional ni un volcado de una sola vez: **cada cambio de estado de una issue o PR exige contrastar y actualizar los dos**, y **antes de trabajar** hay que leer también Obsidian, no solo GitHub.

Se usa el MCP `obsidian` (requiere Obsidian abierto). Antes de escribir, lee `CLAUDE.md` y `IA y reglas/Guia-MCP-Obsidian.md` del vault (sección «Dar de alta un repo de código»). Edita con `vault_patch` y no sobrescribas con `vault_write` sin leer antes; nunca borres notas (se mueven a `Archivo/` con `status: archivada`).

| Momento | Qué hacer en Obsidian (además de GitHub) |
|---|---|
| **Antes de empezar** una issue | Lee `<P>-<n> …`, `<P> Roadmap`, la fase y las decisiones relacionadas. Contrasta con `gh issue view <n>`: estado, labels, milestone. Si no coinciden, corrige la nota o avisa al usuario antes de seguir. Si la issue no tiene nota, créala. |
| **Se crea una issue nueva** | Crea `Issues/<P>-<n> Título.md` con las propiedades fijas: `repo`, `number`, `url`, `status`, `labels`, `phase`, `synced`, más `type: issue`, `area: producto`, `priority`, `project` (el mismo que las demás notas del repo), `created`, `tags`. Añádela a la tabla de `<P> Roadmap` (en «Sin fase» si no tiene milestone). |
| **Se empieza a trabajar** en ella | `status: en-curso`, `synced` a hoy; anota la rama. |
| **Se abre la PR** | Anota en la nota de la issue el número y la URL de la PR y su alcance. Si la PR cierra varias issues, actualiza todas. |
| **Cambia el alcance o aparece trabajo derivado** | Actualiza la nota; cada issue de seguimiento nueva sigue la fila «Se crea una issue nueva». Una decisión de diseño va a `Decisiones/<P> Decisión - Tema.md`. |
| **La PR se mergea / la issue se cierra** | `status: hecho` (o muévela a `Archivo/` con `status: archivada`), `synced`; quita o marca la fila en `<P> Roadmap`; si cierra una fase, actualiza `Fases/<P> Fase N …` (`status`, `end`, `release`) y la tabla del roadmap. |
| **Sale una release** | Añade la línea en `Notas/<P> Changelog.md` y actualiza `version` y `synced` en la ficha. |
| **Al terminar la sesión** | Una línea en `Actividad IA` de `Diario/Diarias/YYYY-MM-DD.md`, con enlaces a las notas tocadas. |

Reglas de contraste:

- Si GitHub y Obsidian discrepan en el **estado**, manda GitHub (verifícalo con `gh`), y se corrige Obsidian. La **información de contexto** (decisiones, avances por subfase, notas) la conserva Obsidian: léela antes de decidir.
- No se da una issue o PR por cerrada del todo mientras Obsidian siga mostrando otro estado. Si Obsidian está cerrado o el MCP falla, **no reintentes en bucle**: dilo al usuario y deja constancia explícita en la PR o en la respuesta de que Obsidian queda sin sincronizar.
- Nada de claves, tokens ni transcripts en Obsidian (aplican las reglas de higiene de logs de «Hard invariants»).

