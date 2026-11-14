"""Conversaciones (M19) vistas desde el cliente (arnés e2e: gateway real, OpenClaw falso).

Cada test mira lo que recibe el cliente por su WebSocket (`ready.conversation`,
`turn_start.conversation`/`origin`, `turn_end.reason`, cierres 4xxx) y, cuando importa, en qué
sesión de OpenClaw acabó cada turno (`openclaw.sessions`).
"""

import asyncio
import json
from datetime import datetime

import pytest
import websockets
from sqlmodel import Session

from fogar_gateway.core.config import settings
from fogar_gateway.db.models import ClientRecord
from fogar_gateway.services import conversations as conv_mod
from fogar_gateway.services.conversations import ConversationService, session_key_for
from tests.integration.conftest import ADMIN_TOKEN, CLIENT_ID
from tests.integration.harness.conftest import assert_turns_paired, of_type

PANEL = {"client_key": None, "admin_token": ADMIN_TOKEN}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    """Reloj de pared del gateway: el 14 a las 23:00 (el día empezó a las 5:00)."""
    c = Clock(datetime(2026, 11, 14, 23, 0))
    monkeypatch.setattr(conv_mod, "_now", c)
    return c


async def closed_by_gateway(client) -> websockets.ConnectionClosed:
    """Recoge lo que llegue (en `client.messages`) hasta que el gateway cierra el socket;
    devuelve el cierre."""
    while True:
        try:
            raw = await asyncio.wait_for(client.ws.recv(), timeout=10)
        except websockets.ConnectionClosed as closed:
            return closed
        client.messages.append(raw if isinstance(raw, bytes) else json.loads(raw))


async def run_turn(client, text: str, ends: int = 1) -> list:
    """`send` y espera `ends` turn_end más (el suyo, y el del turno que sustituya); devuelve
    los mensajes desde el `send`."""
    before = len(client.messages)
    seen = len(of_type(client.messages, "turn_end"))
    await client.send({"type": "send", "text": text})
    await client.collect(lambda m: len(of_type(m, "turn_end")) >= seen + ends)
    return client.messages[before:]


def said(harness, session_key: str) -> list[str]:
    """Lo que el usuario dijo en esa sesión de OpenClaw."""
    rows = harness.openclaw.sessions.get(session_key, {}).get("messages", [])
    return [m["content"] for m in rows if m["role"] == "user"]


# ── Conversación por defecto: una por cliente, agente y día ─────────────────


async def test_ready_and_turns_carry_the_default_conversation_of_the_day(harness, clock):
    client = await harness.connect()
    conv = client.ready["conversation"]
    assert conv["agent"] == "main" == client.ready["agent"]

    msgs = await run_turn(client, "hola")
    (start,) = of_type(msgs, "turn_start")
    assert (start["conversation"], start["origin"]) == (conv["id"], "user")
    assert said(harness, session_key_for("main", conv["id"])) == ["hola"]
    (end,) = of_type(msgs, "turn_end")
    assert end["reason"] == "done"


async def test_same_conversation_in_a_second_session_closes_the_first(harness, clock):
    """Una conversación, una sesión: la tele que reconecta antes de que caiga su socket viejo."""
    old = await harness.connect()
    new = await harness.connect()
    assert new.ready["conversation"] == old.ready["conversation"]  # la del día, la misma

    closed = await closed_by_gateway(old)
    assert closed.rcvd is not None
    assert (closed.rcvd.code, closed.rcvd.reason) == (4102, "conversation opened elsewhere")
    msgs = await run_turn(new, "sigo aquí")  # la nueva sigue viva
    assert of_type(msgs, "turn_start")[0]["conversation"] == new.ready["conversation"]["id"]


async def test_day_cutoff_moves_a_long_lived_session_at_its_next_turn(harness, clock):
    client = await harness.connect()
    yesterday = client.ready["conversation"]["id"]
    first = await run_turn(client, "uno")

    clock.now = datetime(2026, 11, 15, 5, 30)  # pasado el corte de las 5:00
    second = await run_turn(client, "dos")

    today = of_type(second, "turn_start")[0]["conversation"]
    assert of_type(first, "turn_start")[0]["conversation"] == yesterday
    assert today != yesterday
    assert said(harness, session_key_for("main", yesterday)) == ["uno"]
    assert said(harness, session_key_for("main", today)) == ["dos"]
    assert_turns_paired(client.messages)

    # Un recordatorio de la conversación de ayer sigue llegando a la tele, que ya está en la de hoy.
    await harness.push("se te olvidó algo", session_key=session_key_for("main", yesterday))
    await client.collect(lambda m: len(of_type(m, "turn_end")) == 3)
    push = of_type(client.messages, "turn_start")[-1]
    assert (push["conversation"], push["origin"]) == (yesterday, "push")
    assert_turns_paired(client.messages)


async def test_push_of_a_conversation_reaches_the_session_that_has_it_open(harness, clock):
    """Dos sesiones del mismo cliente en conversaciones distintas: cada push, a la suya."""
    a = await harness.connect(handshake={"conversation": "new"})
    b = await harness.connect(handshake={"conversation": "new"})
    assert a.ready["conversation"]["id"] != b.ready["conversation"]["id"]

    await harness.push("para a", session_key=harness.session_key(a))
    await a.collect(lambda m: bool(of_type(m, "turn_end")))
    await harness.push("para b", session_key=harness.session_key(b))
    await b.collect(lambda m: bool(of_type(m, "turn_end")))

    assert [t["text"] for t in of_type(a.messages, "token")] == ["para a"]
    assert [t["text"] for t in of_type(b.messages, "token")] == ["para b"]
    assert of_type(a.messages, "turn_start")[0]["conversation"] == a.ready["conversation"]["id"]


async def test_a_displaced_session_ends_its_turn_and_lets_go_before_the_new_one_talks(harness):
    """La vieja recibe el turn_end (`closed`) de su turno abierto antes del 4102, y su
    chat.abort sale antes del primer chat.send de la nueva, que llega entero."""
    old = await harness.connect()
    await old.send({"type": "send", "text": "wait-old"})  # el orquestador se queda esperando
    await harness.openclaw.wait_held(1)
    await old.collect(lambda m: bool(of_type(m, "turn_start")))

    new = await harness.connect()  # la misma conversación del día
    closed = await closed_by_gateway(old)
    assert (closed.rcvd.code, closed.rcvd.reason) == (4102, "conversation opened elsewhere")
    assert [e["reason"] for e in of_type(old.messages, "turn_end")] == ["closed"]
    assert_turns_paired(old.messages)

    msgs = await run_turn(new, "hola")
    assert [t["text"] for t in of_type(msgs, "token")] == ["reply-hola"]
    key = harness.session_key(new)
    assert [m for m, sk in harness.openclaw.calls if sk == key] == [
        "chat.send", "chat.abort", "chat.send"
    ]


async def test_a_new_session_that_fails_its_health_check_leaves_the_live_one_alone(harness):
    from fogar_gateway.main import app

    live = await harness.connect()

    async def down() -> bool:
        return False

    openclaw = app.state.openclaw
    openclaw.ping = down
    try:
        failed = await harness.open()
        closed = await closed_by_gateway(failed)
    finally:
        del openclaw.ping
    assert (closed.rcvd.code, closed.rcvd.reason) == (1011, "orchestrator unavailable")
    msgs = await run_turn(live, "sigo aquí")  # la viva sigue con su conversación
    assert of_type(msgs, "turn_start")[0]["conversation"] == live.ready["conversation"]["id"]


async def test_a_revoked_session_ends_its_open_turn_before_the_close(harness, monkeypatch):
    monkeypatch.setattr(settings, "SESSION_REVALIDATE_INTERVAL_S", 0.1)
    client = await harness.connect()
    await client.send({"type": "send", "text": "wait-revoked"})
    await harness.openclaw.wait_held(1)
    await client.collect(lambda m: bool(of_type(m, "turn_start")))

    async with harness.admin() as admin:
        r = await admin.patch(f"/admin/clients/{CLIENT_ID}", json={"is_active": False})
        assert r.status_code == 200
    closed = await closed_by_gateway(client)
    assert (closed.rcvd.code, closed.rcvd.reason) == (4200, "credential revoked")
    assert [e["reason"] for e in of_type(client.messages, "turn_end")] == ["closed"]
    assert_turns_paired(client.messages)


# ── Conversación elegida ─────────────────────────────────────────────────────


async def test_a_chosen_conversation_never_changes_day_and_can_be_resumed(harness, clock):
    client = await harness.connect(handshake={"conversation": "new"})
    chosen = client.ready["conversation"]["id"]
    await run_turn(client, "uno")
    clock.now = datetime(2026, 11, 17, 12, 0)
    msgs = await run_turn(client, "dos")
    assert of_type(msgs, "turn_start")[0]["conversation"] == chosen
    await client.ws.close()

    again = await harness.connect(handshake={"conversation": chosen})
    assert again.ready["conversation"]["id"] == chosen
    await run_turn(again, "tres")
    assert said(harness, session_key_for("main", chosen)) == ["uno", "dos", "tres"]


async def test_a_conversation_of_another_client_is_refused(harness, db_engine):
    with Session(db_engine) as s:
        s.add(ClientRecord(id="otro", name="Otro", client_key="otra-clave-de-cliente"))
        s.commit()
    foreign = await ConversationService(db_engine).create("otro", "main")

    for conversation in (foreign.id, "no-existe"):
        client = await harness.open(handshake={"conversation": conversation})
        closed = await closed_by_gateway(client)
        assert (closed.rcvd.code, closed.rcvd.reason) == (4100, "conversation not found")
        assert of_type(client.messages, "ready") == []


async def test_a_conversation_keeps_its_agent(harness):
    client = await harness.connect(handshake={"conversation": "new"})
    chosen = client.ready["conversation"]["id"]
    other = await harness.open(handshake={"conversation": chosen, "agent": "otro-agente"})
    closed = await closed_by_gateway(other)
    assert (closed.rcvd.code, closed.rcvd.reason) == (4101, "conversation agent mismatch")


# ── turn_end.reason en cada camino ───────────────────────────────────────────


async def test_turn_end_reason_for_each_way_a_turn_ends(harness, monkeypatch):
    client = await harness.connect()

    def last_end():
        return of_type(client.messages, "turn_end")[-1]

    assert of_type(await run_turn(client, "hola"), "turn_end")[0]["reason"] == "done"
    assert of_type(await run_turn(client, "fail"), "turn_end")[0]["reason"] == "error"

    await client.send({"type": "send", "text": "wait-1"})  # se queda esperando
    await harness.openclaw.wait_held(1)
    msgs = await run_turn(client, "otra", ends=2)  # un send nuevo lo sustituye
    replaced, done = of_type(msgs, "turn_end")
    assert (replaced["reason"], done["reason"]) == ("replaced", "done")

    await client.send({"type": "send", "text": "wait-2"})
    await harness.openclaw.wait_held(2)
    await client.collect(lambda m: len(of_type(m, "turn_start")) == 5)
    await client.send({"type": "cancel"})
    await client.collect(lambda m: len(of_type(m, "turn_end")) == 5)
    assert last_end()["reason"] == "cancelled"

    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 0.3)
    assert of_type(await run_turn(client, "hold"), "turn_end")[0]["reason"] == "timeout"

    await harness.push("aviso")
    await client.collect(lambda m: len(of_type(m, "turn_end")) == 7)
    assert last_end()["reason"] == "done"
    assert of_type(client.messages, "turn_start")[-1]["origin"] == "push"
    assert of_type(client.messages, "interrupted") == []
    assert_turns_paired(client.messages)


# ── El panel web entra con el ADMIN_TOKEN ────────────────────────────────────


async def test_admin_token_enters_as_the_panel_web_client(harness, caplog):
    panel = await harness.connect(handshake=PANEL)
    assert panel.ready["session_id"].startswith("panel-web:")
    msgs = await run_turn(panel, "hola desde la web")
    assert of_type(msgs, "turn_start")[0]["conversation"] == panel.ready["conversation"]["id"]
    await panel.ws.close()
    await harness.connect(handshake=PANEL)  # la ficha se crea una sola vez

    async with harness.admin() as admin:
        clients = (await admin.get("/admin/clients")).json()
    panels = [c for c in clients if c["name"] == "Panel web"]
    assert [(c["id"], c["client_type"]) for c in panels] == [("panel-web", "web")]
    assert "client_key" not in panels[0]
    assert ADMIN_TOKEN not in caplog.text


@pytest.mark.parametrize("token", ["wrong-token", ""])
async def test_a_wrong_admin_token_is_refused(harness, token):
    client = await harness.open(handshake={"client_key": None, "admin_token": token})
    closed = await closed_by_gateway(client)
    assert (closed.rcvd.code, closed.rcvd.reason) == (4010, "invalid or inactive credential")


async def test_the_panel_resumes_a_conversation_created_from_admin(harness):
    async with harness.admin() as admin:
        r = await admin.post("/admin/conversations", json={})
        assert r.status_code == 201, r.text
        created = r.json()
    assert (created["client_id"], created["kind"], created["agent"]) == ("panel-web", "created", "main")

    panel = await harness.connect(handshake={**PANEL, "conversation": created["id"]})
    assert panel.ready["conversation"] == {"id": created["id"], "agent": "main"}
    await run_turn(panel, "hola")

    async with harness.admin() as admin:
        rows = (await admin.get("/admin/conversations", params={"client_id": "panel-web"})).json()
        history = (await admin.get(f"/admin/conversations/{created['id']}/messages")).json()
    (row,) = [r for r in rows if r["id"] == created["id"]]
    assert row["turn_count"] == 1 and row["last_turn_at"] is not None
    assert [(m["role"], m["text"]) for m in history["messages"]] == [
        ("user", "hola"),
        ("assistant", "reply-hola"),
    ]
    assert history["has_more"] is False
