"""WS handshake enforces per-client default_agent and allowed_agents."""

import json

import pytest
from sqlmodel import Session

from src.db.models import ClientRecord
<<<<<<< HEAD
from src.services.db_client import db_client
=======

>>>>>>> cb9eb1d (refactor: sin caché de sesiones y SQLite fuera del event loop (#265, #240))
from tests.integration.conftest import CLIENT_ID, VALID_KEY


def _patch_client(engine, **fields):
    """Update the seeded client's fields directly in the DB."""
    with Session(engine) as s:
        rec = s.get(ClientRecord, CLIENT_ID)
        for k, v in fields.items():
            setattr(rec, k, v)
        s.add(rec)
        s.commit()


# --- Tests ----------------------------------------------------------------


def test_ws_allowed_agent_passes(client, db_engine):
    """allowed_agents=['a'], handshake with agent='a' → ready.agent == 'a'."""
    _patch_client(db_engine, allowed_agents=json.dumps(["a"]))
    # The default mock roster (conftest) already contains 'a'.
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {
                "client_key": VALID_KEY,
                "input_mode": "text",
                "output_mode": ["text"],
                "agent": "a",
            }
        )
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["agent"] == "a"


def test_ws_disallowed_agent_closes_1008(client, db_engine):
    """allowed_agents=['a'], handshake with agent='b' → close 1008."""
    _patch_client(db_engine, allowed_agents=json.dumps(["a"]))
    with pytest.raises(Exception), client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {
                "client_key": VALID_KEY,
                "input_mode": "text",
                "output_mode": ["text"],
                "agent": "b",
            }
        )
        ws.receive_text()  # should raise on close frame


def test_ws_agent_not_in_roster_closes_1008(client, db_engine):
    """allowed_agents=None, handshake with agent that doesn't exist anywhere → 1008."""
    _patch_client(db_engine, allowed_agents=None)
    with pytest.raises(Exception), client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {
                "client_key": VALID_KEY,
                "input_mode": "text",
                "output_mode": ["text"],
                "agent": "nonexistent-agent-xyz",
            }
        )
        ws.receive_text()


def test_ws_default_agent_applied_when_handshake_omits_agent(client, db_engine):
    """default_agent='a' set on client; handshake has no agent → ready.agent == 'a'.

    This validates the bug fix: previously the gateway default always won,
    silently ignoring the admin-configured per-client default.
    """
    _patch_client(db_engine, default_agent="a", allowed_agents=None)
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {
                "client_key": VALID_KEY,
                "input_mode": "text",
                "output_mode": ["text"],
                # no "agent" key
            }
        )
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["agent"] == "a"


def test_ws_empty_allowed_denies_everything(client, db_engine):
    """allowed_agents=[] (deny-all) without an agent in handshake → close 1008."""
    _patch_client(db_engine, allowed_agents=json.dumps([]))
    with pytest.raises(Exception), client.websocket_connect("/ws/stream") as ws:
        ws.send_json(
            {
                "client_key": VALID_KEY,
                "input_mode": "text",
                "output_mode": ["text"],
            }
        )
        ws.receive_text()
