from src.services.openclaw.models import GatewayInfo

HELLO_OK_PAYLOAD = {
    "type": "hello-ok",
    "protocol": 4,
    "server": {"version": "2026.6.6", "connId": "abc-123"},
    "policy": {"tickIntervalMs": 30000, "maxPayload": 26214400, "maxBufferedBytes": 52428800},
    "snapshot": {
        "defaultAgentId": "main",
        "agents": [
            {"agentId": "main", "name": "Main Agent", "isDefault": True, "heartbeat": {}},
            {"agentId": "assistant", "name": "Jota Voice", "isDefault": False, "heartbeat": {}},
        ],
        "sessionDefaults": {"defaultAgentId": "main"},
    },
    "auth": {"role": "operator", "scopes": ["operator.read", "operator.write"]},
}


def test_from_hello_ok_basic():
    info = GatewayInfo.from_hello_ok(HELLO_OK_PAYLOAD)
    assert info.protocol_version == 4
    assert info.server_version == "2026.6.6"
    assert info.conn_id == "abc-123"
    assert info.tick_interval_ms == 30000
    assert info.max_payload == 26214400
    assert info.default_agent_id == "main"


def test_from_hello_ok_agents():
    info = GatewayInfo.from_hello_ok(HELLO_OK_PAYLOAD)
    assert "main" in info.agents
    assert "assistant" in info.agents
    assert info.agents["main"].is_default is True
    assert info.agents["main"].name == "Main Agent"
    assert info.agents["assistant"].is_default is False


def test_has_agent():
    info = GatewayInfo.from_hello_ok(HELLO_OK_PAYLOAD)
    assert info.has_agent("main") is True
    assert info.has_agent("assistant") is True
    assert info.has_agent("nonexistent") is False


def test_from_hello_ok_minimal():
    """Handles missing optional fields gracefully."""
    info = GatewayInfo.from_hello_ok({})
    assert info.default_agent_id == "main"
    assert info.agents == {}
    assert info.tick_interval_ms == 15000


def test_update_agents_from_list():
    """agents.list payload shape differs from snapshot.agents: 'id' not
    'agentId', no per-agent 'isDefault' — derived from top-level 'defaultId'."""
    info = GatewayInfo.from_hello_ok({})  # starts with empty agents, default "main"
    info.update_agents_from_list({
        "defaultId": "assistant",
        "agents": [
            {"id": "main", "name": "Main Agent"},
            {"id": "assistant", "name": "Jota Voice"},
        ],
    })
    assert info.has_agent("main")
    assert info.has_agent("assistant")
    assert info.agents["assistant"].is_default is True
    assert info.agents["main"].is_default is False
    assert info.default_agent_id == "assistant"


def test_update_agents_from_list_keeps_prior_default_if_missing():
    info = GatewayInfo.from_hello_ok({})
    info.update_agents_from_list({"agents": [{"id": "main", "name": "Main Agent"}]})
    assert info.default_agent_id == "main"
    assert info.agents["main"].is_default is True
