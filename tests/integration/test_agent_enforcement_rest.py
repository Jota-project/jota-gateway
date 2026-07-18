"""REST /v1/chat/completions enforces per-client default_agent and allowed_agents."""
import json
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from src.db import database as db_database
from src.db.models import ClientRecord
from src.services.db_client import db_client
from tests.integration.conftest import VALID_KEY, CLIENT_ID


def _patch_client(engine, **fields):
    with Session(engine) as s:
        rec = s.get(ClientRecord, CLIENT_ID)
        for k, v in fields.items():
            setattr(rec, k, v)
        s.add(rec)
        s.commit()
    db_client.invalidate(VALID_KEY)


# --- Tests ----------------------------------------------------------------

def test_rest_allowed_model_passes(client, db_engine, mock_orchestrator, ha_bearer_headers):
    _patch_client(db_engine, allowed_agents=json.dumps(["a"]))
    r = client.post(
        "/v1/chat/completions",
        headers=ha_bearer_headers,
        json={"model": "a", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200


def test_rest_disallowed_model_returns_403_with_reason(client, db_engine, mock_orchestrator, ha_bearer_headers):
    _patch_client(db_engine, allowed_agents=json.dumps(["a"]))
    r = client.post(
        "/v1/chat/completions",
        headers=ha_bearer_headers,
        json={"model": "b", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == "forbidden"
    assert body["reason"] == "agent_not_permitted"
    assert "Agent 'b' not permitted" in body["message"]


def test_rest_model_not_in_roster_returns_403(client, db_engine, mock_orchestrator, ha_bearer_headers):
    _patch_client(db_engine, allowed_agents=None)
    r = client.post(
        "/v1/chat/completions",
        headers=ha_bearer_headers,
        json={"model": "nonexistent-agent-xyz",
              "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["reason"] == "agent_not_available"
    assert "Agent 'nonexistent-agent-xyz' not available" in body["message"]


def test_rest_default_agent_applied_when_model_missing(client, db_engine, mock_orchestrator, ha_bearer_headers):
    """default_agent='a' set, body.model='' → 200, session uses agent='a'."""
    _patch_client(db_engine, default_agent="a", allowed_agents=None)

    from src.services.protocol import OrchestratorEvent
    from src.core.session_key import make_session_key
    captured = {}

    async def _stream(text, user_id, model_id=None, session_key=None):
        captured["session_key"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    r = client.post(
        "/v1/chat/completions",
        headers=ha_bearer_headers,
        json={"model": "", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    # gateway_info mock has default_agent_id='main'; client.default_agent='a' wins
    assert captured.get("session_key") == make_session_key("a", CLIENT_ID)


def test_rest_legacy_trusted_origin_still_uses_gateway_default(client, mock_orchestrator):
    """Trusted-origin (no Bearer) — body.model ignored, gateway default used.

    Regression check: this path must keep working exactly as before.
    """
    from src.services.protocol import OrchestratorEvent
    from src.core.session_key import make_session_key
    captured = {}

    async def _stream(text, user_id, model_id=None, session_key=None):
        captured["session_key"] = session_key
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = _stream

    r = client.post(
        "/v1/chat/completions",
        json={"model": "anything", "messages": [{"role": "user", "content": "hi"}]},
        # NO auth_headers — trusted origin path
    )
    assert r.status_code == 200
    # gateway_info mock default_agent_id='main', client_id='ha' for legacy
    assert captured.get("session_key") == make_session_key("main", "ha")