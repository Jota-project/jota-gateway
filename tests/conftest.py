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
