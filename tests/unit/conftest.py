from unittest.mock import AsyncMock, MagicMock

import pytest

from jota_gateway.services.pipeline_tracker import PipelineTracker
from tests.fakes import bridge as fake_bridge
from tests.fakes.bridge import DirectSender


@pytest.fixture
def mock_tracker():
    ws = AsyncMock()
    registry = MagicMock()
    return PipelineTracker(
        session_id="test:unit",
        client_id="test",
        input_mode="text",
        output_mode=["text"],
        sender=DirectSender(ws),
        registry=registry,
    )


@pytest.fixture
async def make_bridge():
    """`make_bridge(**overrides) -> (bridge, ws)`, see `tests/fakes/bridge.py`.

    Closes every bridge's sender at teardown, so no writer task outlives the test.
    """
    built = []

    def _make(*args, **kwargs):
        bridge, ws = fake_bridge.make_bridge(*args, **kwargs)
        built.append(bridge)
        return bridge, ws

    yield _make
    for bridge in built:
        await bridge._sender.aclose(1.0)


@pytest.fixture()
def use_api(monkeypatch):
    """Sustituye el cliente HTTP de la CLI por un FakeApi (tests/fakes/cli_api.py)."""
    from jota_gateway.cli import api

    def _use(fake):
        monkeypatch.setattr(api, "from_context", lambda ctx: fake)
        return fake

    return _use
