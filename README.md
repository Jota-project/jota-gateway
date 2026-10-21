# jota-gateway

BFF (Backend For Frontend) del ecosistema Jota IA. Punto de entrada único para todos los clientes: ESP32, web, app, Home Assistant.

```
ESP32 / Web / App / Home Assistant
        │
        ▼
  jota-gateway (FastAPI :8004)
  ├── /ws/stream          WebSocket — sesión interactiva de voz/texto
  ├── /healthz            Liveness probe
  ├── /ready              Readiness probe
  ├── /admin/*            Gestión y observabilidad (X-Admin-Token)
  └── /v1/*               OpenAI-compatible (Home Assistant, Open WebUI)
        │
        ▼
  ┌──────────────────────────────────────────┐
  │             Microservicios               │
  │  OpenClaw         WebSocket v4           │
  │  jota-transcriber WebSocket              │
  │  jota-tts         WebSocket              │
  └──────────────────────────────────────────┘
```

No hay dependencia de `jota-db` — la identidad y configuración de clientes viven en una base de datos SQLite local (`data/gateway.db`).

---

## Endpoints

### WebSocket

| Endpoint | Auth | Descripción |
|----------|------|-------------|
| `ws://<host>:8004/ws/stream` | `client_key` en handshake JSON | Sesión interactiva de voz/texto |

Ver [`docs/client-protocol.md`](docs/client-protocol.md) para la guía completa de integración.

### Health

| Endpoint | Método | Auth | Descripción |
|----------|--------|------|-------------|
| `/healthz` | GET | ninguna | Liveness — siempre 200 si el proceso vive |
| `/ready` | GET | ninguna | Readiness — 200 ok/degraded, 503 si OpenClaw no responde |

```json
// GET /ready — ejemplo degradado
{
  "status": "degraded",
  "services": {
    "orchestrator": "ok",
    "transcriber": "unavailable",
    "tts": "ok"
  }
}
```

### Admin (requiere `X-Admin-Token`)

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/admin/sessions` | GET | Sesiones activas y recientes en memoria |
| `/admin/sessions/{id}` | GET | Detalle con eventos y latencias por turno |
| `/admin/orchestrators/{name}/status` | GET | Estado del orquestador (CONNECTED / RECONNECTING / DEGRADED) |
| `/admin/orchestrators/{name}/reconnect` | POST | Fuerza reconexión — responde 202 |
| `/admin/transcriber/status` | GET | Estado del proveedor STT activo (`provider`; con jota, reachability en vivo; con ElevenLabs, salud cacheada + `counters`) |
| `/admin/tts/status` | GET | Estado del proveedor TTS activo (`provider`, breaker, último error; con ElevenLabs, `counters`) |
| `/admin/speech` | GET | Proveedores de voz activos (`stt`, `tts`) y puerta de audio efectiva |
| `/admin/speech` | PUT | Cambia el proveedor STT y/o TTS en caliente (`{"stt_provider":"jota"\|"elevenlabs","tts_provider":...}`); no se persiste: al reiniciar mandan las variables de entorno. 422 si falta configuración, 503 si el nuevo proveedor no arranca (no cambia nada) |
| `/admin/clients` | GET | Lista clientes |
| `/admin/clients` | POST | Crear cliente (`client_key` generado o provisto) |
| `/admin/clients/{id}` | GET | Detalle de un cliente |
| `/admin/clients/{id}` | PATCH | Actualizar cliente (campos parciales) |
| `/admin/clients/{id}` | DELETE | Borrar cliente |
| `/admin/clients/{id}/rotate-key` | POST | Rotar `client_key` |

Los tres endpoints `*/status` devuelven la misma forma: `{"name", "state", "connected_at", "reconnect_attempts", "last_error"}`.

Auth: header `X-Admin-Token`. Sin header → 422. Token incorrecto → 401. `ADMIN_TOKEN` vacío → 503.

### OpenAI-compatible (`/v1/*`, para Home Assistant)

**Autenticación** (`src/core/network.py`, `resolve_ha_caller` en `src/api/openai_routes.py`):

- Origen **de confianza** (peer directo en las CIDR de `TRUSTED_NETWORKS`, peer loopback si `TRUST_LOOPBACK=true`, o un cliente avalado por un proxy de `TRUSTED_PROXIES` con `X-Real-IP` válido): no necesita credenciales. **Matiz (#216):** con `TRUSTED_PROXIES` por defecto (`127.0.0.1,::1`) un cliente directo en loopback *sin* `X-Real-IP` ya NO queda exento y debe enviar `Authorization: Bearer <client_key>`; sin proxy, vacía `TRUSTED_PROXIES=` para recuperar el comportamiento anterior (y con proxy, este debe sobrescribir `X-Real-IP`).

  | Escenario (config por defecto) | Antes | Ahora |
  |---|---|---|
  | Loopback directo, sin cabeceras (p. ej. HA en el mismo host) | 200 | **401** (Bearer) |
  | Loopback + solo `X-Forwarded-For` (proxy sin `X-Real-IP`) | 200 | **401** |
  | Loopback + `X-Real-IP` en `TRUSTED_NETWORKS` / externo | 200 / 401 | 200 / 401 |
  | Peer LAN directo en `TRUSTED_NETWORKS` | 200 | 200 |
  | `TRUSTED_PROXIES=` vacío, loopback directo | 200 | 200 |
- Cualquier otro origen debe enviar `Authorization: Bearer <client_key>`, validado contra la tabla `clients` (la misma que usa el handshake WebSocket). Sin él, o con una key inválida/inactiva → `401`.
- Si se envía un Bearer, siempre se valida, también desde un origen de confianza.
- Detrás de un proxy (nginx), la IP real solo se toma de `X-Real-IP` si el peer está en `TRUSTED_PROXIES`. **Falla cerrado (#216):** un peer de `TRUSTED_PROXIES` sin `X-Real-IP` válido no es origen de confianza (se exige Bearer); el proxy debe *sobrescribir* `X-Real-IP` (`proxy_set_header X-Real-IP $remote_addr;`), nunca reenviar el del cliente. `X-Forwarded-For` nunca concede confianza (la imagen arranca uvicorn con `--no-proxy-headers`). Consecuencia: un cliente directo en loopback (p. ej. HA en el mismo host, sin proxy) ya no queda exento con la config por defecto; envía Bearer, o vacía `TRUSTED_PROXIES` si no hay proxy.
- Host de escucha de la imagen Docker: `GATEWAY_HOST` (default `0.0.0.0`); detrás de un proxy en el mismo host usa `GATEWAY_HOST=127.0.0.1`.
- `TRUSTED_NETWORKS` vacío = fail-closed: solo loopback puede quedar exento (sujeto al matiz de `TRUSTED_PROXIES` de arriba).
- `HANDSHAKE_MAX_BYTES` mide **caracteres**, no bytes (con caracteres multibyte el tope real puede ser hasta 4x mayor). `--ws-max-size 1048576` solo está en el `CMD` de la imagen: con `uvicorn` a mano rige el tope por defecto de uvicorn (16 MiB).
- Con Bearer válido, `model` selecciona el agente y se aplican `default_agent`/`allowed_agents` del cliente (violación → `403`).

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/v1/models` | GET | Devuelve el alias `jota-gateway` y los agentes del roster de OpenClaw (filtrados por `allowed_agents`) para un llamante con Bearer; un origen confiable sin Bearer (el chat ignora `model` y usa el agente por defecto) y un gateway sin roster, solo el alias |
| `/v1/chat/completions` | POST | Chat completion; soporta `stream: true/false` |

---

## Protocolo WebSocket — resumen rápido

El cliente envía un handshake JSON como primer mensaje. Si es válido, el gateway responde con `ready` y la sesión comienza.

**Cada turno de respuesta:**
```
← {"type":"turn_start","turn_id":"t-1","turn_seq":1}
← {"type":"token","turn_id":"t-1","text":"Hola,"}
← [0xA1][0x00][0x01][PCM16 24kHz...]   ← audio binario con header
← {"type":"turn_end","turn_id":"t-1"}
```

Ver [`docs/client-protocol.md`](docs/client-protocol.md) para la referencia completa incluyendo barge-in, transcripción, push turns y manejo de errores.

---

## Quick start

```bash
pip install .[dev]
uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload
# o con Docker:
docker compose up
```

> **Docker:** el contenedor corre como no-root. Antes del primer `docker compose up`
> define `GATEWAY_UID`/`GATEWAY_GID` en `.env` con el dueño de `./data`
> (`echo "GATEWAY_UID=$(id -u)" >> .env && echo "GATEWAY_GID=$(id -g)" >> .env`).
> Si `./data` viene de un despliegue anterior como root: `sudo chown -R "$(id -u):$(id -g)" data`.
> Detalles en `docker-compose.yml`.

---

## Configuración

Variables en `.env` (formato `host:port` sin protocolo para todos los URLs):

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///data/gateway.db` | Ruta a la base de datos SQLite local (identidad y configuración de clientes) |
| `TRANSCRIBER_WS_URL` | `localhost:9000` | jota-transcriber |
| `TTS_WS_URL` | `localhost:8005` | jota-tts |
| `TTS_TOKEN` | `gateway` | Token de auth para jota-tts |
| `OPENCLAW_HOST` | `127.0.0.1` | Host de OpenClaw |
| `OPENCLAW_PORT` | `18789` | Puerto de OpenClaw |
| `OPENCLAW_TOKEN` | — | Token de auth para OpenClaw (obligatorio) |
| `ADMIN_TOKEN` | — | Token para endpoints `/admin/*`; vacío deshabilita el admin API |
| `ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión del orquestador (s) |
| `ORCHESTRATOR_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión del orquestador (s); también el periodo del reintento lento en DEGRADED |
| `ORCHESTRATOR_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta DEGRADED del orquestador (s); en DEGRADED sigue reintentando un intento cada `MAX_BACKOFF` s |
| `SINGLE_PROCESS_LOCK` | `true` | `flock` sobre `gateway.lock` junto a la BD: un segundo proceso (p. ej. `--workers 2`) no arranca. Multi-worker no está soportado |
| `SESSION_REVALIDATE_INTERVAL_S` | `10.0` | Cada sesión WS se revalida contra la BD; cliente borrado/desactivado/clave rotada → cierre 1008 `revoked` |
| `SESSION_CACHE_TTL_S` | `10.0` | TTL de la caché de sesiones de `db_client` (antes 60 s) |
| `TRANSCRIBER_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión del transcriptor (s) |
| `TRANSCRIBER_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión del transcriptor (s) |
| `TRANSCRIBER_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta DEGRADED del transcriptor (s) |
| `TTS_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial del breaker de TTS (s) |
| `TTS_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo del breaker de TTS (s) — sin estado DEGRADED, cada turno elegible reintenta |

### Proveedores de voz (STT/TTS)

El STT y el TTS pasan por una capa de proveedores (`src/services/speech/`). Los clientes no notan la diferencia: mismos formatos de audio y mismos mensajes.

| Variable | Default | Descripción |
|----------|---------|-------------|
| `STT_PROVIDER` | `jota` | `jota` (jota-transcriber) o `elevenlabs` (Scribe v2 Realtime) |
| `TTS_PROVIDER` | `jota` | `jota` (jota-tts) o `elevenlabs` (stream-input, `eleven_flash_v2_5`) |
| `ELEVENLABS_API_KEY` | — | Obligatoria si algún proveedor es `elevenlabs` (permisos Text to Speech y Speech to Text) |
| `ELEVENLABS_TTS_DEFAULT_VOICE` | `gc61tTk93h3LBXPxew2V` | Voz por defecto; las voces de clientes que no son de ElevenLabs (p. ej. `af_heart`) caen en ella o se traducen con `TTS_VOICE_MAP` |
| `STT_AUDIO_GATE` | `auto` | `auto` = puerta de energía con proveedores de pago (solo se envía audio mientras se habla), `off` con jota; también `off`/`energy` |
| `STT_GATE_RMS_DBFS` | `-45` | Umbral de la puerta de energía |

El resto de ajustes (`ELEVENLABS_*`) está documentado en `.env.sample`. El cambio en caliente se hace con `PUT /admin/speech`. Con el plan Creator, ElevenLabs conserva el audio y las transcripciones (la retención cero es solo enterprise).

`silence_timeout_s` y `max_silence_turns` (umbral del watchdog de silencio) son campos de configuración **por cliente** en la base de datos, no variables de entorno globales — ver `ClientRecord` en `CLAUDE.md`.

---

## Desarrollo y tests

```bash
# Tests
PYTHONPATH=. pytest

# Test específico
PYTHONPATH=. pytest tests/integration/test_rest_health.py

# Lint
ruff check src/ tests/
```

---

## Smoke-test E2E real (contra OpenClaw de producción)

`tests/e2e/` valida el pipeline completo contra el OpenClaw **real** de este mismo
servidor: turno de texto, cancelación, sesiones concurrentes y uso de tools. Nunca
corre automáticamente — ni en la CI normal, ni con un `pytest` a secas (marker
`e2e_real`, deseleccionado por defecto en `pytest.ini`).

Solo se dispara manualmente vía el workflow `E2E Real Smoke Test`
(`workflow_dispatch`), gated por un GitHub Environment con aprobación requerida.
Antes de poder usarlo hace falta configurar, una sola vez, lo siguiente
(fuera de este repo):

1. **Agente de test dedicado en OpenClaw** — con personalidad/memoria aislada de
   los agentes de producción, y alguna tool/skill simple y determinista habilitada
   (para `test_tool_use.py`). Nunca se testea contra un agente real.
2. **Runner self-hosted dedicado**, separado del runner `green-house` existente
   (que sirve a otro repo). Registrar una segunda instancia de
   [`actions-runner`](https://github.com/actions/runner) en este servidor, con
   label `jota-e2e`, contra este repo.
3. **GitHub Environment `e2e-real-production`** (Settings → Environments):
   - *Required reviewers*: el usuario propietario del repo, explícitamente.
   - *Deployment branches*: restringido a `main`.
   - *Environment variable* `E2E_TEST_AGENT`: nombre del agente de test del punto 1.

Ejecución manual local (sin pasar por GitHub Actions), útil para validar cambios
en la suite antes de fiarse del workflow:

```bash
PYTHONPATH=. E2E_TEST_AGENT=<agente-de-test> pytest tests/e2e -m e2e_real -v
```

`OPENCLAW_TOKEN` y `ADMIN_TOKEN` nunca se guardan en GitHub — el proceso de test
los lee del `.env` local del servidor, igual que la propia app.

**Nota para el workflow de GitHub Actions:** el checkout que hace `actions/checkout`
en el runner es limpio y **no incluye `.env`** (está en `.gitignore`). Por eso, para
la ruta de CI, `ADMIN_TOKEN` y `OPENCLAW_TOKEN` deben estar exportados como
variables de entorno reales en el propio proceso del runner self-hosted (p. ej.
vía `Environment=` en su unidad systemd) — no en un `.env` dentro del checkout ni
en GitHub Secrets. El job incluye un paso de verificación previo al de tests que
falla explícitamente (`exit 1`) si alguna de las dos falta, para que un runner mal
configurado rompa de forma visible en lugar de pasar en silencio sin cobertura
real.

---

## Arquitectura interna

El gateway instancia un `JotaBridge` por cada sesión WebSocket. Los tres servicios downstream (OpenClaw, Transcriber, TTS) tienen reconexión automática con backoff exponencial, unificada en `services/reconnection.py` (estados compartidos CONNECTED / RECONNECTING / DEGRADED). `OpenClawClient` y `ReconnectingTTSClient` son singletons de proceso; `ReconnectingTranscriberClient` es uno por sesión de audio.

```
src/
├── api/
│   ├── routes.py            WebSocket /ws/stream — handshake, ready, bridge lifecycle
│   ├── admin_routes.py      /admin/* — CRUD de clientes + observabilidad (sesiones, orquestador, transcriptor, TTS)
│   ├── openai_routes.py     /v1/models, /v1/chat/completions
│   ├── health_routes.py     /healthz, /ready
│   └── deps.py              get_admin_auth — dependencia de auth para admin
├── core/
│   ├── config.py            Settings (pydantic-settings, .env)
│   ├── cache.py             make_cache() — TTLCache + threading.Lock (cross-thread: event loop + Starlette threadpool)
│   └── session_key.py       make_session_key() — formato canónico de session key
├── db/
│   ├── models.py            ClientRecord (SQLModel) — identidad y config por cliente
│   └── database.py          get_engine() / run_migrations() / get_db_session()
├── cli.py                   CLI — add-client / list-clients / (de)activate-client / delete-client
└── services/
    ├── bridge.py            JotaBridge — coordinador de sesión WS
    ├── db_client.py         DbClient — lee ClientRecord de SQLite local (singleton, caché 10s)
    ├── reconnection.py      ConnectionState / ServiceStatus / to_wire_state() — compartido por los 3 wrappers
    ├── transcriber_client.py       WebSocket → jota-transcriber, protocolo puro (sin reconexión)
    ├── transcriber_reconnecting.py ReconnectingTranscriberClient — uno por sesión de audio, backoff en background
    ├── tts_client.py                WebSocket → jota-tts, protocolo puro, creado por turno
    ├── tts_reconnecting.py          ReconnectingTTSClient — singleton, backoff perezoso por turno
    ├── orchestration.py     call_orchestrator() — helper compartido WS+HTTP
    ├── pipeline_tracker.py  PipelineTracker — latencias por turno
    ├── session_registry.py  SessionRegistry — observabilidad de sesiones
    └── openclaw/
        ├── client.py        OpenClawClient — WebSocket v4, multiplexado
        ├── reconnecting.py  ReconnectingOpenClawClient — backoff + DEGRADED
        ├── dispatcher.py    FrameDispatcher — enruta frames a turn/client registry
        ├── registry.py      TurnRegistry + ClientRegistry (+ broadcast_status a todas las sesiones)
        └── models.py        GatewayInfo, AgentInfo
```
