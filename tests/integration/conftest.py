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

from src.core.config import settings
from src.main import app
from src.services.db_client import db_client
from src.services.protocol import OrchestratorProtocol, OrchestratorEvent
from src.services.openclaw.reconnecting import OrchestratorState, OrchestratorStatus
from src.services.openclaw.registry import TurnRegistry, ClientRegistry
from src.services.openclaw.models import GatewayInfo, AgentInfo

_DB_BASE = f"http://{settings.JOTA_DB_BASE_URL}"
DB_BASE = _DB_BASE  # public alias for use in individual test files

# ---------------------------------------------------------------------------
# Datos de test estándar
# ---------------------------------------------------------------------------

VALID_KEY = "valid-key-abc"
CLIENT_ID = "hab_sito"
ADMIN_TOKEN = "test-admin-token"

SESSION_RESPONSE = {
    "client": {"id": CLIENT_ID, "client_key": VALID_KEY, "is_active": True, "name": CLIENT_ID},
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


@pytest.fixture(autouse=True)
def configure_admin_token():
    """Set ADMIN_TOKEN for all integration tests."""
    original = settings.ADMIN_TOKEN
    settings.ADMIN_TOKEN = ADMIN_TOKEN
    yield
    settings.ADMIN_TOKEN = original


@pytest.fixture
def admin_headers():
    return {"x-admin-token": ADMIN_TOKEN}

# ---------------------------------------------------------------------------
# Mock Orchestrator
# ---------------------------------------------------------------------------

def _make_default_gateway_info() -> GatewayInfo:
    return GatewayInfo(
        protocol_version=4,
        server_version="test",
        conn_id="test-conn",
        default_agent_id="main",
        agents={"main": AgentInfo(agent_id="main", name="Main", is_default=True)},
        tick_interval_ms=15000,
        max_payload=26214400,
    )


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
    mock.gateway_info = _make_default_gateway_info()
    mock._name = "openclaw"
    mock.get_name = MagicMock(return_value="openclaw")
    mock.status = MagicMock(return_value=OrchestratorStatus(
        name="openclaw",
        state=OrchestratorState.CONNECTED,
        connected_at=datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc),
        reconnect_attempts=0,
        last_error=None,
    ))
    return mock


def make_mock_registry(orchestrator=None):
    """Backwards-compat helper: returns the orchestrator directly (no registry wrapper)."""
    if orchestrator is None:
        orchestrator = make_mock_orchestrator()
    return orchestrator

# ---------------------------------------------------------------------------
# respx: intercepta tráfico HTTP hacia jota-db
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_services():
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        # --- jota-db: auth ---
        router.get(f"{_DB_BASE}/auth/session").mock(
            side_effect=lambda req: (
                httpx.Response(200, json=SESSION_RESPONSE)
                if req.headers.get("x-api-key") == VALID_KEY
                else httpx.Response(401, json={"detail": "Invalid key"})
            )
        )
        # --- transcriber: health ---
        router.get(f"http://{settings.TRANSCRIBER_WS_URL}/health").mock(
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
    def _mock_lifespan_openclaw(app_instance):
        app_instance.state.openclaw = mock_registry
        app_instance.state.turn_registry = TurnRegistry()
        app_instance.state.client_registry = ClientRegistry()

    monkeypatch.setattr("src.main.ReconnectingOpenClawClient", lambda *a, **kw: mock_registry)
    monkeypatch.setattr("src.main.OpenClawClient", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.main.FrameDispatcher", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.main.TurnRegistry", lambda: TurnRegistry())
    monkeypatch.setattr("src.main.ClientRegistry", lambda: ClientRegistry())
    # Prevent the actual connect() call
    mock_registry.connect = AsyncMock()
    mock_registry.close = AsyncMock()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"x-api-key": VALID_KEY}
