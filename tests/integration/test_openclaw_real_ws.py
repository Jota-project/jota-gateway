"""Frontera con OpenClaw contra un servidor `websockets` REAL (issue #213, M6a).

Los dobles de `SmartFakeWS` reprodujeron el diseño del cliente y ocultaron bugs
(G-03/G-04/G-05). Aquí el servidor es un `websockets.serve` en el puerto 0 y el cliente
es el `OpenClawClient` de producción, con dos sesiones concurrentes sobre la misma
conexión multiplexada.
"""

import asyncio
import json
import logging

import pytest
import websockets

from src.services.openclaw.client import OpenClawClient
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.registry import ClientRegistry, TurnRegistry

SK_A = "agent:main:client-a"
SK_B = "agent:main:client-b"
MIB = 1024 * 1024


class FakeOpenClawServer:
    """OpenClaw mínimo y real: handshake v4 + comportamiento de `chat.send` según el texto.

    ok        -> ack started, un delta y final
    hold      -> ack started y espera a `release` antes del final
    accepted  -> ack `ok:true,status:"accepted"` (no es fin de turno), delta y final
    malformed -> ack started, frames basura, luego delta y final
    close     -> cierra la conexión con 1000 sin responder
    big       -> ack started, un delta de `big_bytes` y final
    """

    def __init__(self, hello_payload=None, big_bytes=2 * MIB):
        self.connections = 0
        self.release = asyncio.Event()
        self.big_bytes = big_bytes
        self.hello_payload = hello_payload or {
            "type": "hello-ok",
            "protocol": 4,
            "server": {"version": "2026.6.11", "connId": "c1"},
            "policy": {"tickIntervalMs": 30000, "maxPayload": 25 * MIB},
            "snapshot": {"sessionDefaults": {"defaultAgentId": "main"}},
        }
        self._server = None
        self._tasks: set[asyncio.Task] = set()
        self.port = 0

    async def __aenter__(self):
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0, max_size=64 * MIB)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    @staticmethod
    def _res(req_id, payload, ok=True):
        return json.dumps({"type": "res", "id": req_id, "ok": ok, "payload": payload})

    @staticmethod
    def _chat(sk, state, text=""):
        return json.dumps(
            {
                "type": "event",
                "event": "chat",
                "payload": {"sessionKey": sk, "state": state, "deltaText": text},
            }
        )

    async def _handler(self, ws):
        self.connections += 1
        await ws.send(json.dumps({"type": "event", "event": "connect.challenge", "payload": {}}))
        try:
            async for raw in ws:
                frame = json.loads(raw)
                method, rid, params = frame.get("method"), frame.get("id"), frame.get("params", {})
                if method == "connect":
                    await ws.send(self._res(rid, self.hello_payload))
                elif method == "agents.list":
                    await ws.send(self._res(rid, {"defaultId": "main", "agents": [{"id": "main"}]}))
                elif method == "sessions.subscribe":
                    await ws.send(self._res(rid, {"subscribed": True}))
                elif method == "health":
                    await ws.send(self._res(rid, {"ok": True}))
                elif method == "chat.send":
                    task = asyncio.create_task(self._chat_send(ws, rid, params))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        except websockets.ConnectionClosed:
            pass

    async def _chat_send(self, ws, rid, params):
        sk, text = params["sessionKey"], params["message"]
        try:
            if text == "close":
                await ws.close(1000)
                return
            if text == "accepted":
                await ws.send(self._res(rid, {"status": "accepted"}))
            else:
                await ws.send(self._res(rid, {"status": "started"}))
            if text == "hold":
                await self.release.wait()
            elif text == "malformed":
                await ws.send("this is not json")
                await ws.send("[]")
                await ws.send(json.dumps({"type": "event", "event": "chat", "payload": None}))
                await ws.send(
                    json.dumps({"type": "event", "event": "session.tool", "payload": {"sessionKey": SK_B, "data": None}})
                )
                await ws.send(
                    json.dumps({"type": "event", "event": "agent", "payload": {"sessionKey": SK_B, "data": None}})
                )
                await ws.send(json.dumps({"type": "res", "id": "x", "payload": None}))
            if text == "big":
                await ws.send(self._chat(sk, "delta", "x" * self.big_bytes))
                await ws.send(self._chat(sk, "final"))
            else:
                await ws.send(self._chat(sk, "delta", f"reply-{text}"))
                await ws.send(self._chat(sk, "final"))
        except websockets.ConnectionClosed:
            pass


def make_client(port):
    turns = TurnRegistry()
    dispatcher = FrameDispatcher(turns, ClientRegistry())
    client = OpenClawClient("127.0.0.1", port, "tok", turns, dispatcher)
    return client, turns


async def run_turn(client, sk, text):
    return [e async for e in client.stream_response(text, "u", session_key=sk)]


def tokens(events):
    return "".join(e.content for e in events if e.type == "token")


def errors(events):
    return [e.content for e in events if e.type == "error"]


@pytest.fixture
async def server():
    async with FakeOpenClawServer() as srv:
        yield srv


async def _two_sessions(server, text_a, text_b, release_after=None):
    """B queda en `hold`; A ejecuta `text_a`. Devuelve (events_a, events_b, client, turns)."""
    client, turns = make_client(server.port)
    await client.connect()
    task_b = asyncio.create_task(run_turn(client, SK_B, text_b))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if turns.get_queue_by_session(SK_B) is not None:
            break
    events_a = await asyncio.wait_for(run_turn(client, SK_A, text_a), 5)
    return events_a, task_b, client, turns


# ── G-03 ──────────────────────────────────────────────────────────


async def test_malformed_frames_do_not_affect_concurrent_session(server):
    events_a, task_b, client, turns = await _two_sessions(server, "malformed", "hold")
    try:
        server.release.set()
        events_b = await asyncio.wait_for(task_b, 5)
        assert errors(events_a) == [] and tokens(events_a) == "reply-malformed"
        assert errors(events_b) == [] and tokens(events_b) == "reply-hold"
        assert server.connections == 1
    finally:
        await client.close()


async def test_malformed_frame_warning_does_not_leak_content(server, caplog):
    caplog.set_level(logging.WARNING)
    events_a, task_b, client, _ = await _two_sessions(server, "malformed", "hold")
    try:
        server.release.set()
        await asyncio.wait_for(task_b, 5)
        assert "this is not json" not in caplog.text
        assert any("malformed" in r.getMessage().lower() for r in caplog.records)
    finally:
        await client.close()


# ── G-04 ──────────────────────────────────────────────────────────


async def test_clean_close_errors_pending_turns_fast_and_frees_session_key(server):
    client, turns = make_client(server.port)
    await client.connect()
    task_b = asyncio.create_task(run_turn(client, SK_B, "hold"))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if turns.get_queue_by_session(SK_B) is not None:
            break
    try:
        events_a = await asyncio.wait_for(run_turn(client, SK_A, "close"), 3)
        events_b = await asyncio.wait_for(task_b, 3)
        assert errors(events_a) == ["orchestrator_disconnected"]
        assert errors(events_b) == ["orchestrator_disconnected"]
        assert turns.get_queue_by_session(SK_B) is None
        assert turns.get_queue_by_session(SK_A) is None
    finally:
        server.release.set()
        await client.close()


async def test_listen_errors_use_stable_message_not_raw_exception():
    turns = TurnRegistry()
    client = OpenClawClient("x", 1, "t", turns, FrameDispatcher(turns, ClientRegistry()))

    class Boom:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("secret internal detail")

    client._ws = Boom()
    q = turns.register("r", SK_A)
    await client._listen()
    assert q.get_nowait() == ("error", "orchestrator_disconnected")


# ── G-05 ──────────────────────────────────────────────────────────


async def test_two_mib_frame_does_not_drop_connection_or_other_session(server):
    events_a, task_b, client, _ = await _two_sessions(server, "big", "hold")
    try:
        server.release.set()
        events_b = await asyncio.wait_for(task_b, 5)
        assert errors(events_a) == [] and len(tokens(events_a)) == 2 * MIB
        assert errors(events_b) == [] and tokens(events_b) == "reply-hold"
        assert server.connections == 1
    finally:
        await client.close()


async def test_frame_larger_than_default_follows_announced_max_payload():
    hello = {
        "type": "hello-ok",
        "protocol": 4,
        "server": {"version": "9", "connId": "c"},
        "policy": {"tickIntervalMs": 30000, "maxPayload": 40 * MIB},
        "snapshot": {"sessionDefaults": {"defaultAgentId": "main"}},
    }
    async with FakeOpenClawServer(hello_payload=hello, big_bytes=30 * MIB) as srv:
        client, _ = make_client(srv.port)
        await client.connect()
        try:
            events = await asyncio.wait_for(run_turn(client, SK_A, "big"), 10)
            assert errors(events) == [] and len(tokens(events)) == 30 * MIB
        finally:
            await client.close()


def test_frame_limit_clamps():
    from src.services.openclaw.client import _frame_limit

    assert _frame_limit(25 * MIB) == 25 * MIB
    assert _frame_limit(10**12) == 64 * MIB
    assert _frame_limit(10) == 1 * MIB
    assert _frame_limit(None) == 25 * MIB
    assert _frame_limit("junk") == 25 * MIB


# ── G-47 ──────────────────────────────────────────────────────────


async def test_ok_true_res_other_than_started_does_not_end_turn(server):
    client, _ = make_client(server.port)
    await client.connect()
    try:
        events = await asyncio.wait_for(run_turn(client, SK_A, "accepted"), 5)
        assert tokens(events) == "reply-accepted"
        assert errors(events) == []
    finally:
        await client.close()


# ── G-48 ──────────────────────────────────────────────────────────


async def test_hello_ok_server_and_protocol_are_logged_once(server, caplog):
    caplog.set_level(logging.INFO, logger="src.services.openclaw.client")
    client, _ = make_client(server.port)
    await client.connect()
    try:
        recs = [r.getMessage() for r in caplog.records if "hello-ok" in r.getMessage()]
        assert len(recs) == 1
        assert "2026.6.11" in recs[0] and "protocol=4" in recs[0]
    finally:
        await client.close()
