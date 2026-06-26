# tests/integration/test_orchestrator_registry.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.orchestrators.registry import OrchestratorRegistry
from src.services.orchestrators.protocol import OrchestratorProtocol


def make_mock_client(name: str):
    client = MagicMock(spec=OrchestratorProtocol)
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    return client


@pytest.mark.asyncio
async def test_get_returns_registered_client():
    mock = make_mock_client("openclaw")
    registry = OrchestratorRegistry({"openclaw": mock})
    assert registry.get("openclaw") is mock


@pytest.mark.asyncio
async def test_get_raises_for_unknown():
    registry = OrchestratorRegistry({})
    with pytest.raises(KeyError, match="not registered"):
        registry.get("unknown")


@pytest.mark.asyncio
async def test_connect_all_calls_connect_on_each():
    a = make_mock_client("a")
    b = make_mock_client("b")
    registry = OrchestratorRegistry({"a": a, "b": b})
    await registry.connect_all()
    a.connect.assert_awaited_once()
    b.connect.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_all_calls_close_on_each():
    a = make_mock_client("a")
    b = make_mock_client("b")
    registry = OrchestratorRegistry({"a": a, "b": b})
    await registry.close_all()
    a.close.assert_awaited_once()
    b.close.assert_awaited_once()
