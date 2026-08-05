"""Agent selection and per-client policy enforcement.

Owns the cascade that picks the effective agent for a request, plus the
two policy checks (allowlist, global roster). Pure helper — no DB, no
FastAPI, no WebSocket. Translation of AgentPolicyError into wire formats
(close 1008 reason / 403 JSON body) happens at the call sites.
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.models.schemas import ClientConfig
    from src.services.openclaw.models import GatewayInfo


class AgentPolicyError(Exception):
    """Raised when the requested agent violates per-client policy or the
    OpenClaw global roster.

    Attributes:
        kind: "not_permitted" — agent not in client_config.allowed_agents
              "not_available" — agent not in gateway_info.agents
        agent_name: the offending agent, or None if no name is meaningful.
    """

    def __init__(self, kind: str, agent_name: str | None, message: str):
        super().__init__(message)
        self.kind = kind
        self.agent_name = agent_name


def resolve_agent(
    requested: str | None,
    client_config: Optional["ClientConfig"],
    gateway_info: Optional["GatewayInfo"],
) -> str:
    """Resolve the effective agent name for a request.

    Cascade (first non-empty-after-stripping wins):
      1. requested (handshake.agent or body.model)
      2. client_config.default_agent (if client_config is not None and set)
      3. gateway_info.default_agent_id (if gateway_info is not None)
      4. "main" (last-resort fallback; preserves legacy REST trusted-origin)

    Policy checks, fail-fast in order:
      a. If client_config is not None and client_config.allowed_agents is
         not None (i.e. an explicit list, including []): the effective
         agent must be in the list. Otherwise AgentPolicyError("not_permitted", ...).
         Note: allowed_agents=None means "no restriction" — this check is skipped.
      b. If the agent was explicitly requested (i.e. `requested is not None`)
         AND gateway_info is not None: the effective agent must be in
         gateway_info.agents. Otherwise AgentPolicyError("not_available", ...).
         The roster check is skipped when the agent came from the cascade
         (client default / gateway default / "main" fallback) — these are
         trusted server-side configuration, not user input.

    Raises:
        AgentPolicyError: when policy (a) or roster (b) rejects the effective agent.
    """
    # Normalize once, here, so every caller (WS handshake.agent, REST body.model)
    # gets identical whitespace-only-is-absent handling instead of duplicating
    # (and potentially diverging on) `.strip() or None` at each call site.
    requested = (requested or "").strip() or None

    # 1. Cascade — pick the effective agent.
    if requested:
        effective = requested
    elif client_config is not None and client_config.default_agent:
        effective = client_config.default_agent
    elif gateway_info is not None and gateway_info.default_agent_id:
        effective = gateway_info.default_agent_id
    else:
        effective = "main"

    # 2a. Allowlist check (only when an explicit list is configured).
    if (
        client_config is not None
        and client_config.allowed_agents is not None
        and effective not in client_config.allowed_agents
    ):
        raise AgentPolicyError(
            "not_permitted",
            effective,
            f"Agent '{effective}' not permitted for this client.",
        )

    # 2b. Global roster check (only when agent was explicitly requested,
    # not when it came from the cascade — gateway/client defaults are
    # trusted configuration, not user input).
    if requested is not None and gateway_info is not None and effective not in gateway_info.agents:
        raise AgentPolicyError(
            "not_available",
            effective,
            f"Agent '{effective}' not available.",
        )

    return effective
