from src.services.openclaw import frames

# ── Shape ─────────────────────────────────────────────────────────


def test_every_frame_is_req_type():
    for fn, args in [
        (frames.connect_backend, ("r", "tok")),
        (frames.sessions_subscribe, ("r",)),
        (frames.health, ("r",)),
        (frames.chat_send, ("r", "sk", "msg", "idem")),
        (frames.chat_abort, ("r", "sk")),
        (frames.chat_history, ("r", "sk")),
        (frames.chat_inject, ("r", "sk", "ctx")),
        (frames.sessions_steer, ("r", "sk", "txt")),
        (frames.sessions_list, ("r",)),
        (frames.agents_list, ("r",)),
        (frames.agent_identity_get, ("r", "aid")),
        (frames.models_list, ("r",)),
        (frames.models_auth_status, ("r",)),
    ]:
        f = fn(*args)
        assert f["type"] == "req", f"{fn.__name__} must have type=req"
        assert f["id"] == "r", f"{fn.__name__} must echo req_id"
        assert isinstance(f["params"], dict), f"{fn.__name__} params must be dict"


# ── connect_backend ───────────────────────────────────────────────


def test_connect_backend_method():
    frame = frames.connect_backend("r1", "secret")
    assert frame["method"] == "connect"


def test_connect_backend_protocol_range():
    p = frames.connect_backend("r1", "secret")["params"]
    assert p["minProtocol"] == 3
    assert p["maxProtocol"] == 4


def test_connect_backend_token_in_auth():
    p = frames.connect_backend("r1", "my-token")["params"]
    assert p["auth"]["token"] == "my-token"


def test_connect_backend_default_client_id():
    p = frames.connect_backend("r1", "tok")["params"]
    assert p["client"]["id"] == "gateway-client"


def test_connect_backend_custom_client_id():
    p = frames.connect_backend("r1", "tok", client_id="custom")["params"]
    assert p["client"]["id"] == "custom"


def test_connect_backend_operator_role():
    p = frames.connect_backend("r1", "tok")["params"]
    assert p["role"] == "operator"
    assert "operator.read" in p["scopes"]
    assert "operator.write" in p["scopes"]


# ── sessions_subscribe ────────────────────────────────────────────


def test_sessions_subscribe_method():
    assert frames.sessions_subscribe("r1")["method"] == "sessions.subscribe"


def test_sessions_subscribe_empty_params():
    assert frames.sessions_subscribe("r1")["params"] == {}


# ── health ────────────────────────────────────────────────────────


def test_health_method():
    assert frames.health("r1")["method"] == "health"


def test_health_empty_params():
    assert frames.health("r1")["params"] == {}


# ── chat_send ─────────────────────────────────────────────────────


def test_chat_send_method():
    assert frames.chat_send("r", "sk", "msg", "idem")["method"] == "chat.send"


def test_chat_send_uses_sessionkey_not_session():
    """Regression: OpenClaw v2026.6.10 renamed session.key → sessionKey (flat)."""
    p = frames.chat_send("r", "agent:main:hab_sito", "hola", "idem")["params"]
    assert p["sessionKey"] == "agent:main:hab_sito"
    assert "session" not in p


def test_chat_send_message_and_idempotency_key():
    p = frames.chat_send("r", "sk", "hello world", "unique-idem")["params"]
    assert p["message"] == "hello world"
    assert p["idempotencyKey"] == "unique-idem"


# ── chat_abort ────────────────────────────────────────────────────


def test_chat_abort_method():
    assert frames.chat_abort("r", "sk")["method"] == "chat.abort"


def test_chat_abort_uses_sessionkey_not_session():
    """Regression: same rename as chat.send — both were broken in the 2026-07-01 incident."""
    p = frames.chat_abort("r", "agent:main:hab_sito")["params"]
    assert p["sessionKey"] == "agent:main:hab_sito"
    assert "session" not in p


# ── chat_history ──────────────────────────────────────────────────


def test_chat_history_method():
    assert frames.chat_history("r", "sk")["method"] == "chat.history"


def test_chat_history_defaults():
    p = frames.chat_history("r", "sk")["params"]
    assert p["limit"] == 50
    assert p["offset"] == 0


def test_chat_history_custom_pagination():
    p = frames.chat_history("r", "sk", limit=10, offset=20)["params"]
    assert p["limit"] == 10
    assert p["offset"] == 20


# ── chat_inject ───────────────────────────────────────────────────


def test_chat_inject_method():
    assert frames.chat_inject("r", "sk", "ctx")["method"] == "chat.inject"


def test_chat_inject_content():
    p = frames.chat_inject("r", "agent:main:hab_sito", "estado del hogar")["params"]
    assert p["sessionKey"] == "agent:main:hab_sito"
    assert p["content"] == "estado del hogar"


# ── sessions_steer ────────────────────────────────────────────────


def test_sessions_steer_method():
    assert frames.sessions_steer("r", "sk", "txt")["method"] == "sessions.steer"


def test_sessions_steer_params():
    p = frames.sessions_steer("r", "agent:main:hab_sito", "nueva dirección")["params"]
    assert p["key"] == "agent:main:hab_sito"
    assert p["steerText"] == "nueva dirección"


# ── sessions_list ─────────────────────────────────────────────────


def test_sessions_list_method():
    assert frames.sessions_list("r")["method"] == "sessions.list"


# ── agents_list ───────────────────────────────────────────────────


def test_agents_list_method():
    assert frames.agents_list("r")["method"] == "agents.list"


def test_agents_list_empty_params():
    assert frames.agents_list("r")["params"] == {}


# ── agent_identity_get ────────────────────────────────────────────


def test_agent_identity_get_method():
    assert frames.agent_identity_get("r", "main")["method"] == "agent.identity.get"


def test_agent_identity_get_param():
    assert frames.agent_identity_get("r", "assistant")["params"]["agentId"] == "assistant"


# ── models_list ───────────────────────────────────────────────────


def test_models_list_method():
    assert frames.models_list("r")["method"] == "models.list"


def test_models_list_default_view():
    assert frames.models_list("r")["params"]["view"] == "configured"


def test_models_list_custom_view():
    assert frames.models_list("r", view="all")["params"]["view"] == "all"


# ── models_auth_status ────────────────────────────────────────────


def test_models_auth_status_method():
    assert frames.models_auth_status("r")["method"] == "models.authStatus"


def test_models_auth_status_empty_params():
    assert frames.models_auth_status("r")["params"] == {}
