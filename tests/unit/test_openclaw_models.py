from src.services.openclaw.models import GatewayInfo, ToolCallEvent

# Real shape since server 2026.6.11: the roster is NOT in hello-ok, only the default agent.
HELLO_OK_PAYLOAD = {
    "type": "hello-ok",
    "protocol": 4,
    "server": {"version": "2026.6.11", "connId": "abc-123"},
    "policy": {"tickIntervalMs": 30000, "maxPayload": 26214400, "maxBufferedBytes": 52428800},
    "snapshot": {"sessionDefaults": {"defaultAgentId": "assistant"}},
    "auth": {"role": "operator", "scopes": ["operator.read", "operator.write"]},
}


def test_from_hello_ok_basic():
    info = GatewayInfo.from_hello_ok(HELLO_OK_PAYLOAD)
    assert info.tick_interval_ms == 30000
    assert info.max_payload == 26214400
    assert info.default_agent_id == "assistant"
    assert info.agents == []


def test_from_hello_ok_minimal():
    """Handles missing optional fields gracefully."""
    info = GatewayInfo.from_hello_ok({})
    assert info.default_agent_id == "main"
    assert info.agents == []
    assert info.tick_interval_ms == 15000


def test_update_agents_from_list():
    """agents.list uses 'id' and a top-level 'defaultId'; the order is kept."""
    info = GatewayInfo.from_hello_ok({})  # starts with empty agents, default "main"
    info.update_agents_from_list(
        {
            "defaultId": "assistant",
            "agents": [
                {"id": "main", "name": "Main Agent"},
                {"id": "assistant", "name": "Jota Voice"},
            ],
        }
    )
    assert info.agents == ["main", "assistant"]
    assert info.has_agent("main")
    assert info.has_agent("assistant")
    assert not info.has_agent("nonexistent")
    assert info.default_agent_id == "assistant"


def test_update_agents_from_list_keeps_prior_default_if_missing():
    info = GatewayInfo.from_hello_ok({})
    info.update_agents_from_list({"agents": [{"id": "main", "name": "Main Agent"}]})
    assert info.default_agent_id == "main"


def test_tool_call_start_parses_name_and_args():
    data = {
        "phase": "start",
        "name": "exec",
        "toolCallId": "call-1",
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
        "phase": "result",
        "name": "exec",
        "toolCallId": "call-1",
        "isError": False,
        "result": {"content": [{"type": "text", "text": "line1\nline2"}]},
    }
    tc = ToolCallEvent.from_session_tool_payload(data)
    assert tc.phase == "result"
    assert tc.result == "line1\nline2"
    assert tc.is_error is False
    assert tc.args is None


def test_tool_call_result_with_no_content_has_none_result():
    data = {
        "phase": "result",
        "name": "exec",
        "toolCallId": "call-1",
        "isError": True,
        "result": {},
    }
    tc = ToolCallEvent.from_session_tool_payload(data)
    assert tc.result is None
    assert tc.is_error is True


def test_tool_call_update_phase_returns_none():
    data = {"phase": "update", "name": "exec", "toolCallId": "call-1", "partialResult": {}}
    assert ToolCallEvent.from_session_tool_payload(data) is None


def test_tool_call_missing_required_fields_returns_none():
    assert ToolCallEvent.from_session_tool_payload({"phase": "start"}) is None
    assert ToolCallEvent.from_session_tool_payload({"phase": "start", "name": "exec"}) is None
