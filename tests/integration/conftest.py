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
from src.services.db_client import db_client

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
# Cache cleanup (autouse — moved here from global conftest so it only runs
# for integration tests, not unit tests)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_db_cache():
    """Limpia caché del db_client entre tests para evitar state leakage."""
    db_client._session_cache.clear()
    db_client._models_cache.clear()
    yield
    db_client._session_cache.clear()
    db_client._models_cache.clear()

# ---------------------------------------------------------------------------
# respx: intercepta tráfico HTTP hacia jota-db y orchestrator
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_services():
    """
    Activa respx e intercepta todas las rutas HTTP que el gateway llama.
    Los tests individuales pueden sobreescribir rutas concretas.
    assert_all_mocked=False permite que routes no usadas no rompan el test.
    assert_all_called=False permite que routes registradas pero no usadas no rompan el test.
    """
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
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
