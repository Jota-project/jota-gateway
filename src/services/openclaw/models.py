from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class AgentInfo:
    agent_id: str
    name: str
    is_default: bool


@dataclass
class GatewayInfo:
    protocol_version: int
    server_version: str
    conn_id: str
    default_agent_id: str
    agents: dict[str, AgentInfo]
    tick_interval_ms: int
    max_payload: int

    def has_agent(self, agent_id: str) -> bool:
        return agent_id in self.agents

    @classmethod
    def from_hello_ok(cls, payload: dict) -> "GatewayInfo":
        server = payload.get("server", {})
        policy = payload.get("policy", {})
        snapshot = payload.get("snapshot", {})

        agents: dict[str, AgentInfo] = {}
        for a in snapshot.get("agents", []):
            aid = a["agentId"]
            agents[aid] = AgentInfo(
                agent_id=aid,
                name=a.get("name", aid),
                is_default=a.get("isDefault", False),
            )

        default_agent_id = (
            snapshot.get("defaultAgentId")
            or snapshot.get("sessionDefaults", {}).get("defaultAgentId")
            or "main"
        )

        return cls(
            protocol_version=payload.get("protocol", 4),
            server_version=server.get("version", ""),
            conn_id=server.get("connId", ""),
            default_agent_id=default_agent_id,
            agents=agents,
            tick_interval_ms=policy.get("tickIntervalMs", 15000),
            max_payload=policy.get("maxPayload", 26214400),
        )


def _flatten_tool_result_content(content: Optional[list]) -> Optional[str]:
    """Join result.content[].text blocks from a session.tool 'result' payload.

    OpenClaw tool results carry a list of typed content blocks; jota-gateway
    only cares about the text ones. Returns None if there's nothing to show.
    """
    if not content:
        return None
    texts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    joined = "\n".join(t for t in texts if t)
    return joined or None


@dataclass
class ToolCallEvent:
    phase: Literal["start", "result"]
    name: str
    tool_call_id: str
    args: Optional[dict] = None
    result: Optional[str] = None
    is_error: Optional[bool] = None

    @classmethod
    def from_session_tool_payload(cls, data: dict) -> Optional["ToolCallEvent"]:
        """Parse the `data` sub-object of an OpenClaw `session.tool` event.

        Only `start` and `result` phases are recognized — `update` (streaming
        partial result) and anything else return None. Returns None on
        missing required fields (name, toolCallId) instead of raising.
        """
        phase = data.get("phase")
        name = data.get("name")
        tool_call_id = data.get("toolCallId")
        if phase not in ("start", "result") or not name or not tool_call_id:
            return None

        if phase == "result":
            result_payload = data.get("result") or {}
            return cls(
                phase="result",
                name=name,
                tool_call_id=tool_call_id,
                result=_flatten_tool_result_content(result_payload.get("content")),
                is_error=data.get("isError"),
            )

        return cls(
            phase="start",
            name=name,
            tool_call_id=tool_call_id,
            args=data.get("args"),
        )
