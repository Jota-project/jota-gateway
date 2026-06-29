# jota-gateway

BFF (Backend For Frontend) del ecosistema Jota IA. Punto de entrada único para todos los clientes (ESP32, web, app, Home Assistant).

```
ESP32 / Web / App / Home Assistant
        │
        ▼
  jota-gateway (FastAPI :8004)
  ├── /ws/stream          WebSocket — sesión interactiva de voz/texto
  ├── /api/*              REST API — config, historial, modelos, observabilidad
  └── /v1/*               OpenAI-compatible — integración Home Assistant
        │
        ▼
  ┌─────────────────────────────────────────┐
  │            Microservicios               │
  │  OpenClaw         WebSocket v4          │
  │  jota-transcriber WebSocket             │
  │  jota-tts         WebSocket             │
  │  jota-db          HTTP REST             │
  └─────────────────────────────────────────┘
```

---

## Endpoints

| Endpoint | Método | Auth | Descripción |
|---|---|---|---|
| `/ws/stream` | WebSocket | `client_key` en handshake | Sesión interactiva de voz/texto |
| `/api/health` | GET | X-API-Key | Estado de todos los microservicios |
| `/api/models` | GET | X-API-Key | Modelos disponibles |
| `/api/config` | GET | X-API-Key | Config del cliente autenticado |
| `/api/config` | PUT | X-API-Key | Actualizar config |
| `/api/config/reset` | POST | X-API-Key | Reset a valores por defecto |
| `/api/conversations` | GET | X-API-Key | Historial de conversaciones |
| `/api/conversations/{id}/messages` | GET | X-API-Key | Mensajes de una conversación |
| `/api/conversations/{id}` | DELETE | X-API-Key | Archivar conversación |
| `/api/orchestrators/{name}/status` | GET | X-API-Key | Estado de conexión del orquestador |
| `/api/orchestrators/{name}/reconnect` | POST | X-API-Key | Forzar reconexión del orquestador |
| `/api/sessions` | GET | X-API-Key | Sesiones activas y recientes |
| `/api/sessions/{id}` | GET | X-API-Key | Detalle de una sesión |
| `/v1/models` | GET | ninguna | Lista de modelos (OpenAI-compatible) |
| `/v1/chat/completions` | POST | ninguna | Chat completion (OpenAI-compatible) |

Los endpoints `/api/*` requieren cabecera `X-API-Key`. Los `/v1/*` no tienen autenticación — expuestos solo en LAN via nginx.

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

Variables en `.env`:

| Variable | Default | Descripción |
|---|---|---|
| `JOTA_DB_BASE_URL` | `localhost:8001` | jota-db (`host:port`, sin protocolo) |
| `JOTA_DB_API_KEY` | — | API key para jota-db |
| `TRANSCRIBER_WS_URL` | `localhost:9000` | jota-transcriber (`host:port`) |
| `TTS_WS_URL` | `localhost:8005` | jota-tts (`host:port`) |
| `TTS_TOKEN` | `gateway` | Token de auth para jota-tts |
| `OPENCLAW_HOST` | `127.0.0.1` | Host de OpenClaw |
| `OPENCLAW_PORT` | `18789` | Puerto de OpenClaw |
| `OPENCLAW_TOKEN` | — | Token de auth para OpenClaw (obligatorio) |
| `OPENCLAW_DEFAULT_AGENT` | `main` | Agente OpenClaw por defecto |
| `DEFAULT_ORCHESTRATOR` | `openclaw` | Orquestador activo en el registry |
| `ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF` | `1.0` | Backoff inicial en reconexión (s) |
| `ORCHESTRATOR_RECONNECT_MAX_BACKOFF` | `60.0` | Backoff máximo en reconexión (s) |
| `ORCHESTRATOR_RECONNECT_MAX_DURATION` | `300.0` | Tiempo hasta entrar en DEGRADED (s) |
| `TRANSCRIBER_SILENCE_TIMEOUT_S` | `25` | Silencio máximo antes de cerrar sesión de audio (s) |

> Todos los campos `*_URL` son `host:port` sin protocolo. El protocolo (`ws://`, `http://`) se inyecta dentro de cada cliente de servicio.

---

## Protocolo de cliente WebSocket

Ver [`docs/client-protocol.md`](docs/client-protocol.md) para la guía completa de integración.

---

## Arquitectura interna

El gateway instancia un `JotaBridge` por cada sesión WebSocket. El orquestador (`OpenClawClient`) es un singleton persistente gestionado en el lifespan de la app, envuelto en `ReconnectingOrchestrator` (reconexión automática con backoff exponencial; estados CONNECTED / RECONNECTING / DEGRADED).

```
src/
├── api/
│   ├── routes.py                    WebSocket /ws/stream
│   ├── openai_routes.py             /v1/chat/completions, /v1/models
│   ├── orchestrator_routes.py       /api/orchestrators/*
│   ├── sessions_routes.py           /api/sessions
│   ├── config_routes.py             /api/config
│   ├── conversation_routes.py       /api/conversations
│   ├── health_routes.py             /api/health
│   ├── models_routes.py             /api/models
│   └── deps.py                      dependencia de autenticación (X-API-Key)
├── core/
│   ├── config.py                    Settings (pydantic-settings, .env)
│   ├── cache.py                     make_cache() — TTLCache + asyncio.Lock
│   └── session_key.py               make_session_key() — formato canónico
└── services/
    ├── bridge.py                    JotaBridge — coordinador de sesión WS
    ├── db_client.py                 HTTP → jota-db (singleton)
    ├── transcriber_client.py        WebSocket → jota-transcriber (por sesión)
    ├── tts_client.py                WebSocket → jota-tts (creado por turno)
    ├── orchestration.py             call_orchestrator() — helper compartido WS+HTTP
    ├── pipeline_tracker.py          PipelineTracker — latencias por turno
    ├── session_registry.py          SessionRegistry — observabilidad de sesiones
    └── orchestrators/
        ├── protocol.py              OrchestratorProtocol, OrchestratorEvent
        ├── openclaw_client.py       OpenClaw WebSocket v4
        ├── reconnecting.py          ReconnectingOrchestrator (state machine)
        └── registry.py             OrchestratorRegistry + build_registry()
```
