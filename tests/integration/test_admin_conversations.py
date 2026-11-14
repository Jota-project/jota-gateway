"""`/admin/conversations*` (M19) con un OpenClaw simulado: auth, validación, OpenClaw caído,
la conversación nueva del panel (agente por la cascada, #245) y el borrado de un cliente."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, select

from fogar_gateway.core.config import settings
from fogar_gateway.db.models import ClientRecord, ConversationRecord
from fogar_gateway.services import conversations as conv_mod
from fogar_gateway.services.openclaw.client import OrchestratorRequestError
from tests.integration.conftest import CLIENT_ID, VALID_KEY


def _sessions(*rows):
    async def request(method, params, timeout=10.0):
        if method == "sessions.list":
            return {"sessions": list(rows), "hasMore": False}
        raise AssertionError(method)

    return request


def _down(code="orchestrator_unavailable"):
    return AsyncMock(side_effect=OrchestratorRequestError(code))


@pytest.fixture
def conversations(client):
    return client.app.state.conversations


def test_admin_conversations_needs_the_admin_token(client):
    assert client.get("/admin/conversations").status_code == 422  # sin cabecera
    r = client.get("/admin/conversations", headers={"x-admin-token": "nope"})
    assert r.status_code == 401


def test_list_has_titles_from_openclaw(client, admin_headers, conversations, mock_orchestrator):
    import asyncio

    conv = asyncio.run(conversations.default_for(CLIENT_ID, "main"))
    mock_orchestrator.request = AsyncMock(wraps=_sessions(
        {"key": conv.session_key, "derivedTitle": "La cena"}, {"key": "agent:x:other"}
    ))
    # La del día en la que nunca se habló no sale (un cliente que conectó y calló).
    assert client.get("/admin/conversations", headers=admin_headers).json() == []
    asyncio.run(conversations.note_turn(conv.id))
    r = client.get("/admin/conversations", headers=admin_headers)
    assert r.status_code == 200
    (row,) = r.json()
    assert row == {
        "id": conv.id, "client_id": CLIENT_ID, "client_name": "Test Client", "agent": "main",
        "kind": "default", "day": conv.day.isoformat(), "created_at": row["created_at"],
        "last_turn_at": row["last_turn_at"], "turn_count": 1, "title": "La cena",
    }
    assert row["last_turn_at"] is not None
    client.get("/admin/conversations", headers=admin_headers)
    # Títulos cacheados (TITLES_TTL_S): una sola lectura de sessions.list para dos listas.
    assert mock_orchestrator.request.await_count == 1
    assert client.get(
        "/admin/conversations", headers=admin_headers, params={"client_id": "otro"}
    ).json() == []


def test_list_never_fails_when_openclaw_is_down(client, admin_headers, conversations, mock_orchestrator):
    import asyncio

    asyncio.run(conversations.create(CLIENT_ID, "main"))
    mock_orchestrator.request = _down()
    r = client.get("/admin/conversations", headers=admin_headers)
    assert r.status_code == 200
    assert [row["title"] for row in r.json()] == [None]


@pytest.mark.parametrize(
    "code, status", [("orchestrator_unavailable", 503), ("orchestrator_error", 502)]
)
def test_messages_with_openclaw_down_is_a_stable_error(
    client, admin_headers, conversations, mock_orchestrator, code, status
):
    import asyncio

    conv = asyncio.run(conversations.create(CLIENT_ID, "main"))
    mock_orchestrator.request = _down(code)
    r = client.get(f"/admin/conversations/{conv.id}/messages", headers=admin_headers)
    assert (r.status_code, r.json()) == (status, {"detail": code})


def test_messages_of_an_unknown_conversation_is_404(client, admin_headers):
    r = client.get("/admin/conversations/nope/messages", headers=admin_headers)
    assert r.status_code == 404


def test_messages_limit_is_validated_and_passed_to_chat_history(
    client, admin_headers, conversations, mock_orchestrator
):
    import asyncio

    conv = asyncio.run(conversations.create(CLIENT_ID, "main"))
    mock_orchestrator.request = AsyncMock(return_value={"messages": [], "hasMore": True})
    url = f"/admin/conversations/{conv.id}/messages"
    assert client.get(url, headers=admin_headers, params={"limit": 0}).status_code == 422
    assert client.get(url, headers=admin_headers, params={"limit": 1001}).status_code == 422
    r = client.get(url, headers=admin_headers, params={"limit": 5})
    assert r.json() == {"conversation_id": conv.id, "messages": [], "has_more": True}
    mock_orchestrator.request.assert_awaited_once_with(
        "chat.history", {"sessionKey": conv.session_key, "limit": 5}
    )


# ── POST: una conversación nueva del panel web ───────────────────────────────


def test_create_uses_the_cascade_and_the_installation_default(
    client, admin_headers, monkeypatch
):
    r = client.post("/admin/conversations", headers=admin_headers, json={})
    assert r.status_code == 201
    assert (r.json()["agent"], r.json()["kind"]) == ("main", "created")  # el de OpenClaw

    monkeypatch.setattr(settings, "DEFAULT_AGENT", "a")  # #245: también de una conversación nueva
    r = client.post("/admin/conversations", headers=admin_headers, json={})
    assert (r.json()["agent"], r.json()["client_id"], r.json()["client_name"]) == (
        "a", "panel-web", "Panel web"
    )


def test_create_validates_the_agent_like_the_handshake(client, admin_headers, db_engine):
    r = client.post("/admin/conversations", headers=admin_headers, json={"agent": "openclaw"})
    assert r.status_code == 201 and r.json()["agent"] == "openclaw"
    r = client.post("/admin/conversations", headers=admin_headers, json={"agent": "nope"})
    assert (r.status_code, r.json()) == (422, {"detail": "agent_not_available"})
    with Session(db_engine) as s:
        panel = s.get(ClientRecord, "panel-web")
        panel.allowed_agents = '["a"]'
        s.add(panel)
        s.commit()
    r = client.post("/admin/conversations", headers=admin_headers, json={"agent": "openclaw"})
    assert (r.status_code, r.json()) == (422, {"detail": "agent_not_permitted"})
    r = client.post("/admin/conversations", headers=admin_headers, json={"agent": "", "x": 1})
    assert r.status_code == 422


def _set_panel(db_engine, **fields):
    with Session(db_engine) as s:
        panel = s.get(ClientRecord, "panel-web")
        for name, value in fields.items():
            setattr(panel, name, value)
        s.add(panel)
        s.commit()


def test_agents_for_a_new_conversation_follow_the_panel_policy(
    client, admin_headers, db_engine, mock_orchestrator, monkeypatch
):
    r = client.get("/admin/conversations/agents", headers=admin_headers)
    assert (r.status_code, r.json()) == (200, {"agents": ["main", "a", "openclaw"], "default": "main"})
    with Session(db_engine) as s:  # una lectura: no crea la ficha del panel
        assert s.get(ClientRecord, "panel-web") is None
    client.post("/admin/conversations", headers=admin_headers, json={})  # ahora sí existe
    monkeypatch.setattr(settings, "DEFAULT_AGENT", "a")
    assert client.get("/admin/conversations/agents", headers=admin_headers).json()["default"] == "a"
    _set_panel(db_engine, allowed_agents='["openclaw"]')
    # El de la cascada ("a") no le está permitido al panel: no hay agente por defecto.
    r = client.get("/admin/conversations/agents", headers=admin_headers)
    assert r.json() == {"agents": ["openclaw"], "default": None}
    mock_orchestrator.gateway_info = None  # OpenClaw sin conectar: sin roster, sus permitidos
    assert client.get("/admin/conversations/agents", headers=admin_headers).json() == {
        "agents": ["openclaw"], "default": None
    }
    _set_panel(db_engine, allowed_agents=None, is_active=False)
    r = client.get("/admin/conversations/agents", headers=admin_headers)
    assert (r.status_code, r.json()) == (409, {"detail": "panel_client_inactive"})
    _set_panel(db_engine, is_active=True)
    r = client.get("/admin/conversations/agents", headers=admin_headers)
    assert r.json() == {"agents": [], "default": "a"}


def test_conversation_dates_carry_their_utc_offset(client, admin_headers, conversations):
    import asyncio

    conv = asyncio.run(conversations.create(CLIENT_ID, "main"))
    asyncio.run(conversations.note_turn(conv.id))
    (row,) = client.get("/admin/conversations", headers=admin_headers).json()
    # SQLite las guarda sin zona; salen en UTC con su desplazamiento, no como hora local.
    for field in ("created_at", "last_turn_at"):
        assert datetime.fromisoformat(row[field]).utcoffset() is not None, field
    created = client.post("/admin/conversations", headers=admin_headers, json={}).json()
    assert datetime.fromisoformat(created["created_at"]).utcoffset() is not None


def test_create_with_the_panel_disabled_is_a_conflict(client, admin_headers, db_engine):
    client.post("/admin/conversations", headers=admin_headers, json={})
    with Session(db_engine) as s:
        panel = s.get(ClientRecord, "panel-web")
        panel.is_active = False
        s.add(panel)
        s.commit()
    r = client.post("/admin/conversations", headers=admin_headers, json={})
    assert (r.status_code, r.json()) == (409, {"detail": "panel_client_inactive"})


def test_deleting_a_client_deletes_its_conversations(client, admin_headers, conversations, db_engine):
    import asyncio

    conv = asyncio.run(conversations.default_for(CLIENT_ID, "main"))
    assert client.delete(f"/admin/clients/{CLIENT_ID}", headers=admin_headers).status_code == 204
    with Session(db_engine) as s:
        assert s.exec(select(ConversationRecord)).all() == []
    assert conversations.index.get(conv.session_key) is None


# ── /v1 (Home Assistant): la misma regla diaria ──────────────────────────────


def test_v1_follows_the_daily_default_conversation(
    client_untrusted, mock_orchestrator, db_engine, monkeypatch
):
    from fogar_gateway.services.protocol import OrchestratorEvent

    keys = []

    async def stream(text, session_key=None):
        keys.append(session_key)
        yield OrchestratorEvent(type="status", content="accepted")  # el ack del chat.send
        yield OrchestratorEvent(type="token", content="ok")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = stream
    now = {"t": datetime(2026, 11, 14, 22, 0)}
    monkeypatch.setattr(conv_mod, "_now", lambda: now["t"])
    body = {"messages": [{"role": "user", "content": "hola"}]}
    headers = {"authorization": f"Bearer {VALID_KEY}"}

    for when in (datetime(2026, 11, 14, 22, 0), datetime(2026, 11, 15, 4, 0), datetime(2026, 11, 15, 6, 0)):
        now["t"] = when
        assert client_untrusted.post("/v1/chat/completions", json=body, headers=headers).status_code == 200

    assert keys[0] == keys[1] != keys[2]  # el corte es a las 5:00, no a medianoche
    with Session(db_engine) as s:
        rows = s.exec(select(ConversationRecord).order_by(ConversationRecord.day)).all()
    assert [(r.kind, r.turn_count, r.session_key) for r in rows] == [
        ("default", 2, keys[0]),
        ("default", 1, keys[2]),
    ]


def test_v1_counts_a_turn_only_once_it_started(client_untrusted, mock_orchestrator, db_engine):
    from fogar_gateway.services.protocol import OrchestratorEvent

    async def busy(text, session_key=None):
        yield OrchestratorEvent(type="error", content="turn_in_progress")

    mock_orchestrator.stream_response = busy
    body = {"messages": [{"role": "user", "content": "hola"}]}
    headers = {"authorization": f"Bearer {VALID_KEY}"}
    r = client_untrusted.post("/v1/chat/completions", json=body, headers=headers)
    assert (r.status_code, r.json()) == (409, {"error": "turn_in_progress"})
    with Session(db_engine) as s:
        (row,) = s.exec(select(ConversationRecord)).all()
    assert row.turn_count == 0 and row.last_turn_at is None


def test_v1_counts_the_turn_once_openclaw_accepted_it(
    client_untrusted, mock_orchestrator, db_engine
):
    """Aceptado el chat.send, el turno cuenta aunque luego falle (o el cliente se vaya)."""
    from fogar_gateway.services.protocol import OrchestratorEvent

    async def accepted_then_dropped(text, session_key=None):
        yield OrchestratorEvent(type="status", content="accepted")
        yield OrchestratorEvent(type="error", content="orchestrator_disconnected")

    mock_orchestrator.stream_response = accepted_then_dropped
    body = {"messages": [{"role": "user", "content": "hola"}]}
    headers = {"authorization": f"Bearer {VALID_KEY}"}
    r = client_untrusted.post("/v1/chat/completions", json=body, headers=headers)
    assert r.status_code == 502
    with Session(db_engine) as s:
        (row,) = s.exec(select(ConversationRecord)).all()
    assert row.turn_count == 1 and row.last_turn_at is not None


def test_v1_streaming_closes_its_session_and_counts_the_turn(
    client_untrusted, mock_orchestrator, db_engine, admin_headers
):
    from fogar_gateway.services.protocol import OrchestratorEvent

    async def stream(text, session_key=None):
        yield OrchestratorEvent(type="status", content="accepted")
        yield OrchestratorEvent(type="token", content="hola")
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = stream
    body = {"messages": [{"role": "user", "content": "hola"}], "stream": True}
    headers = {"authorization": f"Bearer {VALID_KEY}"}
    r = client_untrusted.post("/v1/chat/completions", json=body, headers=headers)
    assert r.status_code == 200 and "data: [DONE]" in r.text
    sessions = client_untrusted.get("/admin/sessions", headers=admin_headers).json()
    (http,) = [s for s in sessions["sessions"] if s["session_id"].startswith("http:")]
    assert http["status"] == "completed" and http["ended_at"] is not None
    assert sessions["active"] == 0
    with Session(db_engine) as s:
        (row,) = s.exec(select(ConversationRecord)).all()
    assert row.turn_count == 1


def test_v1_database_failure_is_a_stable_error(client_untrusted, monkeypatch, caplog):
    async def broken(*a, **k):
        raise RuntimeError("database is locked /secret/path")

    monkeypatch.setattr(client_untrusted.app.state.conversations, "default_for", broken)
    body = {"messages": [{"role": "user", "content": "hola"}]}
    headers = {"authorization": f"Bearer {VALID_KEY}"}
    r = client_untrusted.post("/v1/chat/completions", json=body, headers=headers)
    assert (r.status_code, r.json()) == (502, {"error": "internal_error"})
    assert "/secret/path" not in caplog.text


def test_v1_legacy_trusted_origin_keeps_its_key_and_uses_default_agent(
    client, mock_orchestrator, monkeypatch
):
    """Sin Bearer no hay ficha de cliente, ni conversación: conserva `agent:{agent}:ha`, y su
    agente sale de la cascada, con DEFAULT_AGENT (#245)."""
    from fogar_gateway.services.protocol import OrchestratorEvent

    keys = []

    async def stream(text, session_key=None):
        keys.append(session_key)
        yield OrchestratorEvent(type="status", content="done")

    mock_orchestrator.stream_response = stream
    monkeypatch.setattr(settings, "DEFAULT_AGENT", "jota")
    body = {"messages": [{"role": "user", "content": "hola"}]}
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    assert keys == ["agent:jota:ha"]
    assert client.app.state.conversations.index.get("agent:jota:ha") is None


def test_ws_uses_default_agent_when_nothing_else_says(client, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_AGENT", "a")
    with client.websocket_connect("/ws/stream") as ws:
        ws.send_json({"client_key": VALID_KEY, "input_mode": "text", "output_mode": ["text"]})
        ready = ws.receive_json()
    assert ready["type"] == "ready"
    assert ready["agent"] == ready["conversation"]["agent"] == "a"
