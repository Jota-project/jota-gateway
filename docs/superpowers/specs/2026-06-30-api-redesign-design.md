# API Redesign — jota-gateway

**Date:** 2026-06-30
**Status:** Approved

## Contexto

jota-gateway es un BFF cuyo propósito es tomar orquestadores AI single-user (OpenClaw como referencia) y convertirlos en multi-cliente: gestiona routing, identidad, pipeline de audio/texto y personalización por cliente. No es un proxy pasivo — es la capa que hace posible que varias personas e dispositivos interactúen con un orquestador pensado para un solo usuario.

Este documento especifica la reorganización completa de la superficie de API del gateway como resultado de dos cambios arquitectónicos:

1. **jota-db queda eliminado como dependencia** (salvo auth, que también se internaliza). Ver spec `2026-06-30-sqlite-internal-db.md` para la base de datos interna.
2. **El historial de conversaciones y los modelos los gestiona el orquestador (OpenClaw)**, no el gateway.

---

## Superficie de API — Mapa completo

```
jota-gateway :8004
│
├── WS  /ws/stream                          — sesión tiempo real
│        Auth: client_key en handshake
│        Consumidores: ESP32, web client
│
├── HTTP /v1/models                         — OpenAI-compat, lista estática
├── HTTP /v1/chat/completions               — OpenAI-compat, stateless
│        Auth: ninguna (LAN-only vía nginx)
│        Consumidores: Home Assistant, cualquier cliente OpenAI-compatible
│
├── HTTP /admin/clients                     — CRUD de clientes
├── HTTP /admin/clients/{id}
├── HTTP /admin/clients/{id}/rotate-key
├── HTTP /admin/sessions                    — observabilidad
├── HTTP /admin/sessions/{id}
├── HTTP /admin/orchestrators/{name}/status
├── HTTP /admin/orchestrators/{name}/reconnect
│        Auth: header X-Admin-Token (env var ADMIN_TOKEN)
│        Consumidores: operador, CLI interno (docker exec)
│
├── HTTP /healthz                           — liveness probe
└── HTTP /ready                             — readiness probe con checks reales
         Auth: ninguna
         Consumidores: Docker healthcheck, load balancer, nginx
```

---

## Sección 1 — Protocolo WebSocket tipado

### Principios

- **Text frames**: mensajes de control JSON, siempre con campo `type`
- **Binary frames cliente → gateway**: PCM Float32 16kHz crudo (audio de entrada)
- **Binary frames gateway → cliente**: header 3 bytes + PCM16 24kHz (audio de salida)
- El cliente nunca necesita inferir contexto — cada mensaje lleva todo lo necesario

### Mensajes Client → Gateway

#### `handshake` *(primer mensaje, obligatorio)*
```json
{
  "type": "handshake",
  "client_key": "abc123",
  "input_mode": "audio" | "text",
  "output_mode": ["audio", "text", "status"],
  "agent": "main"
}
```
`agent` es opcional — si se omite, el gateway usa el agente por defecto del cliente.

#### `send` *(texto explícito)*
```json
{ "type": "send", "text": "enciende la luz" }
```

#### `end` *(fin de utterance en modo audio)*
```json
{ "type": "end" }
```

#### Binary frames (audio de entrada)
PCM Float32 16kHz sin header. El contexto (sesión activa en modo audio) hace innecesario cualquier framing adicional.

---

### Mensajes Gateway → Client

#### `ready` *(confirmación de handshake — primer mensaje del gateway)*
```json
{
  "type": "ready",
  "session_id": "uuid-...",
  "agent": "main",
  "input_mode": "audio",
  "output_mode": ["audio", "text"],
  "capabilities": {
    "barge_in": true,
    "tts": true,
    "transcriber": true
  }
}
```
El cliente sabe exactamente qué modos están activos y puede ajustar su UI.

#### `turn_start`
```json
{ "type": "turn_start", "turn_id": "t-001", "turn_seq": 1 }
```
`turn_id` es un string con formato `t-{N}` donde N es un entero secuencial por sesión (empieza en 1). `turn_seq` es el mismo N como uint16, usado en el header de los binary frames de audio.

#### `token` *(texto streaming)*
```json
{ "type": "token", "turn_id": "t-001", "text": "Hola" }
```

#### `turn_end`
```json
{ "type": "turn_end", "turn_id": "t-001" }
```

#### `error`
```json
{
  "type": "error",
  "code": "TTS_UNAVAILABLE",
  "message": "TTS no responde",
  "fatal": false,
  "turn_id": "t-001"
}
```

- `fatal: true` → el gateway cierra la conexión inmediatamente después
- `fatal: false` → degradación parcial, la sesión continúa
- `turn_id` es opcional (presente solo si el error está ligado a un turno concreto)

**Códigos de error:**

| Código | Fatal | Descripción |
|--------|-------|-------------|
| `AUTH_FAILED` | true | client_key inválida o inactiva |
| `AGENT_NOT_FOUND` | true | el agente solicitado no existe en OpenClaw |
| `ORCHESTRATOR_UNAVAILABLE` | true | OpenClaw no disponible al arrancar sesión |
| `TTS_UNAVAILABLE` | false | TTS caído, sesión continúa en modo texto |
| `TRANSCRIBER_UNAVAILABLE` | false | Transcriber caído, cliente puede pasar a modo texto |
| `TURN_ERROR` | false | Fallo en un turno concreto, sesión sigue |
| `INTERNAL_ERROR` | true/false | Error inesperado, `fatal` según criticidad |

#### `status` *(cambio de estado de un servicio durante la sesión)*
```json
{ "type": "status", "service": "tts", "state": "degraded" }
```
`service`: `orchestrator` | `transcriber` | `tts`
`state`: `ok` | `degraded` | `unavailable`

---

### Binary frames gateway → client (audio de salida)

```
┌──────────┬──────────────────────┬─────────────────────┐
│  0xA1    │  turn_seq (uint16 BE) │  PCM16 24kHz        │
│  1 byte  │  2 bytes              │  N bytes            │
└──────────┴──────────────────────┴─────────────────────┘
```

- `0xA1` — magic byte, distingue audio de posibles futuros binary frames
- `turn_seq` — el cliente descarta audio de secuencias anteriores si ya llegó un `turn_start` con seq mayor (barge-in sin mensaje adicional)

---

### Flujo de sesión típica

```
Client                          Gateway
  │──── handshake ────────────────▶│
  │◀─── ready ─────────────────────│
  │                                 │
  │──── [PCM Float32] ─────────────▶│
  │──── [PCM Float32] ─────────────▶│
  │──── end ───────────────────────▶│
  │◀─── turn_start (seq=1) ─────────│
  │◀─── token "Hola" ───────────────│
  │◀─── [0xA1][0x01][PCM16...] ─────│
  │◀─── turn_end ───────────────────│
  │                                 │
  │──── [PCM Float32] ─────────────▶│  ← barge-in
  │◀─── turn_start (seq=2) ─────────│  ← cliente descarta audio seq=1
  │◀─── token "Claro" ──────────────│
  │◀─── [0xA1][0x02][PCM16...] ─────│
  │◀─── turn_end ───────────────────│
```

---

## Sección 2 — Superficie HTTP

### `/v1/*` — OpenAI-compat (sin auth)

**`GET /v1/models`**
Respuesta estática. Necesaria para que clientes OpenAI (HA, Open WebUI, etc.) puedan conectar sin configuración especial.
```json
{
  "object": "list",
  "data": [{ "id": "jota-gateway", "object": "model", "created": 0, "owned_by": "jota-gateway" }]
}
```

**`POST /v1/chat/completions`**
Sin cambios funcionales. Soporta `stream: true/false`. Usa `default_agent` del orquestador para la session key `agent:{default}:ha`.

---

### `/admin/*` — Gestión y observabilidad

Auth: header `X-Admin-Token`. Si `ADMIN_TOKEN` env var está vacía → 503. Token incorrecto → 401.

#### Clientes

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/admin/clients` | Lista todos los clientes |
| `POST` | `/admin/clients` | Crear cliente → devuelve objeto completo incluyendo `client_key` generada |
| `GET` | `/admin/clients/{id}` | Detalle de un cliente (`client_key` omitida por seguridad) |
| `PATCH` | `/admin/clients/{id}` | Actualizar campos (nombre, config, estado...) |
| `DELETE` | `/admin/clients/{id}` | Borrar cliente |
| `POST` | `/admin/clients/{id}/rotate-key` | Regenerar `client_key` → devuelve `{"client_key": "nueva-key"}` |

#### Observabilidad

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/admin/sessions` | Sesiones activas y recientes en memoria |
| `GET` | `/admin/sessions/{session_id}` | Detalle con eventos y latencias por turno |
| `GET` | `/admin/orchestrators/{name}/status` | Estado de un orquestador |
| `POST` | `/admin/orchestrators/{name}/reconnect` | Forzar reconexión (responde 202) |

---

### `/healthz` y `/ready`

**`GET /healthz`** — liveness. Siempre 200 si el proceso responde.
```json
{ "status": "ok" }
```

**`GET /ready`** — readiness. Pinga OpenClaw, transcriber y TTS.

| Estado | HTTP | Cuándo |
|--------|------|--------|
| `"ok"` | 200 | Todos los servicios responden |
| `"degraded"` | 200 | TTS o transcriber no disponibles (OpenClaw ok) |
| `"unavailable"` | 503 | OpenClaw no disponible |

```json
{
  "status": "degraded",
  "services": {
    "orchestrator": "ok",
    "transcriber": "unavailable",
    "tts": "ok"
  }
}
```

OpenClaw es el único servicio que bloquea con 503 — sin él no hay sesiones posibles.

---

### Modelo de error HTTP unificado

Todas las rutas devuelven errores en formato compatible con OpenAI:
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid or inactive API key"
  }
}
```

Esto permite que clientes que ya parsean errores de `/v1/` consuman `/admin/` sin lógica adicional.

---

## Cambios respecto al estado actual

### Endpoints eliminados

| Endpoint actual | Motivo |
|-----------------|--------|
| `GET /api/conversations` | Historial gestionado por OpenClaw |
| `GET /api/conversations/{id}/messages` | Ídem |
| `DELETE /api/conversations/{id}` | Ídem + semántica incorrecta (archivaba, no borraba) |
| `GET /api/models` | Modelos gestionados por OpenClaw |
| `GET /api/config` | Reemplazado por `/admin/clients/{id}` |
| `PUT /api/config` | Reemplazado por `PATCH /admin/clients/{id}` |
| `POST /api/config/reset` | Sin equivalente (la config reset se hace via PATCH) |
| `GET /health` (raíz) | Renombrado a `/healthz` |

### Endpoints movidos

| Antes | Después |
|-------|---------|
| `GET /api/health` | `GET /ready` |
| `GET /api/sessions` | `GET /admin/sessions` |
| `GET /api/sessions/{id}` | `GET /admin/sessions/{id}` |
| `GET /api/orchestrators/{name}/status` | `GET /admin/orchestrators/{name}/status` |
| `POST /api/orchestrators/{name}/reconnect` | `POST /admin/orchestrators/{name}/reconnect` |

### Archivos a eliminar

- `src/api/conversation_routes.py`
- `src/api/models_routes.py`
- `src/api/config_routes.py`

### Archivos a crear

- `src/api/admin_routes.py` — CRUD clientes + observabilidad

### Archivos a modificar

- `src/main.py` — registrar nuevos routers, eliminar obsoletos
- `src/api/health_routes.py` — reescribir con `/healthz` y `/ready` (actualmente tiene `/api/health`)
- `src/api/openai_routes.py` — limpiar `/v1/models` respuesta estática
- `src/api/deps.py` — añadir dependencia de auth para admin token
