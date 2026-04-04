# TASKS — jota-gateway

Estado actual del proyecto y trabajo pendiente. Actualizado: 2026-04-04.

---

## Estado actual: v1.4.0

El gateway está completo en su fase BFF (Backend-For-Frontend). Expone:

- **WebSocket** `/ws/stream` — sesión bidireccional con transcriber + orchestrator + TTS
- **REST API** `/api/*` — endpoints para config, historial, modelos y health

---

## Arquitectura (v1.4.0)

```
ESP32 / Web Client
       │
       ▼
jota-gateway (FastAPI)
  ├── /ws/stream          ← WebSocket BFF (JotaBridge)
  └── /api/*              ← REST API pública
       ├── GET  /health
       ├── GET  /models
       ├── GET  /config
       ├── PUT  /config
       ├── POST /config/reset
       ├── GET  /conversations
       ├── GET  /conversations/{id}/messages
       └── DELETE /conversations/{id}
            │
            ▼ (archiva vía PATCH a jota-db)
       ┌────────────────────────────────┐
       │         Microservicios         │
       │  jota-db         (HTTP REST)   │
       │  jota-orchestrator (HTTP NDJSON)│
       │  jota-transcriber  (WebSocket) │
       │  jota-tts          (WebSocket) │
       └────────────────────────────────┘
```

### Convención de URLs

Todas las URLs en `settings` son `host:port` sin protocolo. El protocolo se inyecta en el punto de uso dentro de cada cliente de servicio.

### Capa de caché

`src/core/cache.py` expone `make_cache(maxsize, ttl)` → `(TTLCache, asyncio.Lock)`.

| Recurso          | TTL   | Maxsize |
|------------------|-------|---------|
| `get_session()`  | 60 s  | 500     |
| `get_models()`   | 300 s | 1       |

El lock envuelve solo el acceso al dict, nunca la llamada IO.

---

## Historial de releases

| Versión | Descripción                                                                 |
|---------|-----------------------------------------------------------------------------|
| v1.0.0  | Base: WebSocket BFF, JotaBridge, TranscriberClient, OrchestratorClient, TtsClient |
| v1.1.0  | Refactor URL convention + DbClient completo + `TranscriberClient.ping()`   |
| v1.2.0  | Fase 2 REST API: config, conversations, models, health + `get_verified_client` dep |
| v1.2.1  | Fix: header `x-client-id` enviado al orchestrator                          |
| v1.3.0  | Cache TTL en `get_session()` y `get_models()` (cierra #22, #23)            |
| v1.4.0  | `DELETE /api/conversations/{id}` — archiva conversación (cierra #24)       |

---

## Issues cerradas

| Issue | Descripción                                                    |
|-------|----------------------------------------------------------------|
| #1    | Fase 2 REST API pública                                        |
| #20   | Enviar `x-client-id` al orchestrator                          |
| #21   | Eliminar `transcribe_file` (deprecado)                        |
| #22   | Resiliencia jota-db — caché ante caídas breves               |
| #23   | Reducir latencia de sesión con caché de `get_session()`       |
| #24   | `DELETE /api/conversations/{id}` — archivar conversación      |

---

## Pendiente

### #8 — Crear suite de test y CI

La única issue abierta. Abarca:

- Tests unitarios para `DbClient`, `OrchestratorClient`, `TranscriberClient`
- Tests de integración para los endpoints REST (`/api/config`, `/api/conversations`, `/api/models`, `/api/health`)
- Tests del WebSocket BFF (`/ws/stream`) con mocks de los microservicios internos
- Pipeline CI en GitHub Actions (lint + test en cada PR)

---

## Estructura de ficheros clave

```
src/
├── api/
│   ├── deps.py                 ← get_verified_client (autenticación REST)
│   ├── routes.py               ← WebSocket /ws/stream
│   ├── config_routes.py        ← GET/PUT /api/config, POST /api/config/reset
│   ├── conversation_routes.py  ← GET /api/conversations, GET /messages, DELETE
│   ├── health_routes.py        ← GET /api/health
│   └── models_routes.py        ← GET /api/models
├── core/
│   ├── cache.py                ← make_cache() — utilidad TTL cache
│   └── config.py               ← Settings (pydantic-settings)
├── services/
│   ├── db_client.py            ← Cliente HTTP jota-db (singleton)
│   ├── orchestrator_client.py  ← Cliente HTTP NDJSON jota-orchestrator
│   ├── transcriber_client.py   ← Cliente WebSocket jota-transcriber
│   └── tts_client.py           ← Cliente WebSocket jota-tts
└── models/
    └── schemas.py              ← Pydantic models compartidos
```
