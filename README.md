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
| `/admin/transcriber/status` | GET | Reachability en vivo del transcriptor (`TranscriberClient.ping()` — no hay conexión de proceso, es uno por sesión) |
| `/admin/tts/status` | GET | Estado del breaker de reconexión de TTS (CONNECTED / RECONNECTING, intentos, último error) |
| `/admin/clients` | GET | Lista clientes |
| `/admin/clients` | POST | Crear cliente (`client_key` generado o provisto) |
| `/admin/clients/{id}` | GET | Detalle de un cliente |
| `/admin/clients/{id}` | PATCH | Actualizar cliente (campos parciales) |
| `/admin/clients/{id}` | DELETE | Borrar cliente |
| `/admin/clients/{id}/rotate-key` | POST | Rotar `client_key` |

Los tres endpoints `*/status` devuelven la misma forma: `{"name", "state", "connected_at", "reconnect_attempts", "last_error"}`.

Auth: header `X-Admin-Token`. Sin header → 422. Token incorrecto → 401. `ADMIN_TOKEN` vacío → 503.

### OpenAI-compatible (sin auth, LAN-only vía nginx)

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/v1/models` | GET | Lista estática — devuelve `id: "jota-gateway"` |
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
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8004 --reload
# o con Docker:
docker compose up
```

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
| `ORCHESTRATOR_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión del orquestador (s) |
| `ORCHESTRATOR_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta DEGRADED del orquestador (s) |
| `TRANSCRIBER_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión del transcriptor (s) |
| `TRANSCRIBER_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión del transcriptor (s) |
| `TRANSCRIBER_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta DEGRADED del transcriptor (s) |
| `TTS_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial del breaker de TTS (s) |
| `TTS_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo del breaker de TTS (s) — sin estado DEGRADED, cada turno elegible reintenta |

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
│   ├── cache.py             make_cache() — TTLCache + asyncio.Lock
│   └── session_key.py       make_session_key() — formato canónico de session key
├── db/
│   ├── models.py            ClientRecord (SQLModel) — identidad y config por cliente
│   └── database.py          get_engine() / create_db_and_tables() / get_db_session()
├── cli.py                   CLI — add-client / list-clients / (de)activate-client / delete-client
└── services/
    ├── bridge.py            JotaBridge — coordinador de sesión WS
    ├── db_client.py         DbClient — lee ClientRecord de SQLite local (singleton, caché 60s)
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
