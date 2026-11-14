"""Lecturas de OpenClaw (M19): `OpenClawClient.request` contra un OpenClaw falso real
(`sessions.list`, `chat.history`), solo métodos de lectura, y el aviso de DEFAULT_AGENT (#245)."""

import logging

import pytest

from fogar_gateway.core.config import settings
from fogar_gateway.services.openclaw.client import (
    READ_METHODS,
    OpenClawClient,
    OrchestratorRequestError,
)
from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
from fogar_gateway.services.openclaw.reconnecting import ReconnectingOpenClawClient
from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry
from tests.fakes.openclaw import FakeOpenClawServer


def _client(port: int) -> OpenClawClient:
    turns = TurnRegistry()
    return OpenClawClient("127.0.0.1", port, "tok", turns, FrameDispatcher(turns, ClientRegistry()))


async def test_read_methods_return_their_payload():
    async with FakeOpenClawServer() as server:
        server.add_session("agent:main:fogar-c1", title="Cena", messages=[
            {"role": "user", "content": "hola", "timestamp": 1},
        ])
        client = _client(server.port)
        await client.connect()
        try:
            listed = await client.request("sessions.list", {"includeDerivedTitles": True})
            history = await client.request("chat.history", {"sessionKey": "agent:main:fogar-c1"})
        finally:
            await client.close()
    assert [(r["key"], r["derivedTitle"]) for r in listed["sessions"]] == [("agent:main:fogar-c1", "Cena")]
    assert history["messages"][0]["content"] == "hola"
    assert [m for m, _ in server.reads] == ["sessions.list", "chat.history"]


async def test_no_admin_or_write_method_goes_through_request():
    """M19: el gateway tiene operator.admin pero no llama a ningún método de admin."""
    assert {"sessions.list", "chat.history"} == READ_METHODS
    client = _client(1)
    for method in ("sessions.reset", "sessions.compact", "config.patch", "chat.send", "agents.create"):
        with pytest.raises(ValueError):
            await client.request(method, {})


async def test_a_rejected_read_is_a_stable_code_without_openclaws_text(caplog):
    async with FakeOpenClawServer() as server:
        server.fail_reads = True
        client = _client(server.port)
        await client.connect()
        try:
            with pytest.raises(OrchestratorRequestError) as exc:
                await client.request("chat.history", {"sessionKey": "agent:main:fogar-c1"})
        finally:
            await client.close()
    assert str(exc.value) == "orchestrator_error"
    assert "store busy" not in caplog.text
    assert "code=UNAVAILABLE" in caplog.text


async def test_reads_without_a_connection_are_unavailable():
    client = _client(1)
    with pytest.raises(OrchestratorRequestError) as exc:
        await client.request("sessions.list", {})
    assert str(exc.value) == "orchestrator_unavailable"

    reconnecting = ReconnectingOpenClawClient(client, name="openclaw")
    with pytest.raises(OrchestratorRequestError) as exc:
        await reconnecting.request("sessions.list", {})
    assert str(exc.value) == "orchestrator_unavailable"
    await reconnecting.close()


async def test_default_agent_missing_from_the_roster_warns_at_connect(monkeypatch, caplog):
    monkeypatch.setattr(settings, "DEFAULT_AGENT", "j-assistant")
    async with FakeOpenClawServer() as server:
        client = _client(server.port)
        with caplog.at_level(logging.WARNING):
            await client.connect()  # arranca igual
        await client.close()
    assert "DEFAULT_AGENT=j-assistant is not in OpenClaw's agent roster" in caplog.text


async def test_default_agent_in_the_roster_does_not_warn(monkeypatch, caplog):
    monkeypatch.setattr(settings, "DEFAULT_AGENT", "main")
    async with FakeOpenClawServer() as server:
        client = _client(server.port)
        await client.connect()
        await client.close()
    assert "DEFAULT_AGENT" not in caplog.text
