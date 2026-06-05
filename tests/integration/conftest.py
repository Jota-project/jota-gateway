"""
Fixtures de integración.

HTTP (jota-db) interceptado por respx.
WebSocket (transcriber, TTS) con fake servers en hilos de background.
Orchestrator inyectado via MockOrchestrator (OrchestratorProtocol).
"""
import pytest
import httpx
import respx
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from starlette.testclient import TestClient

from src.main import app
from src.services.db_client import db_client
from src.services.orchestrators.protocol import OrchestratorProtocol, OrchestratorEvent
from src.services.orchestrators.reconnecting import OrchestratorState, OrchestratorStatus
from src.services.orchestrators.registry import OrchestratorRegistry

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

# ---------------------------------------------------------------------------
# Cache cleanup
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_db_cache():
    db_client._session_cache.clear()
    db_client._models_cache.clear()
    yield
    db_client._session_cache.clear()
    db_client._models_cache.clear()

# ---------------------------------------------------------------------------
# Mock Orchestrator
# ---------------------------------------------------------------------------

def make_mock_orchestrator(tokens: list[str] = None) -> OrchestratorProtocol:
    """Creates a mock orchestrator that yields the given tokens then status:done."""
    if tokens is None:
        tokens = ["Hola"]

    async def _stream(*args, **kwargs):
        for t in tokens:
            yield OrchestratorEvent(type="token", content=t)
        yield OrchestratorEvent(type="status", content="done")

    mock = MagicMock(spec=OrchestratorProtocol)
    mock.connect = AsyncMock()
    mock.close = AsyncMock()
    mock.ping = AsyncMock(return_value=True)
    mock.stream_response = _stream
    return mock


def make_mock_registry(orchestrator=None) -> OrchestratorRegistry:
    if orchestrator is None:
        orchestrator = make_mock_orchestrator()
    registry = MagicMock(spec=OrchestratorRegistry)
    registry.connect_all = AsyncMock()
    registry.close_all = AsyncMock()
    registry.default = MagicMock(return_value=orchestrator)
    registry.get = MagicMock(return_value=orchestrator)

    _known = {
        "openclaw": OrchestratorStatus(
            name="openclaw",
            state=OrchestratorState.CONNECTED,
            connected_at=datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc),
            disconnected_at=None,
            reconnect_attempts=0,
            last_error=None,
        )
    }

    def _get_status(name: str) -> OrchestratorStatus:
        if name not in _known:
            raise KeyError(f"Orchestrator '{name}' not registered.")
        return _known[name]

    async def _reconnect(name: str) -> None:
        if name not in _known:
            raise KeyError(f"Orchestrator '{name}' not registered.")

    registry.get_status = MagicMock(side_effect=_get_status)
    registry.reconnect = AsyncMock(side_effect=_reconnect)
    return registry

# ---------------------------------------------------------------------------
# respx: intercepta tráfico HTTP hacia jota-db
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_services():
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
def mock_orchestrator():
    return make_mock_orchestrator()


@pytest.fixture
def mock_registry(mock_orchestrator):
    return make_mock_registry(mock_orchestrator)


@pytest.fixture
def client(mock_services, mock_registry, monkeypatch):
    """TestClient con jota-db mockeado y orchestrator mock inyectado."""
    monkeypatch.setattr("src.main.build_registry", lambda: mock_registry)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"x-api-key": VALID_KEY}
