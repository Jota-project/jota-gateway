# Testing Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar los tests fragmentados actuales con una pirámide real: unit tests para lógica pura, integration tests via FastAPI TestClient + respx + fake WS servers, y GitHub Actions CI — todo sin servicios externos.

**Architecture:** Los tests REST usan `starlette.testclient.TestClient` con `respx` interceptando todo el tráfico httpx globalmente. Los tests de bridge WS usan `TestClient.websocket_connect()` con respx para el orchestrator y fake WS servers en hilos de background para transcriber/TTS. Los fixtures se organizan en dos `conftest.py`: global y de integración.

**Tech Stack:** pytest, pytest-asyncio (`asyncio_mode=auto` ya configurado), respx, httpx, starlette TestClient, websockets

---

## Mapa de ficheros

**Crear:**
- `tests/conftest.py`
- `tests/integration/conftest.py`
- `tests/integration/test_rest_auth.py`
- `tests/integration/test_rest_health.py`
- `tests/integration/test_rest_config.py`
- `tests/integration/test_rest_conversations.py`
- `tests/integration/test_rest_models.py`
- `tests/integration/test_ws_handshake.py`
- `tests/integration/test_bridge_flow.py`
- `tests/integration/test_bridge_audio_flow.py`
- `.github/workflows/test.yml`

**Modificar:**
- `requirements.txt` — añadir pytest, pytest-asyncio, respx
- `pytest.ini` — registrar marker `integration`

**Eliminar:**
- `tests/unit/test_orchestrator_ping.py`
- `tests/unit/test_tts_ping.py`
- `tests/unit/test_tts_connect.py`
- `tests/unit/test_bridge_health_check.py`
- `tests/unit/test_bridge_config_propagation.py`
- `tests/unit/test_orchestrator_system_prompt.py`
- `test_orchestrator.py` (root)

**Renombrar:**
- `tests/unit/test_transcriber_listen_loop.py` → `tests/unit/test_transcriber_loop.py`

---

## Task 1: Dependencias y conftest global

**Files:**
- Modify: `requirements.txt`
- Modify: `pytest.ini`
- Create: `tests/conftest.py`

- [ ] **Step 1: Añadir dependencias de test a requirements.txt**

Añadir al final de `requirements.txt`:
```
pytest>=8.0.0
pytest-asyncio>=0.23.0
respx>=0.21.0
```

- [ ] **Step 2: Actualizar pytest.ini para registrar marker**

Reemplazar contenido de `pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
markers =
    integration: marks tests as integration tests
```

- [ ] **Step 3: Crear tests/conftest.py**

```python
"""Global test fixtures shared across unit and integration tests."""
import pytest
from starlette.testclient import TestClient

from src.main import app
from src.services.db_client import db_client


@pytest.fixture
def test_client():
    """FastAPI TestClient con lifespan (conecta/cierra db_client)."""
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def clear_db_cache():
    """Limpia caché del db_client entre tests para evitar state leakage."""
    db_client._session_cache.clear()
    db_client._models_cache.clear()
    yield
    db_client._session_cache.clear()
    db_client._models_cache.clear()
```

- [ ] **Step 4: Instalar dependencias y verificar colección**

```bash
cd /home/sito/jota-gateway && source venv/bin/activate && pip install -r requirements.txt
pytest tests/ --collect-only 2>&1 | head -30
```

Expected: sin errores, los 10 tests unitarios actuales se colectan.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pytest.ini tests/conftest.py
git commit -m "test: add test dependencies and global fixtures"
```

---

## Task 2: Limpiar tests unitarios obsoletos

**Files:**
- Delete: 6 ficheros unit + `test_orchestrator.py` root
- Rename: `test_transcriber_listen_loop.py`

- [ ] **Step 1: Eliminar ficheros obsoletos**

```bash
cd /home/sito/jota-gateway
git rm tests/unit/test_orchestrator_ping.py \
       tests/unit/test_tts_ping.py \
       tests/unit/test_tts_connect.py \
       tests/unit/test_bridge_health_check.py \
       tests/unit/test_bridge_config_propagation.py \
       tests/unit/test_orchestrator_system_prompt.py \
       test_orchestrator.py
```

- [ ] **Step 2: Renombrar test del transcriber**

```bash
git mv tests/unit/test_transcriber_listen_loop.py tests/unit/test_transcriber_loop.py
```

- [ ] **Step 3: Ejecutar unit tests restantes**

```bash
pytest tests/unit/ -v
```

Expected: 4 ficheros, todos PASS: `test_bridge_barge_in`, `test_bridge_disconnect`, `test_bridge_send_guards`, `test_transcriber_loop`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: remove tests that verify framework behavior, not gateway logic"
```

---

## Task 3: Integration conftest — fixtures compartidos

**Files:**
- Create: `tests/integration/conftest.py`

Este fichero es el núcleo. Provee:
- Datos de test estándar (`VALID_KEY`, `CLIENT_UUID`, `SESSION_RESPONSE`)
- Fixture `mock_services`: intercepta todo el tráfico HTTP via respx
- Fixture `client`: TestClient con servicios ya mockeados
- Fixture `auth_headers`: header de auth válido

**Nota técnica:** `client` depende de `mock_services` para que el TestClient se cree DENTRO del contexto de respx. Así `db_client._http` (creado en el lifespan) queda bajo intercepción desde el primer request.

- [ ] **Step 1: Crear tests/integration/conftest.py**

```python
"""
Fixtures de integración.

HTTP (jota-db, orchestrator) interceptado por respx.
WebSocket (transcriber, TTS) con fake servers en hilos de background.
"""
import json
import pytest
import httpx
import respx
from starlette.testclient import TestClient

from src.main import app

# ---------------------------------------------------------------------------
# Datos de test estándar
# ---------------------------------------------------------------------------

VALID_KEY = "valid-key-abc"
CLIENT_UUID = "uuid-client-123"

SESSION_RESPONSE = {
    "client": {"id": CLIENT_UUID, "client_key": VALID_KEY, "is_active": True},
    "config": {
        "stt_language": "es",
        "stt_vad_thold": 0.0,
        "tts_voice": "af_heart",
        "tts_speed": 1.0,
        "preferred_model_id": None,
        "system_prompt_extra": None,
        "barge_in_enabled": True,
        "barge_in_min_chars": 5,
        "conversation_memory_limit": 20,
    },
}

CONFIG_RESPONSE = SESSION_RESPONSE["config"]

# Un token NDJSON mínimo para respuestas del orchestrator
NDJSON_ONE_TOKEN = b'{"type":"token","content":"Hola"}\n'

# ---------------------------------------------------------------------------
# respx: intercepta tráfico HTTP hacia jota-db y orchestrator
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_services():
    """
    Activa respx e intercepta todas las rutas HTTP que el gateway llama.
    Los tests individuales pueden sobreescribir rutas concretas.
    assert_all_mocked=False permite que routes no usadas no rompan el test.
    """
    with respx.mock(assert_all_mocked=False) as router:
        # --- jota-db: auth ---
        router.get("http://localhost:8001/auth/session").mock(
            side_effect=lambda req: (
                httpx.Response(200, json=SESSION_RESPONSE)
                if req.headers.get("x-api-key") == VALID_KEY
                else httpx.Response(401, json={"detail": "Invalid key"})
            )
        )
        # --- jota-db: config ---
        router.get("http://localhost:8001/config/me").mock(
            return_value=httpx.Response(200, json=CONFIG_RESPONSE)
        )
        router.put("http://localhost:8001/config/me").mock(
            return_value=httpx.Response(200, json=CONFIG_RESPONSE)
        )
        router.post("http://localhost:8001/config/me/reset").mock(
            return_value=httpx.Response(200, json=CONFIG_RESPONSE)
        )
        # --- jota-db: conversations ---
        router.get("http://localhost:8001/conversations").mock(
            return_value=httpx.Response(200, json=[{"id": "conv-1", "title": "Test"}])
        )
        router.get(url__regex=r"http://localhost:8001/conversations/.+/messages").mock(
            return_value=httpx.Response(200, json=[{"id": "msg-1", "content": "hola"}])
        )
        router.patch(url__regex=r"http://localhost:8001/conversations/.+").mock(
            return_value=httpx.Response(200, json={"id": "conv-1", "status": "archived"})
        )
        # --- jota-db: models ---
        router.get("http://localhost:8001/models").mock(
            return_value=httpx.Response(200, json=[{"id": "llama3", "name": "LLaMA 3"}])
        )
        # --- orchestrator: health ---
        router.get("http://localhost:8000/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        # --- orchestrator: quick (NDJSON streaming) ---
        router.post("http://localhost:8000/api/quick").mock(
            return_value=httpx.Response(
                200,
                content=NDJSON_ONE_TOKEN,
                headers={"content-type": "application/x-ndjson"},
            )
        )
        # --- transcriber: health ---
        router.get("http://localhost:9000/health").mock(
            return_value=httpx.Response(200)
        )
        # --- TTS: health ---
        router.get("http://localhost:8005/health").mock(
            return_value=httpx.Response(200)
        )
        yield router


@pytest.fixture
def client(mock_services):
    """TestClient con todos los servicios HTTP mockeados."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"x-api-key": VALID_KEY}
```

- [ ] **Step 2: Verificar que el conftest importa sin errores**

```bash
pytest tests/integration/ --collect-only 2>&1 | head -20
```

Expected: `0 tests collected` (no hay ficheros aún), sin errores de importación.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test: integration conftest with respx mock_services fixture"
```

---

## Task 4: test_rest_auth.py

**Files:**
- Create: `tests/integration/test_rest_auth.py`
- Test: `tests/integration/test_rest_auth.py`

- [ ] **Step 1: Escribir los tests**

```python
"""Tests para validación de API key (deps.py → db_client.get_session)."""


def test_missing_api_key_returns_422(client):
    """Sin X-API-Key header → FastAPI devuelve 422 (header requerido ausente)."""
    r = client.get("/api/config")
    assert r.status_code == 422


def test_invalid_api_key_returns_401(client):
    """Key inválida → db devuelve 401 → gateway devuelve 401."""
    r = client.get("/api/config", headers={"x-api-key": "wrong-key"})
    assert r.status_code == 401


def test_valid_api_key_passes(client, auth_headers):
    """Key válida → resolución correcta, request pasa."""
    r = client.get("/api/config", headers=auth_headers)
    assert r.status_code == 200
```

- [ ] **Step 2: Ejecutar y verificar que pasan**

```bash
pytest tests/integration/test_rest_auth.py -v
```

Expected:
```
test_missing_api_key_returns_422 PASSED
test_invalid_api_key_returns_401 PASSED
test_valid_api_key_passes PASSED
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rest_auth.py
git commit -m "test(integration): API key authentication"
```

---

## Task 5: test_rest_health.py

**Files:**
- Create: `tests/integration/test_rest_health.py`

- [ ] **Step 1: Escribir los tests**

```python
"""Tests para GET /api/health."""
import httpx


def test_health_all_ok(client):
    """Todos los servicios responden → todos los campos son 'ok'."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["orchestrator"] == "ok"
    assert body["transcriber"] == "ok"
    assert body["tts"] == "ok"


def test_health_never_returns_5xx(client, mock_services):
    """Aunque el orchestrator esté caído, health devuelve 200."""
    mock_services.get("http://localhost:8000/health").mock(
        side_effect=httpx.ConnectError("down")
    )
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["orchestrator"] == "unavailable"


def test_health_partial_outage_tts(client, mock_services):
    """TTS caído → campo tts es 'unavailable', el resto 'ok'."""
    mock_services.get("http://localhost:8005/health").mock(
        side_effect=httpx.ConnectError("down")
    )
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["tts"] == "unavailable"
    assert body["orchestrator"] == "ok"
    assert body["transcriber"] == "ok"
```

- [ ] **Step 2: Ejecutar**

```bash
pytest tests/integration/test_rest_health.py -v
```

Expected: 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rest_health.py
git commit -m "test(integration): GET /api/health"
```

---

## Task 6: test_rest_config.py

**Files:**
- Create: `tests/integration/test_rest_config.py`

- [ ] **Step 1: Escribir los tests**

```python
"""Tests para GET/PUT/POST /api/config."""
import httpx


def test_get_config_returns_client_config(client, auth_headers):
    """GET /api/config devuelve la config del cliente desde jota-db."""
    r = client.get("/api/config", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["stt_language"] == "es"
    assert body["tts_voice"] == "af_heart"
    assert "barge_in_enabled" in body


def test_put_config_returns_updated_config(client, auth_headers, mock_services):
    """PUT /api/config llama a jota-db y devuelve la config actualizada."""
    mock_services.put("http://localhost:8001/config/me").mock(
        return_value=httpx.Response(200, json={
            "stt_language": "en", "stt_vad_thold": 0.0,
            "tts_voice": "af_heart", "tts_speed": 1.0,
            "preferred_model_id": None, "system_prompt_extra": None,
            "barge_in_enabled": True, "barge_in_min_chars": 5,
            "conversation_memory_limit": 20,
        })
    )
    r = client.put("/api/config", json={"stt_language": "en"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["stt_language"] == "en"


def test_post_config_reset_returns_defaults(client, auth_headers):
    """POST /api/config/reset llama al reset de jota-db y devuelve defaults."""
    r = client.post("/api/config/reset", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["stt_language"] == "es"


def test_put_config_invalid_body_returns_422(client, auth_headers):
    """Body no-JSON en PUT → 422."""
    r = client.put(
        "/api/config",
        content=b"not-json",
        headers={**auth_headers, "content-type": "application/json"},
    )
    assert r.status_code == 422


def test_config_endpoint_without_auth_returns_422(client):
    """Sin X-API-Key → 422."""
    r = client.get("/api/config")
    assert r.status_code == 422
```

- [ ] **Step 2: Ejecutar**

```bash
pytest tests/integration/test_rest_config.py -v
```

Expected: 5 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rest_config.py
git commit -m "test(integration): GET/PUT/POST /api/config"
```

---

## Task 7: test_rest_conversations.py

**Files:**
- Create: `tests/integration/test_rest_conversations.py`

- [ ] **Step 1: Escribir los tests**

```python
"""Tests para endpoints de conversaciones."""
import httpx


def test_get_conversations_returns_list(client, auth_headers):
    r = client.get("/api/conversations", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["id"] == "conv-1"


def test_get_messages_returns_list(client, auth_headers):
    r = client.get("/api/conversations/conv-1/messages", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_delete_conversation_returns_204(client, auth_headers):
    r = client.delete("/api/conversations/conv-1", headers=auth_headers)
    assert r.status_code == 204


def test_get_messages_not_found_propagates_404(client, auth_headers, mock_services):
    """404 de jota-db se propaga como 404 al cliente."""
    mock_services.get(
        url__regex=r"http://localhost:8001/conversations/.+/messages"
    ).mock(return_value=httpx.Response(404, json={"detail": "Not found"}))
    r = client.get("/api/conversations/nonexistent/messages", headers=auth_headers)
    assert r.status_code == 404
```

- [ ] **Step 2: Ejecutar**

```bash
pytest tests/integration/test_rest_conversations.py -v
```

Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rest_conversations.py
git commit -m "test(integration): conversation endpoints"
```

---

## Task 8: test_rest_models.py

**Files:**
- Create: `tests/integration/test_rest_models.py`

- [ ] **Step 1: Escribir los tests**

```python
"""Tests para GET /api/models."""
import httpx


def test_get_models_returns_list(client, auth_headers):
    r = client.get("/api/models", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["id"] == "llama3"


def test_get_models_db_unavailable_returns_503(client, auth_headers, mock_services):
    """Error de conexión a jota-db → 503."""
    mock_services.get("http://localhost:8001/models").mock(
        side_effect=httpx.ConnectError("db down")
    )
    r = client.get("/api/models", headers=auth_headers)
    assert r.status_code == 503
```

- [ ] **Step 2: Ejecutar**

```bash
pytest tests/integration/test_rest_models.py -v
```

Expected: 2 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_rest_models.py
git commit -m "test(integration): GET /api/models"
```

---

## Task 9: test_ws_handshake.py

Tests del handshake WebSocket en `routes.py`. Sin intercambio de mensajes de voz — solo auth y validación del payload inicial.

**Nota:** El bridge no envía mensaje "ready" al cliente tras el handshake (por diseño). Para handshakes inválidos, el servidor cierra el WS, lo que hace que TestClient lance una excepción al intentar comunicarse. Para handshakes válidos, verificamos que la conexión sigue abierta.

**Files:**
- Create: `tests/integration/test_ws_handshake.py`

- [ ] **Step 1: Escribir los tests**

```python
"""Tests para WebSocket handshake (/ws/stream)."""
import pytest


def test_malformed_json_closes_ws(client):
    """JSON malformado como primer mensaje → WS se cierra (código 1008)."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_text("not-json{{")
            ws.receive_text()  # debe lanzar excepción al recibir close frame


def test_invalid_client_key_closes_ws(client, mock_services):
    """Key rechazada por jota-db → WS se cierra."""
    import httpx
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid key"})
    )
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({
                "client_key": "bad-key",
                "input_mode": "text",
                "output_mode": ["text"],
            })
            ws.receive_text()


def test_missing_required_handshake_field_closes_ws(client):
    """Handshake sin campo requerido (input_mode) → WS se cierra."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json({"client_key": "valid-key-abc", "output_mode": ["text"]})
            ws.receive_text()


def test_valid_text_mode_handshake_connection_stays_open(client):
    """Handshake válido con input_mode=text — conexión permanece abierta."""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({
            "client_key": "valid-key-abc",
            "input_mode": "text",
            "output_mode": ["text"],
        })
        # send {"type":"end"} — no-op en modo texto, pero cierra el loop limpiamente
        ws.send_text('{"type":"end"}')
        # Si no lanza excepción, el handshake fue válido y la conexión estuvo abierta
```

- [ ] **Step 2: Ejecutar**

```bash
pytest tests/integration/test_ws_handshake.py -v
```

Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_ws_handshake.py
git commit -m "test(integration): WebSocket handshake auth and validation"
```

---

## Task 10: test_bridge_flow.py — flujo texto

Verifica que el bridge llama al orchestrator con los headers y payload correctos, y que los tokens llegan al cliente.

**Flujo en modo texto:** cliente envía WS text message → `_client_input_loop` → `_call_orchestrator(text)` → `orchestrator.listen_loop` → POST /api/quick (respx) → NDJSON → token enviado al cliente.

**Files:**
- Create: `tests/integration/test_bridge_flow.py`

- [ ] **Step 1: Escribir los tests**

```python
"""Tests para el flujo de texto en JotaBridge.

input_mode=text: sin transcriber. El cliente manda texto plano,
recibe tokens del orchestrator.
"""
import json
import httpx
from tests.integration.conftest import VALID_KEY, CLIENT_UUID, SESSION_RESPONSE

HANDSHAKE_TEXT = {
    "client_key": VALID_KEY,
    "input_mode": "text",
    "output_mode": ["text"],
}


def test_text_message_produces_token(client):
    """Cliente manda texto → recibe token del orchestrator."""
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("hola")
        msg = ws.receive_json()
        assert msg["type"] == "token"
        assert msg["content"] == "Hola"


def test_orchestrator_receives_correct_headers(client, mock_services):
    """El request al orchestrator incluye x-client-key y x-client-id correctos."""
    captured = {}

    def capture(req):
        captured["x-client-key"] = req.headers.get("x-client-key")
        captured["x-client-id"] = req.headers.get("x-client-id")
        return httpx.Response(
            200,
            content=b'{"type":"token","content":"ok"}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    mock_services.post("http://localhost:8000/api/quick").mock(side_effect=capture)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("test")
        ws.receive_json()  # consumir token

    assert captured["x-client-key"] == VALID_KEY
    assert captured["x-client-id"] == CLIENT_UUID


def test_preferred_model_id_included_in_orchestrator_payload(client, mock_services):
    """preferred_model_id de ClientConfig se envía en el body al orchestrator."""
    session = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "preferred_model_id": "llama3-70b"},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    captured_body = {}

    def capture(req):
        captured_body.update(json.loads(req.content))
        return httpx.Response(
            200,
            content=b'{"type":"token","content":"ok"}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    mock_services.post("http://localhost:8000/api/quick").mock(side_effect=capture)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("test")
        ws.receive_json()

    assert captured_body.get("model_id") == "llama3-70b"


def test_system_prompt_extra_included_in_orchestrator_payload(client, mock_services):
    """system_prompt_extra de ClientConfig se envía en el body al orchestrator."""
    session = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "system_prompt_extra": "Habla en inglés"},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    captured_body = {}

    def capture(req):
        captured_body.update(json.loads(req.content))
        return httpx.Response(
            200,
            content=b'{"type":"token","content":"ok"}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    mock_services.post("http://localhost:8000/api/quick").mock(side_effect=capture)

    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(HANDSHAKE_TEXT)
        ws.send_text("test")
        ws.receive_json()

    assert captured_body.get("system_prompt_extra") == "Habla en inglés"
```

- [ ] **Step 2: Ejecutar**

```bash
pytest tests/integration/test_bridge_flow.py -v
```

Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_bridge_flow.py
git commit -m "test(integration): bridge text flow — orchestrator headers and config propagation"
```

---

## Task 11: test_bridge_audio_flow.py — flujo audio

Verifica el flujo audio: PCM → transcriber (fake WS server) → is_final → orchestrator → token al cliente. También verifica que `stt_language` y `tts_voice`/`tts_speed` de la config llegan a los clientes correctos.

**Fake transcriber:** servidor WS real en hilo de background (puerto 19009). Se parchea `settings.TRANSCRIBER_WS_URL` para que el bridge apunte ahí.

**Files:**
- Create: `tests/integration/test_bridge_audio_flow.py`

- [ ] **Step 1: Escribir el fichero completo**

```python
"""
Tests para JotaBridge en modo audio.

input_mode=audio: el bridge conecta al transcriber WS, manda PCM,
recibe transcripción is_final, llama al orchestrator, devuelve tokens al cliente.
"""
import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import websockets

from src.main import app
from src.core.config import settings
from starlette.testclient import TestClient
from tests.integration.conftest import VALID_KEY, SESSION_RESPONSE

HANDSHAKE_AUDIO = {
    "client_key": VALID_KEY,
    "input_mode": "audio",
    "output_mode": ["text"],
}

# ---------------------------------------------------------------------------
# Fake transcriber WS server
# ---------------------------------------------------------------------------

_FAKE_TRANSCRIBER_PORT = 19009
_fake_transcriber_started = False


def _start_fake_transcriber():
    """Arranca fake transcriber en puerto 19009 en un hilo daemon."""
    global _fake_transcriber_started
    if _fake_transcriber_started:
        return
    _fake_transcriber_started = True

    async def handler(ws):
        # Handshake: recibe config, responde ready
        raw = await ws.recv()
        msg = json.loads(raw)
        assert msg["type"] == "config"
        await ws.send(json.dumps({
            "type": "ready",
            "protocol_version": 1,
            "session_id": "test-audio-session",
        }))
        # Espera audio, responde con transcripción final
        async for chunk in ws:
            if isinstance(chunk, bytes) and len(chunk) > 0:
                await ws.send(json.dumps({
                    "type": "transcription",
                    "text": "hola desde audio",
                    "is_final": True,
                }))
                break

    loop = asyncio.new_event_loop()

    async def run():
        async with websockets.serve(handler, "localhost", _FAKE_TRANSCRIBER_PORT):
            await asyncio.Future()

    thread = threading.Thread(
        target=lambda: loop.run_until_complete(run()),
        daemon=True,
    )
    thread.start()
    time.sleep(0.15)  # esperar a que el servidor arranque


@pytest.fixture(scope="module", autouse=True)
def start_fake_transcriber():
    """Arranca el fake transcriber una sola vez por módulo."""
    _start_fake_transcriber()
    old_url = settings.TRANSCRIBER_WS_URL
    settings.TRANSCRIBER_WS_URL = f"localhost:{_FAKE_TRANSCRIBER_PORT}"
    yield
    settings.TRANSCRIBER_WS_URL = old_url


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_audio_chunk_transcribed_and_forwarded_to_orchestrator(mock_services):
    """PCM → transcriber fake emite is_final → orchestrator llamado con el texto."""
    called_with_text = {}

    def capture(req):
        called_with_text["text"] = json.loads(req.content).get("text")
        return httpx.Response(
            200,
            content=b'{"type":"token","content":"ok"}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    mock_services.post("http://localhost:8000/api/quick").mock(side_effect=capture)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            ws.send_json(HANDSHAKE_AUDIO)
            ws.send_bytes(b"\x00" * 512)  # PCM fake
            # Esperar token (pueden llegar mensajes intermedios)
            for _ in range(10):
                msg = ws.receive_json()
                if msg.get("type") == "token":
                    break
            assert msg["type"] == "token"

    assert called_with_text.get("text") == "hola desde audio"


def test_transcriber_connect_uses_stt_language_from_config(mock_services):
    """stt_language de ClientConfig se pasa a TranscriberClient.connect()."""
    session_fr = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "stt_language": "fr"},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session_fr)
    )

    connect_calls = []
    original_connect = __import__(
        "src.services.transcriber_client", fromlist=["TranscriberClient"]
    ).TranscriberClient.connect

    async def spy_connect(self, language="es", token="", vad_thold=0.0):
        connect_calls.append({"language": language, "token": token})
        await original_connect(self, language=language, token=token, vad_thold=vad_thold)

    with patch("src.services.transcriber_client.TranscriberClient.connect", spy_connect):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/stream") as ws:
                ws.send_json(HANDSHAKE_AUDIO)
                ws.send_bytes(b"\x00" * 512)
                try:
                    ws.receive_json()
                except Exception:
                    pass

    assert connect_calls, "TranscriberClient.connect nunca fue llamado"
    assert connect_calls[0]["language"] == "fr"


def test_tts_connect_uses_voice_and_speed_from_config(mock_services):
    """tts_voice y tts_speed de ClientConfig se pasan a TTSClient.connect()."""
    session = {
        **SESSION_RESPONSE,
        "config": {**SESSION_RESPONSE["config"], "tts_voice": "bf_emma", "tts_speed": 1.2},
    }
    mock_services.get("http://localhost:8001/auth/session").mock(
        return_value=httpx.Response(200, json=session)
    )

    connect_calls = []

    # TTSClient.connect es async — necesitamos un spy async
    original_tts_connect = __import__(
        "src.services.tts_client", fromlist=["TTSClient"]
    ).TTSClient.connect

    async def spy_tts_connect(self, voice=None, speed=None):
        connect_calls.append({"voice": voice, "speed": speed})
        # No llamamos al original (evitamos conexión WS real a TTS)
        self.ws = AsyncMock()
        self.ws.recv = AsyncMock(return_value=json.dumps({"type": "auth_ok"}))

    with patch("src.services.tts_client.TTSClient.connect", spy_tts_connect):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/stream") as ws:
                # output_mode=["audio"] para que el bridge instancie TTSClient
                ws.send_json({
                    "client_key": VALID_KEY,
                    "input_mode": "text",
                    "output_mode": ["audio", "text"],
                })
                ws.send_text("test")
                try:
                    ws.receive_json()
                except Exception:
                    pass

    assert connect_calls, "TTSClient.connect nunca fue llamado"
    assert connect_calls[0]["voice"] == "bf_emma"
    assert connect_calls[0]["speed"] == 1.2
```

- [ ] **Step 2: Ejecutar**

```bash
pytest tests/integration/test_bridge_audio_flow.py -v
```

Expected: 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_bridge_audio_flow.py
git commit -m "test(integration): bridge audio flow — transcriber, config propagation to TTS"
```

---

## Task 12: GitHub Actions CI

**Files:**
- Create: `.github/workflows/test.yml`

- [ ] **Step 1: Crear el workflow**

```yaml
name: Tests

on:
  push:
    branches: ["**"]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Lint (ruff)
        run: ruff check src/ tests/

      - name: Unit tests
        run: pytest tests/unit/ -v

      - name: Integration tests
        run: pytest tests/integration/ -v
```

- [ ] **Step 2: Verificar que ruff está disponible**

```bash
pip show ruff 2>/dev/null || pip install ruff
ruff check src/ tests/ 2>&1 | head -20
```

Si hay errores de lint en los tests nuevos, corregirlos antes de continuar.

- [ ] **Step 3: Ejecutar la suite completa local para verificar**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected:
```
tests/unit/test_bridge_barge_in.py ...
tests/unit/test_bridge_disconnect.py ...
tests/unit/test_bridge_send_guards.py ...
tests/unit/test_transcriber_loop.py ...
tests/integration/test_rest_auth.py ...
tests/integration/test_rest_health.py ...
tests/integration/test_rest_config.py ...
tests/integration/test_rest_conversations.py ...
tests/integration/test_rest_models.py ...
tests/integration/test_ws_handshake.py ...
tests/integration/test_bridge_flow.py ...
tests/integration/test_bridge_audio_flow.py ...
X passed in Xs
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add GitHub Actions workflow — lint + unit + integration tests"
```

---

## Resumen

| Categoría | Tests | Qué cubre |
|---|---|---|
| Unit | ~12 | Lógica de barge-in, guards de send, disconnect, callbacks del transcriber |
| Integration REST | ~18 | Auth, health, config, conversations, models |
| Integration WS | ~7 | Handshake, flujo texto, flujo audio, propagación de config |
| **Total** | **~37** | **Sin servicios externos. CI < 15s** |

**Trabajo futuro:** Crear issue "Crear suite E2E con Docker Compose" — tests marcados `@pytest.mark.e2e` que corren con servicios reales levantados.
