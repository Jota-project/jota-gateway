"""Global test fixtures shared across unit and integration tests."""

import pytest
from starlette.testclient import TestClient

from src.main import app


@pytest.fixture(autouse=True)
def _disable_single_process_lock(monkeypatch):
    """Los tests crean varias apps en un mismo proceso y flock entra en conflicto con otro
    open() del mismo fichero aunque sea del mismo proceso (M6f, #218). Además evita crear
    data/gateway.lock en el checkout. Los tests del guard lo reactivan explícitamente."""
    from src.core.config import settings

    monkeypatch.setattr(settings, "SINGLE_PROCESS_LOCK", False)


@pytest.fixture
def test_client():
    """FastAPI TestClient con lifespan (conecta/cierra db_client)."""
    with TestClient(app) as client:
        yield client
