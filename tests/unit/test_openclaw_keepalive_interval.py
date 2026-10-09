"""_keepalive_interval() must be clamped (issue #135): tickIntervalMs=0 (or tiny)
used to make the keepalive loop spin with asyncio.sleep(0) — a ping flood."""

import pytest

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.models import GatewayInfo
from src.services.openclaw.registry import ClientRegistry, TurnRegistry


def _client(tick_ms=None):
    turns = TurnRegistry()
    client = OpenClawClient("h", 1, "t", turns, FrameDispatcher(turns, ClientRegistry()))
    if tick_ms is not None:
        client.gateway_info = GatewayInfo(
            protocol_version=4,
            server_version="x",
            conn_id="c",
            default_agent_id="main",
            agents={},
            tick_interval_ms=tick_ms,
            max_payload=1,
        )
    return client


def test_normal_tick_is_80_percent():
    assert _client(30000)._keepalive_interval() == pytest.approx(24.0)


def test_no_gateway_info_uses_default():
    assert _client()._keepalive_interval() == pytest.approx(12.0)


@pytest.mark.parametrize("tick_ms", [0, -5, 1, 100])
def test_degenerate_tick_is_clamped_to_a_safe_minimum(tick_ms):
    assert _client(tick_ms)._keepalive_interval() >= 1.0
