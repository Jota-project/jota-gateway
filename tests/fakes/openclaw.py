"""OpenClaw falso con un servidor `websockets` real (handshake v4), compartido por los tests."""

import asyncio
import json

import websockets

MIB = 1024 * 1024
_SK_B = "agent:main:client-b"  # sessionKey de los frames basura de `malformed`


class FakeOpenClawServer:
    """OpenClaw mínimo y real: handshake v4 + comportamiento de `chat.send` según el texto.

    ok        -> ack started, un delta y final
    hold      -> ack started y espera a `release` antes del final
    accepted  -> ack `ok:true,status:"accepted"` (no es fin de turno), delta y final
    malformed -> ack started, frames basura, luego delta y final
    close     -> cierra la conexión con 1000 sin responder
    huge      -> un frame de ~26 MiB (supera el límite de 25 MiB: cierre 1009)
    big       -> ack started, un delta de `big_bytes` y final
    fail      -> `res` ok:false con `error.code` (turno de error: orchestrator_error)
    drop      -> ack started, un delta y cierra la conexión con 1011 (caída a mitad de turno)
    push(sk, text) -> agent start, chat delta, chat final, agent end, sin `chat.send`
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
        self._conns: set = set()
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

    async def push(self, sk, text):
        """Turno push: lo inicia OpenClaw, sin `chat.send`. Emite en las conexiones abiertas."""
        if not self._conns:
            raise RuntimeError("no hay conexión abierta para el push")
        agent = lambda phase: json.dumps(  # noqa: E731
            {"type": "event", "event": "agent", "payload": {"sessionKey": sk, "data": {"phase": phase}}}
        )
        for ws in list(self._conns):
            await ws.send(agent("start"))
            await ws.send(self._chat(sk, "delta", text))
            await ws.send(self._chat(sk, "final"))
            await ws.send(agent("end"))

    async def _handler(self, ws):
        self.connections += 1
        self._conns.add(ws)
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
        finally:
            self._conns.discard(ws)

    async def _chat_send(self, ws, rid, params):
        sk, text = params["sessionKey"], params["message"]
        try:
            if text == "close":
                await ws.close(1000)
                return
            if text == "fail":
                error = {"code": "UNAVAILABLE", "message": "provider exploded"}
                await ws.send(json.dumps({"type": "res", "id": rid, "ok": False, "error": error}))
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
                    json.dumps({"type": "event", "event": "session.tool", "payload": {"sessionKey": _SK_B, "data": None}})
                )
                await ws.send(
                    json.dumps({"type": "event", "event": "agent", "payload": {"sessionKey": _SK_B, "data": None}})
                )
                await ws.send(json.dumps({"type": "res", "id": "x", "payload": None}))
            if text == "drop":
                await ws.send(self._chat(sk, "delta", "reply-drop"))
                await ws.close(1011)
                return
            if text == "huge":
                await ws.send(self._chat(sk, "delta", "SECRETCONTENT" * (2 * MIB)))
                return
            if text == "big":
                await ws.send(self._chat(sk, "delta", "x" * self.big_bytes))
                await ws.send(self._chat(sk, "final"))
            else:
                await ws.send(self._chat(sk, "delta", f"reply-{text}"))
                await ws.send(self._chat(sk, "final"))
        except websockets.ConnectionClosed:
            pass
