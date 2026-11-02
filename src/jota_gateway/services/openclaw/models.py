from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class GatewayInfo:
    default_agent_id: str
    tick_interval_ms: int
    max_payload: int
    # Agent ids in the order agents.list returns them (/v1/models lists them as-is).
    agents: list[str] = field(default_factory=list)

    def has_agent(self, agent_id: str) -> bool:
        return agent_id in self.agents

    def update_agents_from_list(self, payload: dict) -> None:
        """Populate agents from an agents.list response (the only roster source since
        OpenClaw server 2026.6.11, the minimum supported)."""
        self.agents = [a["id"] for a in payload.get("agents", [])]
        self.default_agent_id = payload.get("defaultId", self.default_agent_id)

    @classmethod
    def from_hello_ok(cls, payload: dict) -> "GatewayInfo":
        policy = payload.get("policy", {})
        snapshot = payload.get("snapshot", {})
        return cls(
            default_agent_id=snapshot.get("sessionDefaults", {}).get("defaultAgentId") or "main",
            tick_interval_ms=policy.get("tickIntervalMs", 15000),
            max_payload=policy.get("maxPayload", 26214400),
        )


def _flatten_tool_result_content(content: list | None) -> str | None:
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
    args: dict | None = None
    result: str | None = None
    is_error: bool | None = None

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
