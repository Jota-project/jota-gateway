"""Errores estables hacia el cliente fuera de `_listen` (issue #228, M6h).

Servidores `websockets` REALES en el puerto 0. En cada caso se mete el marcador
`SECRET-INTERNAL` en el texto interno (excepción, `error` de OpenClaw, motivo de cierre)
y se comprueba que no llega ni al evento que ve el cliente, ni al `last_error` que expone
`/admin`, ni a ningún log (todos los niveles).
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest
import websockets

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.reconnecting import ReconnectingOpenClawClient
from src.services.openclaw.registry import ClientRegistry, TurnRegistry
from src.services.protocol import OrchestratorEvent
from src.services.reconnection import ConnectionState

MARK = "SECRET-INTERNAL"
SK = "agent:main:client-a"


def _res(rid, payload=None, ok=True, error=None):
    frame = {"type": "res", "id": rid, "ok": ok, "payload": payload if payload is not None else {}}
    if error is not None:
        frame["error"] = error
    return json.dumps(frame)


class Server:
    """OpenClaw mínimo. `hello` (str | None) sustituye la respuesta a `connect`;
    `challenge` sustituye el primer frame; `on_chat` decide qué hacer con `chat.send`."""

    def __init__(self, hello=None, challenge=None, on_chat=None, agents_error=None):
        self.agents_error = agents_error
        self.hello = hello
        self.challenge = challenge
        self.on_chat = on_chat
        self._server = None
        self.port = 0

    async def __aenter__(self):
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def _handler(self, ws):
        first = self.challenge or json.dumps(
            {"type": "event", "event": "connect.challenge", "payload": {}}
        )
        await ws.send(first)
        try:
            async for raw in ws:
                frame = json.loads(raw)
                method, rid = frame.get("method"), frame.get("id")
                if method == "connect":
                    if self.hello is not None:
                        await ws.send(self.hello)
                    else:
                        await ws.send(
                            _res(
                                rid,
                                {
                                    "type": "hello-ok",
                                    "protocol": 4,
                                    "server": {"version": "x", "connId": "c1"},
                                    "policy": {"tickIntervalMs": 30000, "maxPayload": 1 << 20},
                                    "snapshot": {"sessionDefaults": {"defaultAgentId": "main"}},
                                },
                            )
                        )
                elif method == "agents.list" and self.agents_error is not None:
                    await ws.send(_res(rid, ok=False, error=self.agents_error))
                elif method == "agents.list":
                    await ws.send(_res(rid, {"defaultId": "main", "agents": [{"id": "main"}]}))
                elif method == "sessions.subscribe":
                    await ws.send(_res(rid, {"subscribed": True}))
                elif method == "chat.send" and self.on_chat:
                    await self.on_chat(ws, rid)
        except websockets.ConnectionClosed:
            pass


def make_client(port):
    turns = TurnRegistry()
    client = OpenClawClient("127.0.0.1", port, "tok", turns, FrameDispatcher(turns, ClientRegistry()))
    return client


async def run_turn(client):
    return [e async for e in client.stream_response("hola", "u", session_key=SK)]


def assert_clean(caplog, *texts):
    # Solo los loggers del gateway (`src.*`): la librería `websockets` registra a DEBUG los
    # frames crudos y no es código nuestro (se deja en su nivel por defecto).
    logs = "\n".join(
        r.getMessage() + (r.exc_text or "") for r in caplog.records if r.name.startswith("src")
    )
    assert MARK not in logs + "".join(str(t) for t in texts)


# ── res con ok:false: texto interno de OpenClaw ───────────────────────────────


async def test_res_ok_false_gives_stable_error_not_openclaw_text(caplog):
    caplog.set_level(logging.DEBUG, logger="src")

    async def on_chat(ws, rid):
        await ws.send(_res(rid, ok=False, error={"code": "X", "message": MARK}))

    async with Server(on_chat=on_chat) as srv:
        client = make_client(srv.port)
        await client.connect()
        try:
            events = await asyncio.wait_for(run_turn(client), 5)
        finally:
            await client.close()
    assert [e.content for e in events if e.type == "error"] == ["orchestrator_error"]
    assert_clean(caplog, events)


# ── fallo de send: la excepción de websockets lleva el motivo del cierre ──────


async def test_send_failure_gives_stable_error_not_exception_text(caplog):
    caplog.set_level(logging.DEBUG, logger="src")

    async def on_chat(ws, rid):
        pass

    async with Server(on_chat=on_chat) as srv:
        client = make_client(srv.port)
        await client.connect()
        try:
            # Cierra el socket por el lado cliente con un motivo que acaba en el texto
            # de la excepción de los envíos posteriores (ConnectionClosed lo incluye).
            await client._ws.close(1000, MARK)
            events = await asyncio.wait_for(run_turn(client), 5)
        finally:
            await client.close()
    assert [e.content for e in events if e.type == "error"] == ["orchestrator_send_failed"]
    assert_clean(caplog, events)


# ── ReconnectingOpenClawClient.stream_response ────────────────────────────────


async def test_reconnecting_stream_exception_gives_stable_error(caplog):
    caplog.set_level(logging.DEBUG, logger="src")
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.gateway_info = None

    async def boom(*a, **k):
        raise ValueError(MARK)
        yield  # pragma: no cover

    inner.stream_response = boom
    roc = ReconnectingOpenClawClient(inner, "test")
    roc._health.state = ConnectionState.CONNECTED
    events = [e async for e in roc.stream_response("hi", "u", session_key=SK)]
    assert events == [OrchestratorEvent(type="error", content="orchestrator_error")]
    assert "ValueError" in caplog.text
    assert_clean(caplog, events)


# ── connect: motivo estable, last_error estable, log sin texto ────────────────


async def _failed_connect(srv_kwargs, caplog):
    caplog.set_level(logging.DEBUG, logger="src")
    async with Server(**srv_kwargs) as srv:
        roc = ReconnectingOpenClawClient(
            make_client(srv.port), "test", initial_backoff=0.01, max_backoff=0.02, max_duration=0.3
        )
        await roc.connect()
        try:
            await asyncio.sleep(0.1)  # deja correr algún reintento del bucle
            return roc.status().last_error
        finally:
            await roc.close()


async def test_handshake_error_response_is_stable(caplog):
    hello = _res("x", ok=False, error={"message": MARK})
    last = await _failed_connect({"hello": hello}, caplog)
    assert last == "orchestrator_handshake_failed"
    assert_clean(caplog)


@pytest.mark.parametrize("payload", [json.dumps(MARK), json.dumps([MARK]), json.dumps(None)])
async def test_non_object_hello_is_stable_handshake_failure(payload, caplog):
    last = await _failed_connect({"hello": payload}, caplog)
    assert last == "orchestrator_handshake_failed"
    assert_clean(caplog)


async def test_unexpected_challenge_frame_is_stable_handshake_failure(caplog):
    challenge = json.dumps({"type": "event", "event": "other", "payload": MARK})
    last = await _failed_connect({"challenge": challenge}, caplog)
    assert last == "orchestrator_handshake_failed"
    assert_clean(caplog)


async def test_non_object_challenge_is_stable_handshake_failure(caplog):
    last = await _failed_connect({"challenge": json.dumps([MARK])}, caplog)
    assert last == "orchestrator_handshake_failed"
    assert_clean(caplog)


async def test_connect_exception_last_error_is_type_name_only(caplog):
    caplog.set_level(logging.DEBUG, logger="src")
    inner = AsyncMock(spec=OpenClawClient)
    inner.on_disconnect = None
    inner.connect.side_effect = OSError(MARK)
    roc = ReconnectingOpenClawClient(
        inner, "test", initial_backoff=0.01, max_backoff=0.02, max_duration=0.1
    )
    await roc.connect()
    try:
        await asyncio.sleep(0.05)
        assert roc.status().last_error == "OSError"
    finally:
        await roc.close()
    assert_clean(caplog)
