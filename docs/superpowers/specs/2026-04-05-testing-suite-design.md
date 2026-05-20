# Testing Suite — jota-gateway

> Spec aprobado: 2026-04-05
> Relacionado con: jota-gateway #8

---

## Contexto

El gateway es el único punto de entrada del sistema Jota. Conecta clientes físicos (ESP32/web/app) con cuatro microservicios: jota-db (HTTP), jota-orchestrator (HTTP/NDJSON), jota-transcriber (WebSocket), jota-speaker/TTS (WebSocket).

Los tests actuales son todos unitarios con mocking total. Problemas concretos:
- Los endpoints REST no tienen ningún test.
- El handshake WebSocket no tiene ningún test.
- `DbClient` no tiene ningún test.
- Muchos tests existentes verifican que el framework funciona (httpx hace GET, websockets.connect manda JSON), no que el código del gateway es correcto.
- No hay CI.

---

## Decisiones de diseño

**Sin servicios externos en CI.** Los tests de integración corren siempre, sin Docker, sin microservicios reales. Esto permite desarrollar el gateway de forma independiente.

**Tests E2E contra servicios reales:** fuera de scope en este spec. Queda pendiente como trabajo futuro — ver "Out of scope" al final.

**Capa de intercepción: transporte real, sin servicios reales.** Se usa `respx` para interceptar tráfico `httpx` (jota-db, jota-orchestrator) y fake WebSocket servers en proceso para transcriber y TTS. Esto testea serialización, headers y protocolo — no solo lógica Python.

---

## Estructura de ficheros

```
tests/
  conftest.py                      # fixtures globales: app, TestClient
  unit/
    test_bridge_barge_in.py        # lógica de cancelación de turno y umbral
    test_bridge_send_guards.py     # send_json silencioso si cliente desconectado
    test_bridge_disconnect.py      # websocket.disconnect termina el loop
    test_transcriber_loop.py       # dispatch de callbacks (text, is_final)
  integration/
    conftest.py                    # respx fixtures + fake WS servers
    test_rest_auth.py              # API key: ausente, inválida, válida
    test_rest_health.py            # GET /api/health — todos ok / uno caído
    test_rest_config.py            # GET/PUT/POST /api/config + /reset
    test_rest_conversations.py     # GET lista, GET mensajes, DELETE
    test_rest_models.py            # GET /api/models — ok / db caída
    test_ws_handshake.py           # /ws/stream — auth, handshake, input modes
    test_bridge_flow.py            # texto → orchestrator → tokens → cliente
    test_bridge_audio_flow.py      # PCM → transcriber → orchestrator → TTS → PCM
```

### Ficheros eliminados del unit actual

| Fichero | Motivo |
|---|---|
| `test_orchestrator_ping.py` | Testea que httpx hace GET. No aporta valor. |
| `test_tts_ping.py` | Ídem. |
| `test_tts_connect.py` | Testea que websockets.connect manda JSON. No aporta valor. |
| `test_bridge_health_check.py` | Se cubre con mayor fidelidad en integración. |
| `test_bridge_config_propagation.py` | Se cubre con mayor fidelidad en integración. |
| `test_orchestrator_system_prompt.py` | Se cubre con mayor fidelidad en integración. |
| `test_orchestrator.py` (root) | Script manual de desarrollo. Eliminar. |

---

## Infraestructura de tests

### respx — intercepción HTTP

`respx` intercepta `httpx.AsyncClient` antes de que salga a la red. Si el gateway manda un header incorrecto o serializa mal el body, el mock no matchea y el test falla.

```python
# integration/conftest.py
@pytest.fixture
def mock_db(respx_mock):
    respx_mock.get("http://jota-db:8001/auth/session").mock(
        return_value=httpx.Response(200, json={
            "client": {"id": "uuid-123", "client_key": "key-abc", "is_active": True},
            "config": {"stt_language": "es", "tts_voice": "af_heart", "tts_speed": 1.0,
                       "stt_vad_thold": 0.0, "barge_in_enabled": True, "barge_in_min_chars": 5,
                       "preferred_model_id": None, "system_prompt_extra": None,
                       "conversation_memory_limit": 20}
        })
    )
    return respx_mock
```

### Fake WebSocket servers

Servidores async mínimos que implementan el protocolo real de cada microservicio. Se levantan en el proceso de test en un puerto libre.

**FakeTranscriber** — acepta handshake de config, emite `ready`, luego emite una transcripción cuando recibe audio:

```python
async def fake_transcriber_handler(ws):
    msg = json.loads(await ws.recv())
    assert msg["type"] == "config"
    await ws.send(json.dumps({"type": "ready", "protocol_version": 1, "session_id": "s-test"}))
    async for chunk in ws:
        await ws.send(json.dumps({"type": "transcription", "text": "hola mundo", "is_final": True}))
        break
```

**FakeTTS** — acepta handshake de auth, responde `auth_ok`, luego devuelve chunks PCM:

```python
async def fake_tts_handler(ws):
    msg = json.loads(await ws.recv())
    assert msg["type"] == "auth"
    await ws.send(json.dumps({"type": "auth_ok"}))
    async for chunk in ws:
        await ws.send(b"\x00" * 256)  # PCM fake
```

### FastAPI TestClient

```python
@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c

# WebSocket nativo en TestClient:
with client.websocket_connect("/ws/stream") as ws:
    ws.send_json({"client_key": "key-abc", "input_mode": "text", "output_mode": ["text"]})
    msg = ws.receive_json()
    assert msg["type"] == "ready"
```

---

## Cobertura por fichero

### Unit

| Fichero | Casos |
|---|---|
| `test_bridge_barge_in.py` | Barge-in se activa si `len(text) >= barge_in_min_chars`. Parciales no llegan al orchestrator. Turno activo se cancela. |
| `test_bridge_send_guards.py` | `send_json` falla silenciosamente si el cliente se desconecta mid-stream. Excepción no propaga. |
| `test_bridge_disconnect.py` | `{"type": "websocket.disconnect"}` rompe el loop sin excepción. |
| `test_transcriber_loop.py` | `listen_loop` llama al callback con `(text, True)` en final y `(text, False)` en parcial. |

### Integration — REST

| Fichero | Casos |
|---|---|
| `test_rest_auth.py` | Sin `X-API-Key` → 403. Key inválida (db devuelve 401) → 403. Key válida → pasa. |
| `test_rest_health.py` | Todos los servicios up → 200 con todos `"ok"`. Un servicio caído → 200 con ese campo `"unavailable"` (nunca 5xx). |
| `test_rest_config.py` | `GET` devuelve config del cliente. `PUT` parchea y reenvía a db. `POST /reset` llama a db. Campos inválidos → 422. |
| `test_rest_conversations.py` | `GET` lista conversaciones. `GET /{id}/messages`. `DELETE /{id}` archiva. ID inexistente → 404 propagado desde db. |
| `test_rest_models.py` | `GET` devuelve modelos. Db caída → 502. |

### Integration — WebSocket y Bridge

| Fichero | Casos |
|---|---|
| `test_ws_handshake.py` | Key inválida → WS se cierra con error. Handshake correcto → mensaje `ready`. `input_mode=text` no levanta transcriber. |
| `test_bridge_flow.py` | Texto → orchestrator recibe headers `x-client-key` y `x-client-id` correctos. `preferred_model_id` y `system_prompt_extra` de config aparecen en payload. Tokens llegan al cliente como `{"type":"token","content":"..."}`. |
| `test_bridge_audio_flow.py` | PCM → transcriber fake emite transcripción → gateway llama orchestrator → si `output_mode` incluye `audio`, llama a TTS y reenvía PCM al cliente. |

---

## CI — GitHub Actions

Fichero: `.github/workflows/test.yml`

- **Triggers:** push a cualquier rama, PR a `main`
- **Python:** 3.12
- **Pasos:** checkout → install deps → `ruff check` → `pytest tests/unit/` → `pytest tests/integration/`
- **Sin Docker, sin servicios externos**
- **Tiempo estimado:** < 10 segundos

---

## Estimación

- Tests unit a mantener: ~12
- Tests de integración nuevos: ~25
- **Total: ~35-40 tests**
- Ficheros nuevos de infraestructura: `conftest.py` (global), `integration/conftest.py`
- Dependencias nuevas: `respx`, `websockets` (ya en requirements)

---

## Out of scope — trabajo futuro

**Tests E2E contra servicios reales.**

Patrón: Docker Compose levanta todos los microservicios → pytest con `@pytest.mark.e2e` verifica flujos completos con inputs preparados (audio PCM pre-grabado, texto fijo). Cada servicio puede tener sus propios tests internos; el E2E verifica el sistema completo.

Pendiente crear issue en jota-gateway: *"Crear suite E2E con Docker Compose"*.

Los tests E2E no reemplazan esta suite — la complementan. CI sin dependencias sigue siendo la base.
