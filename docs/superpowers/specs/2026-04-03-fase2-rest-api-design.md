# Fase 2 — REST API pública del Gateway

**Fecha:** 2026-04-03
**Repo:** jota-gateway
**Issue:** #1
**Dependencias:** ninguna (DbClient ya disponible desde Fase 1)

---

## Contexto

El gateway actúa como BFF (Backend For Frontend) para dispositivos cliente (ESP32, web). Hasta ahora solo exponía un WebSocket (`/ws/stream`). Esta fase añade una capa REST HTTP que permite a los clientes consultar y modificar su configuración, leer su historial de conversaciones y conocer los modelos disponibles. El endpoint de health es para monitorización del operador.

El `DbClient` singleton ya tiene todos los métodos necesarios para config y conversaciones. Solo hay que añadir `get_models()` y exponer todo vía FastAPI.

---

## Estructura de ficheros

```
src/api/
├── deps.py                  # dependency get_verified_client()
├── routes.py                # WS /ws/stream — sin tocar
├── transcribe.py            # sin tocar
├── config_routes.py         # GET/PUT /api/config, POST /api/config/reset
├── conversation_routes.py   # GET /api/conversations, GET /api/conversations/{id}/messages
├── models_routes.py         # GET /api/models
└── health_routes.py         # GET /api/health (sin auth)
```

Split por dominio para escalar al dashboard de admin en los próximos días.

---

## Auth — `deps.py`

Todos los endpoints de datos requieren `X-API-Key` en el header, que se resuelve contra jota-db en cada request (sin caché por ahora — issue futura).

```python
async def get_verified_client(
    x_api_key: str = Header(...)
) -> tuple[Client, ClientConfig]:
    """
    Llama db_client.get_session(x_api_key).
    401 si la key es inválida/inactiva.
    503 si jota-db no está disponible.
    """
```

`GET /api/health` no usa esta dependency — es pública.

---

## Endpoints

### Config — `config_routes.py`

| Método | Path | Acción |
|---|---|---|
| `GET` | `/api/config` | `db_client.get_config(client.id)` |
| `PUT` | `/api/config` | `db_client.update_config(client.id, body)` — campos parciales |
| `POST` | `/api/config/reset` | `db_client.reset_config(client.id)` |

Los tres devuelven `ClientConfig`. El PUT acepta cualquier subconjunto de campos de `ClientConfig`; jota-db aplica el merge.

### Conversaciones — `conversation_routes.py`

| Método | Path | Acción |
|---|---|---|
| `GET` | `/api/conversations` | `db_client.get_conversations(client.id)` |
| `GET` | `/api/conversations/{id}/messages` | `db_client.get_messages(conversation_id)` |

Nota: `get_messages()` en `DbClient` no pasa `X-Client-Id` actualmente — hay que corregirlo para que jota-db valide ownership.

### Modelos — `models_routes.py`

| Método | Path | Acción |
|---|---|---|
| `GET` | `/api/models` | `db_client.get_models()` — nuevo método en DbClient |

Llama directamente a `GET /models` en jota-db. El endpoint equivalente en jota-orchestrator es un proxy redundante — issue abierta para limpiarlo.

### Health — `health_routes.py`

| Método | Path | Auth |
|---|---|---|
| `GET` | `/api/health` | ninguna — uso de operador |

Hace pings paralelos a los tres servicios y devuelve siempre `200`:

```json
{
  "orchestrator": "ok",
  "transcriber": "ok",
  "tts": "ok"
}
```

Si un servicio no responde, su valor es `"unavailable"`. El endpoint no falla con 5xx — es informativo.

Implementación:
- Orchestrator: `OrchestratorClient.ping()` (HTTP GET `/health`) — instancia temporal con `GATEWAY_KEY`
- TTS: `TTSClient.ping()` (método estático, intenta abrir WS)
- Transcriber: `TranscriberClient.ping()` — **nuevo método estático** (HTTP GET `http://{TRANSCRIBER_WS_URL}/health`)

---

## Convención de URLs en config

Los valores en `.env` y `config.py` almacenan solo `host[:puerto]`, sin protocolo. El código inyecta el protocolo y el path en el punto de uso:

| Variable | Valor en .env | Uso WS | Uso HTTP |
|---|---|---|---|
| `JOTA_DB_BASE_URL` | `localhost:8001` | — | `http://{url}` |
| `ORCHESTRATOR_BASE_URL` | `localhost:8000` | — | `http://{url}` |
| `TRANSCRIBER_WS_URL` | `localhost:9000` | `ws://{url}/api/stt` | `http://{url}/health` |
| `TTS_WS_URL` | `localhost:8005` | `ws://{url}/ws` | — |

Los paths (`/api/stt`, `/ws`, `/health`) van hardcodeados en el código, no en el `.env`.

---

## Cambios a ficheros existentes

| Fichero | Cambio |
|---|---|
| `src/core/config.py` | Eliminar protocolos de los valores por defecto |
| `src/services/db_client.py` | Inyectar `http://` en constructor; añadir `get_models()` |
| `src/services/orchestrator_client.py` | Inyectar `http://` en constructor |
| `src/services/tts_client.py` | Inyectar `ws://` y path `/ws` en connect y ping |
| `src/services/transcriber_client.py` | Inyectar `ws://` y path `/api/stt` en connect; añadir `ping()` estático HTTP |
| `src/main.py` | Montar los 3 nuevos routers bajo prefix `/api` |

---

## Manejo de errores

| Situación | HTTP |
|---|---|
| `X-API-Key` ausente | `422` (FastAPI automático) |
| Key inválida/inactiva (jota-db 401/403) | `401 Unauthorized` |
| jota-db no disponible | `503 Service Unavailable` |
| Recurso no encontrado | `404` (propagado desde jota-db) |
| Error inesperado | `502 Bad Gateway` |

---

## Issues abiertas durante el diseño

1. **Cache en `get_verified_client()`** — cada request REST hace un round-trip a jota-db. Añadir caché con TTL corto en memoria como mejora futura.
2. **`DELETE /api/conversations/{id}`** — jota-db no tiene endpoint de borrado/archivo todavía. Revisar y añadir cuando jota-db lo soporte (existe `PATCH /conversations/{id}` con `status: "archived"`).
3. **Proxy redundante en jota-orchestrator** — `GET /models` en el orchestrator es un proxy a jota-db. Limpiar una vez el gateway sea el punto de entrada consolidado.
4. **Admin / Dashboard** — la REST API actual es solo para clientes. Cuando llegue el dashboard de admin, crear un servicio separado (`jota-admin`) que hable directamente con jota-db, en lugar de extender este gateway.

---

## Fuera de scope (Fase 3)

- Propagar `ClientConfig` (TTS voice/speed, model_id, system_prompt_extra, barge_in) a los servicios internos.
- Añadir `x-client-id` al orchestrator (issue #20).
- `transcribe_file()` con token y vad_thold (issue #21).
