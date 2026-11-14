"""Unit tests for fogar_gateway.core.agent_policy.resolve_agent.

Pure helper — no DB, WS, or FastAPI. Stubs for ClientConfig and GatewayInfo
live inline to avoid coupling to other modules' internals.
"""

from dataclasses import dataclass, field

import pytest

from fogar_gateway.core.agent_policy import AgentPolicyError, resolve_agent

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


# --- DEFAULT_AGENT, the installation default (#245) ------------------------


@pytest.fixture
def installation_default(monkeypatch):
    from fogar_gateway.core.config import settings

    monkeypatch.setattr(settings, "DEFAULT_AGENT", "jota")


def test_default_agent_empty_changes_nothing():
    from fogar_gateway.core.config import settings

    assert settings.DEFAULT_AGENT == ""
    assert resolve_agent(None, _StubClientConfig(), _StubGatewayInfo("oc-default")) == "oc-default"


def test_default_agent_comes_before_the_openclaw_default(installation_default):
    assert resolve_agent(None, _StubClientConfig(), _StubGatewayInfo("oc-default")) == "jota"
    # Also with no client row (the legacy /v1 trusted-origin path) and no roster yet.
    assert resolve_agent(None, None, None) == "jota"


def test_client_default_and_requested_agent_win_over_default_agent(installation_default):
    gw = _StubGatewayInfo("oc-default", agents={"a": {}, "jota": {}})
    assert resolve_agent(None, _StubClientConfig(default_agent="b"), gw) == "b"
    assert resolve_agent("a", _StubClientConfig(default_agent="b"), gw) == "a"


def test_allowlist_applies_to_default_agent(installation_default):
    with pytest.raises(AgentPolicyError) as exc:
        resolve_agent(None, _StubClientConfig(allowed_agents=["a"]), _StubGatewayInfo())
    assert exc.value.kind == "not_permitted"
    assert resolve_agent(None, _StubClientConfig(allowed_agents=["jota"]), _StubGatewayInfo()) == "jota"


def test_default_agent_is_server_config_not_checked_against_the_roster(installation_default):
    """Like the other cascade defaults: a DEFAULT_AGENT missing from OpenClaw only warns at
    connect (OpenClawClient), it does not reject the session."""
    assert resolve_agent(None, _StubClientConfig(), _StubGatewayInfo(agents={"main": {}})) == "jota"


def test_default_agent_setting_is_stripped():
    from fogar_gateway.core.config import Settings

    assert Settings(DEFAULT_AGENT="  jota ").DEFAULT_AGENT == "jota"  # type: ignore[call-arg]


def test_default_agent_follows_the_client_agent_name_rule():
    """Como `default_agent` de un cliente (admin_schemas.AgentName): hasta 200 caracteres."""
    from pydantic import ValidationError

    from fogar_gateway.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(DEFAULT_AGENT="x" * 201)  # type: ignore[call-arg]
    assert Settings(DEFAULT_AGENT="x" * 200).DEFAULT_AGENT == "x" * 200  # type: ignore[call-arg]
