"""Global test fixtures shared across unit and integration tests."""

import pytest
from starlette.testclient import TestClient

from src.main import app


@pytest.fixture
def test_client():
    """FastAPI TestClient con lifespan (conecta/cierra db_client)."""
    with TestClient(app) as client:
        yield client
