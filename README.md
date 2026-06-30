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
  │  jota-db          HTTP REST (solo auth)  │
  └──────────────────────────────────────────┘
```

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
| `/admin/clients` | GET | Lista clientes *(pendiente DB interna — devuelve 501)* |
| `/admin/clients` | POST | Crear cliente *(pendiente — 501)* |
| `/admin/clients/{id}` | GET | Detalle *(pendiente — 501)* |
| `/admin/clients/{id}` | PATCH | Actualizar *(pendiente — 501)* |
| `/admin/clients/{id}` | DELETE | Borrar *(pendiente — 501)* |
| `/admin/clients/{id}/rotate-key` | POST | Rotar `client_key` *(pendiente — 501)* |

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
| `JOTA_DB_BASE_URL` | `localhost:8001` | jota-db — solo para auth de clientes |
| `JOTA_DB_API_KEY` | — | API key para jota-db |
| `TRANSCRIBER_WS_URL` | `localhost:9000` | jota-transcriber |
| `TTS_WS_URL` | `localhost:8005` | jota-tts |
| `TTS_TOKEN` | `gateway` | Token de auth para jota-tts |
| `OPENCLAW_HOST` | `127.0.0.1` | Host de OpenClaw |
| `OPENCLAW_PORT` | `18789` | Puerto de OpenClaw |
| `OPENCLAW_TOKEN` | — | Token de auth para OpenClaw (obligatorio) |
| `ADMIN_TOKEN` | — | Token para endpoints `/admin/*`; vacío deshabilita el admin API |
| `ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión (s) |
| `ORCHESTRATOR_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión (s) |
| `ORCHESTRATOR_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta DEGRADED (s) |
| `TRANSCRIBER_SILENCE_TIMEOUT_S` | `25` | Silencio máximo sin transcripción antes de degradar (s) |

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

## Arquitectura interna

El gateway instancia un `JotaBridge` por cada sesión WebSocket. `OpenClawClient` es un singleton persistente envuelto en `ReconnectingOpenClawClient` (reconexión automática con backoff exponencial; estados CONNECTED / RECONNECTING / DEGRADED).

```
src/
├── api/
│   ├── routes.py            WebSocket /ws/stream — handshake, ready, bridge lifecycle
│   ├── admin_routes.py      /admin/* — observabilidad + CRUD stubs
│   ├── openai_routes.py     /v1/models, /v1/chat/completions
│   ├── health_routes.py     /healthz, /ready
│   └── deps.py              get_admin_auth — dependencia de auth para admin
├── core/
│   ├── config.py            Settings (pydantic-settings, .env)
│   ├── cache.py             make_cache() — TTLCache + asyncio.Lock
│   └── session_key.py       make_session_key() — formato canónico de session key
└── services/
    ├── bridge.py            JotaBridge — coordinador de sesión WS
    ├── db_client.py         HTTP → jota-db (singleton, caché 60s)
    ├── transcriber_client.py  WebSocket → jota-transcriber (por sesión)
    ├── tts_client.py        WebSocket → jota-tts (creado por turno)
    ├── orchestration.py     call_orchestrator() — helper compartido WS+HTTP
    ├── pipeline_tracker.py  PipelineTracker — latencias por turno
    ├── session_registry.py  SessionRegistry — observabilidad de sesiones
    └── openclaw/
        ├── client.py        OpenClawClient — WebSocket v4, multiplexado
        ├── reconnecting.py  ReconnectingOpenClawClient — backoff + DEGRADED
        ├── dispatcher.py    FrameDispatcher — enruta frames a turn/client registry
        ├── registry.py      TurnRegistry + ClientRegistry
        └── models.py        GatewayInfo, AgentInfo
```
