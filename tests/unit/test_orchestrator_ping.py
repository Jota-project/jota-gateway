"""Tests for OrchestratorClient.ping()."""
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from src.services.orchestrator_client import OrchestratorClient


@pytest.fixture
def client():
    c = OrchestratorClient(
        base_url="http://localhost:8000",
        api_key="test-key",
        client_id="test",
    )
    c._http = AsyncMock(spec=httpx.AsyncClient)
    return c


async def test_ping_returns_true_on_200(client):
    response = MagicMock()
    response.is_success = True
    client._http.get = AsyncMock(return_value=response)

    result = await client.ping()

    assert result is True
    client._http.get.assert_called_once_with(
        "http://localhost:8000/health", timeout=5.0
    )


async def test_ping_returns_false_on_503(client):
    response = MagicMock()
    response.is_success = False
    client._http.get = AsyncMock(return_value=response)

    result = await client.ping()

    assert result is False


async def test_ping_returns_false_on_network_error(client):
    client._http.get = AsyncMock(
        side_effect=httpx.ConnectError("connection refused")
    )

    result = await client.ping()

    assert result is False
