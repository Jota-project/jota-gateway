from src.services.openclaw.models import GatewayInfo, ToolCallEvent

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


def test_tool_call_start_parses_name_and_args():
    data = {
        "phase": "start", "name": "exec", "toolCallId": "call-1",
        "args": {"command": "pwd && ls", "workdir": "/tmp"},
    }
    tc = ToolCallEvent.from_session_tool_payload(data)
    assert tc.phase == "start"
    assert tc.name == "exec"
    assert tc.tool_call_id == "call-1"
    assert tc.args == {"command": "pwd && ls", "workdir": "/tmp"}
    assert tc.result is None
    assert tc.is_error is None


def test_tool_call_result_flattens_text_content():
    data = {
        "phase": "result", "name": "exec", "toolCallId": "call-1",
        "isError": False,
        "result": {"content": [{"type": "text", "text": "line1\nline2"}]},
    }
    tc = ToolCallEvent.from_session_tool_payload(data)
    assert tc.phase == "result"
    assert tc.result == "line1\nline2"
    assert tc.is_error is False
    assert tc.args is None


def test_tool_call_result_with_no_content_has_none_result():
    data = {"phase": "result", "name": "exec", "toolCallId": "call-1", "isError": True, "result": {}}
    tc = ToolCallEvent.from_session_tool_payload(data)
    assert tc.result is None
    assert tc.is_error is True


def test_tool_call_update_phase_returns_none():
    data = {"phase": "update", "name": "exec", "toolCallId": "call-1", "partialResult": {}}
    assert ToolCallEvent.from_session_tool_payload(data) is None


def test_tool_call_missing_required_fields_returns_none():
    assert ToolCallEvent.from_session_tool_payload({"phase": "start"}) is None
    assert ToolCallEvent.from_session_tool_payload({"phase": "start", "name": "exec"}) is None
