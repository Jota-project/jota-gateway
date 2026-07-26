"""
Fixtures de integración.

BD: SQLite en memoria inyectada por monkeypatch (reemplaza respx/jota-db).
WebSocket: fake servers TTS/transcriber en hilos de background (sin cambios).
Orchestrator: MockOrchestrator inyectado via monkeypatch (sin cambios).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from src.core.config import settings
from src.db.models import ClientRecord
from src.main import app
from src.services.db_client import db_client
from src.services.openclaw.models import AgentInfo, GatewayInfo
from src.services.openclaw.registry import ClientRegistry, TurnRegistry
from src.services.protocol import OrchestratorEvent, OrchestratorProtocol
from src.services.reconnection import ConnectionState, ServiceStatus

# ---------------------------------------------------------------------------
# Datos de test estándar
# ---------------------------------------------------------------------------

VALID_KEY = "valid-key-abc"
CLIENT_ID = "hab_sito"
CLIENT_NAME = "Test Client"
ADMIN_TOKEN = "test-admin-token"

# ---------------------------------------------------------------------------
# BD SQLite en memoria
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def db_engine(monkeypatch):
    """SQLite in-memory inyectado en get_engine() para todos los integration tests."""
    from src import db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db.database, "_engine", engine)
    return engine


@pytest.fixture()
def seed_client(db_engine):
    """Inserta el cliente de prueba estándar en la BD."""
    with Session(db_engine) as s:
        s.add(
            ClientRecord(
                id=CLIENT_ID,
                name=CLIENT_NAME,
                client_key=VALID_KEY,
                is_active=True,
            )
        )
        s.commit()


@pytest.fixture(autouse=True)
def clear_db_cache():
    """Limpia el caché y el contador de generaciones de db_client antes y después de cada test."""
    db_client._session_cache.clear()
    db_client._generations.clear()
    yield
    db_client._session_cache.clear()
    db_client._generations.clear()


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
        agents={
            "main": AgentInfo(agent_id="main", name="Main", is_default=True),
            "a": AgentInfo(agent_id="a", name="Agent A", is_default=False),
            "openclaw": AgentInfo(agent_id="openclaw", name="OpenClaw", is_default=False),
        },
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
    mock.trigger_reconnect = MagicMock(return_value="mock-job-id")
    mock.stream_response = _stream
    mock.gateway_info = _make_default_gateway_info()
    mock._name = "openclaw"
    mock.get_name = MagicMock(return_value="openclaw")
    mock.status = MagicMock(
        return_value=ServiceStatus(
            name="openclaw",
            state=ConnectionState.CONNECTED,
            connected_at=datetime(2026, 6, 4, 10, 0, tzinfo=UTC),
            reconnect_attempts=0,
            last_error=None,
        )
    )
    return mock


def make_mock_registry(orchestrator=None):
    """Backwards-compat helper: returns the orchestrator directly."""
    if orchestrator is None:
        orchestrator = make_mock_orchestrator()
    return orchestrator


# ---------------------------------------------------------------------------
# Health-check HTTP mocks (transcriber + TTS — sin jota-db)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_services():
    """Mockea los readiness checks HTTP de transcriber y TTS (/ready, no /health — ver issue #101)."""
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        router.get(f"http://{settings.TRANSCRIBER_WS_URL}/ready").mock(
            return_value=httpx.Response(200)
        )
        router.get("http://localhost:8005/ready").mock(return_value=httpx.Response(200))
        yield router


@pytest.fixture
def mock_orchestrator():
    return make_mock_orchestrator()


@pytest.fixture
def mock_registry(mock_orchestrator):
    return make_mock_registry(mock_orchestrator)


def _configure_app_mocks(mock_registry, monkeypatch):
    monkeypatch.setattr("src.main.ReconnectingOpenClawClient", lambda *a, **kw: mock_registry)
    monkeypatch.setattr("src.main.OpenClawClient", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.main.FrameDispatcher", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("src.main.TurnRegistry", lambda: TurnRegistry())
    monkeypatch.setattr("src.main.ClientRegistry", lambda: ClientRegistry())
    mock_registry.connect = AsyncMock()
    mock_registry.close = AsyncMock()


@pytest.fixture
def client(mock_services, mock_registry, seed_client, monkeypatch):
    """TestClient con SQLite en memoria y orchestrator mock inyectado.

    client=("127.0.0.1", 50000): peer loopback, para que /v1/* (ver
    src/core/network.py) se comporte igual que en el despliegue doméstico
    real sin exigir Authorization en cada test existente.
    """
    _configure_app_mocks(mock_registry, monkeypatch)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


@pytest.fixture
def client_untrusted(mock_services, mock_registry, seed_client, monkeypatch):
    """TestClient cuyo peer TCP no es loopback ni está en TRUSTED_NETWORKS
    (203.0.113.5 es TEST-NET-3, RFC 5737 — rango reservado para documentación)."""
    _configure_app_mocks(mock_registry, monkeypatch)
    with TestClient(app, client=("203.0.113.5", 54321)) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"x-api-key": VALID_KEY}


@pytest.fixture
def ha_bearer_headers():
    return {"authorization": f"Bearer {VALID_KEY}"}
