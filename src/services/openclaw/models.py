from dataclasses import dataclass, field


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
