"""Unit tests for src.core.agent_policy.resolve_agent.

Pure helper — no DB, WS, or FastAPI. Stubs for ClientConfig and GatewayInfo
live inline to avoid coupling to other modules' internals.
"""

from dataclasses import dataclass, field

import pytest

from src.core.agent_policy import AgentPolicyError, resolve_agent

# --- Stubs ----------------------------------------------------------------


@dataclass
class _StubGatewayInfo:
    default_agent_id: str = "main"
    agents: dict = field(default_factory=dict)


@dataclass
class _StubClientConfig:
    default_agent: str | None = None
    allowed_agents: list[str] | None = None


# --- Tests ----------------------------------------------------------------


def test_none_allowed_requested_in_roster():
    gi = _StubGatewayInfo(agents={"x": None})
    cc = _StubClientConfig(allowed_agents=None)
    assert resolve_agent("x", cc, gi) == "x"


def test_none_allowed_no_requested_uses_gateway_default():
    gi = _StubGatewayInfo(default_agent_id="gw-default", agents={"a": None})
    cc = _StubClientConfig(allowed_agents=None)
    assert resolve_agent(None, cc, gi) == "gw-default"


def test_legacy_no_client_no_gateway_falls_back_to_main():
    # REST legacy trusted-origin path with no body.model
    assert resolve_agent(None, None, None) == "main"


def test_empty_allowed_requested_denied():
    gi = _StubGatewayInfo(agents={"x": None})
    cc = _StubClientConfig(allowed_agents=[])
    with pytest.raises(AgentPolicyError) as ei:
        resolve_agent("x", cc, gi)
    assert ei.value.kind == "not_permitted"
    assert ei.value.agent_name == "x"
    assert "not permitted" in str(ei.value)


def test_empty_allowed_no_requested_denies_everything():
    gi = _StubGatewayInfo(default_agent_id="anything", agents={"anything": None})
    cc = _StubClientConfig(allowed_agents=[])
    with pytest.raises(AgentPolicyError) as ei:
        resolve_agent(None, cc, gi)
    assert ei.value.kind == "not_permitted"
    assert ei.value.agent_name == "anything"


def test_specific_allowed_requested_not_in_list_denied():
    gi = _StubGatewayInfo(agents={"a": None, "b": None})
    cc = _StubClientConfig(allowed_agents=["a"])
    with pytest.raises(AgentPolicyError) as ei:
        resolve_agent("b", cc, gi)
    assert ei.value.kind == "not_permitted"
    assert ei.value.agent_name == "b"


def test_specific_allowed_no_requested_uses_client_default():
    gi = _StubGatewayInfo(agents={"a": None, "b": None})
    cc = _StubClientConfig(default_agent="a", allowed_agents=["a"])
    assert resolve_agent(None, cc, gi) == "a"


def test_specific_allowed_client_default_not_in_list_denied():
    gi = _StubGatewayInfo(agents={"b": None})
    cc = _StubClientConfig(default_agent="b", allowed_agents=["a"])
    with pytest.raises(AgentPolicyError) as ei:
        resolve_agent(None, cc, gi)
    assert ei.value.kind == "not_permitted"
    assert ei.value.agent_name == "b"


def test_specific_allowed_requested_in_list_and_roster_ok():
    gi = _StubGatewayInfo(agents={"a": None})
    cc = _StubClientConfig(allowed_agents=["a"])
    assert resolve_agent("a", cc, gi) == "a"


def test_specific_allowed_requested_in_list_but_missing_from_roster():
    """Ordering: allowlist passes, roster fails → not_available, not not_permitted."""
    gi = _StubGatewayInfo(agents={})  # "a" not in roster
    cc = _StubClientConfig(allowed_agents=["a"])
    with pytest.raises(AgentPolicyError) as ei:
        resolve_agent("a", cc, gi)
    assert ei.value.kind == "not_available"
    assert ei.value.agent_name == "a"


def test_none_allowed_requested_missing_from_roster():
    gi = _StubGatewayInfo(agents={})
    cc = _StubClientConfig(allowed_agents=None)
    with pytest.raises(AgentPolicyError) as ei:
        resolve_agent("x", cc, gi)
    assert ei.value.kind == "not_available"
    assert ei.value.agent_name == "x"


def test_legacy_no_client_requested_in_roster_passes():
    """REST trusted-origin (no client_config) — allowlist check skipped."""
    gi = _StubGatewayInfo(agents={"x": None})
    assert resolve_agent("x", None, gi) == "x"


def test_whitespace_only_requested_falls_through_cascade():
    """A whitespace-only requested agent (e.g. WS handshake {"agent": "   "})
    must be treated as absent, same as REST's `(body.model or "").strip() or
    None` — not as an explicit (and likely invalid) agent name. Otherwise WS
    and REST diverge on identical malformed input."""
    gi = _StubGatewayInfo(default_agent_id="gw-default", agents={"a": None})
    cc = _StubClientConfig(default_agent="a", allowed_agents=None)
    assert resolve_agent("   ", cc, gi) == "a"


def test_allowlist_runs_before_roster_check():
    """Acceptance criterion: allowed_agents is enforced BEFORE the global roster.

    Setup: requested='b', allowed_agents=['a'], roster does NOT contain 'b'.
    If allowlist ran after roster, we'd see not_available. Allowlist-first
    means we see not_permitted.
    """
    gi = _StubGatewayInfo(agents={})  # 'b' missing
    cc = _StubClientConfig(allowed_agents=["a"])
    with pytest.raises(AgentPolicyError) as ei:
        resolve_agent("b", cc, gi)
    assert ei.value.kind == "not_permitted", (
        "allowlist must be checked before the roster — got not_available"
    )
    assert ei.value.agent_name == "b"
