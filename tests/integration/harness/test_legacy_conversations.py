"""Conversaciones de antes de M19 y lectura de OpenClaw desde `/admin` (arnés e2e).

OpenClaw ya tiene, al arrancar el gateway, la sesión `agent:main:<client.id>` de la tele (la
clave de antes de M19), con título y mensajes. El gateway la importa una vez como `legacy`.
"""

import asyncio
import logging
import time

import pytest

from tests.integration.conftest import CLIENT_ID
from tests.integration.harness.conftest import assert_turns_paired, of_type

LEGACY_KEY = f"agent:main:{CLIENT_ID}"


@pytest.fixture
def openclaw_seed():
    return {
        LEGACY_KEY: {
            "title": "Luces del salón",
            "messages": [
                {"role": "user", "content": "enciende la luz", "timestamp": 1_794_000_000_000},
                {"role": "assistant", "content": [{"type": "text", "text": "Hecho."}],
                 "timestamp": 1_794_000_001_000},
                {"role": "toolResult", "content": "ok"},
            ],
        },
        # Ni de un cliente conocido ni de M19: no se importa.
        "agent:main:telegram:direct:42": {"title": "Telegram"},
    }


async def _conversations(harness, timeout: float = 5.0, **params) -> list[dict]:
    """GET /admin/conversations hasta que aparezca la importada (la importación no bloquea)."""
    deadline = time.monotonic() + timeout
    async with harness.admin() as admin:
        while True:
            rows = (await admin.get("/admin/conversations", params=params)).json()
            if any(r["kind"] == "legacy" for r in rows) or time.monotonic() > deadline:
                return rows
            await asyncio.sleep(0.05)


async def test_pre_m19_session_is_imported_as_legacy_with_its_title(harness, caplog):
    rows = await _conversations(harness)
    (legacy,) = [r for r in rows if r["kind"] == "legacy"]
    assert (legacy["client_id"], legacy["agent"], legacy["title"]) == (CLIENT_ID, "main", "Luces del salón")
    assert legacy["day"] is None
    assert len(rows) == 1  # ni la de Telegram ni ninguna más

    async with harness.admin() as admin:
        history = (await admin.get(f"/admin/conversations/{legacy['id']}/messages")).json()
    assert [(m["role"], m["text"]) for m in history["messages"]] == [
        ("user", "enciende la luz"),
        ("assistant", "Hecho."),
    ]
    assert history["messages"][0]["timestamp"].startswith("2026-11-0")
    # Títulos y mensajes se leen de OpenClaw y van a /admin, nunca al log.
    logged = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert "enciende la luz" not in logged and "Luces del salón" not in logged


async def test_a_push_on_the_old_key_still_reaches_the_client(harness):
    """Un recordatorio que OpenClaw tenía programado en la sesión de antes de M19."""
    legacy = next(r for r in await _conversations(harness) if r["kind"] == "legacy")
    client = await harness.connect()
    assert client.ready["conversation"]["id"] != legacy["id"]  # la tele cae en la del día

    await harness.push("recordatorio", session_key=LEGACY_KEY)
    msgs = await client.collect(lambda m: bool(of_type(m, "turn_end")))
    (start,) = of_type(msgs, "turn_start")
    assert (start["conversation"], start["origin"]) == (legacy["id"], "push")
    assert_turns_paired(msgs)


async def test_titles_degrade_when_openclaw_refuses_the_read(harness, monkeypatch):
    from fogar_gateway.services import conversations

    monkeypatch.setattr(conversations, "TITLES_TTL_S", 0.0)  # sin caché: se ve el fallo ya
    await _conversations(harness)
    harness.openclaw.fail_reads = True
    async with harness.admin() as admin:
        r = await admin.get("/admin/conversations")
        assert r.status_code == 200
        assert [row["title"] for row in r.json()] == [None]
        legacy_id = r.json()[0]["id"]
        r = await admin.get(f"/admin/conversations/{legacy_id}/messages")
    assert (r.status_code, r.json()) == (502, {"detail": "orchestrator_error"})
    assert "store busy" not in r.text
