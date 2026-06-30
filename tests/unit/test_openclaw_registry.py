import asyncio
import pytest
from src.services.openclaw.registry import TurnRegistry, ClientRegistry, client_id_from_session_key


# --- client_id_from_session_key ---

def test_client_id_simple():
    assert client_id_from_session_key("agent:main:hab_sito") == "hab_sito"

def test_client_id_with_colons():
    assert client_id_from_session_key("agent:plants:telegram:direct:5239228928") == "5239228928"

def test_client_id_two_parts():
    assert client_id_from_session_key("agent:assistant:probe-test") == "probe-test"


# --- TurnRegistry ---

def test_register_returns_queue():
    reg = TurnRegistry()
    q = reg.register("req-1", "agent:main:client-a")
    assert isinstance(q, asyncio.Queue)

def test_get_queue_by_session():
    reg = TurnRegistry()
    q = reg.register("req-1", "agent:main:client-a")
    assert reg.get_queue_by_session("agent:main:client-a") is q

def test_get_queue_by_req():
    reg = TurnRegistry()
    q = reg.register("req-1", "agent:main:client-a")
    assert reg.get_queue_by_req("req-1") is q

def test_get_queue_missing_returns_none():
    reg = TurnRegistry()
    assert reg.get_queue_by_session("nonexistent") is None
    assert reg.get_queue_by_req("nonexistent") is None

def test_unregister_clears_both():
    reg = TurnRegistry()
    reg.register("req-1", "agent:main:client-a")
    reg.unregister("agent:main:client-a", "req-1")
    assert reg.get_queue_by_session("agent:main:client-a") is None
    assert reg.get_queue_by_req("req-1") is None

def test_two_sessions_independent():
    reg = TurnRegistry()
    qa = reg.register("req-a", "agent:main:client-a")
    qb = reg.register("req-b", "agent:main:client-b")
    assert reg.get_queue_by_session("agent:main:client-a") is qa
    assert reg.get_queue_by_session("agent:main:client-b") is qb
    assert qa is not qb

def test_error_all_notifies_and_clears():
    reg = TurnRegistry()
    qa = reg.register("req-a", "agent:main:client-a")
    qb = reg.register("req-b", "agent:main:client-b")
    reg.error_all("reconnecting")
    assert qa.get_nowait() == ("error", "reconnecting")
    assert qb.get_nowait() == ("error", "reconnecting")
    assert reg.get_queue_by_session("agent:main:client-a") is None
    assert reg.get_queue_by_req("req-a") is None


# --- ClientRegistry ---

def test_client_registry_register_get():
    reg = ClientRegistry()
    bridge = object()
    reg.register("hab_sito", bridge)
    assert reg.get("hab_sito") is bridge

def test_client_registry_unregister():
    reg = ClientRegistry()
    reg.register("hab_sito", object())
    reg.unregister("hab_sito")
    assert reg.get("hab_sito") is None

def test_client_registry_missing_returns_none():
    reg = ClientRegistry()
    assert reg.get("nonexistent") is None
