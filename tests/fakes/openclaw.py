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
    agent_error -> ack started, `agent` phase error y `chat` state error con `errorMessage`
                 (el agente falla tras aceptar el turno; 2026.10.5, issue #300)
    wait*     -> ack started y se queda en cola hasta `finish_next()` (o `release`); entonces
                 emite su respuesta como una ejecución del agente: agent start, delta
                 `reply-<texto>`, final, agent end. Ignora `chat.abort`, como un run cuya
                 respuesta ya iba en camino cuando llegó el abort.
    push(sk, text, end=True) -> agent start, chat delta, chat final y agent end, sin
                 `chat.send`; con `end=False` el push queda abierto hasta `push_end(sk)`,
                 `push_fail(sk)` o un `chat.abort`.
    chat.abort -> si hay un push abierto en esa `sessionKey`: chat `aborted`, agent end y
                 `res {ok, aborted: true, runIds}`, en el orden del real (6g); si no, solo
                 `res {aborted: false}`. Tras el abort, `push_end`/`push_fail` no emiten nada.

    Cada `chat.send` y cada push tienen su `runId` (en el ack y en sus eventos), como el real.

    `startup_pending=n`: los `n` primeros `connect` reciben el `UNAVAILABLE`/`startup-sidecars`
    de OpenClaw 2026.11.1 arrancando (`retryAfterMs = startup_retry_after_ms`) y cierre 1013.
    """

    def __init__(self, hello_payload=None, big_bytes=2 * MIB, startup_pending=0, startup_retry_after_ms=50):
        self.connections = 0
        self.release = asyncio.Event()
        self.big_bytes = big_bytes
        self.startup_pending = startup_pending
        self.startup_retry_after_ms = startup_retry_after_ms
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
        self._runs = 0
        self._held: list[tuple[asyncio.Event, asyncio.Event]] = []  # (seguir, terminado)
        self._open_pushes: dict[str, str] = {}  # sessionKey -> runId del push abierto

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
    def _chat(sk, state, text="", run_id=None):
        payload = {"sessionKey": sk, "state": state, "deltaText": text}
        if run_id is not None:
            payload["runId"] = run_id
        return json.dumps({"type": "event", "event": "chat", "payload": payload})

    @staticmethod
    def _chat_error(sk, run_id):
        payload = {"sessionKey": sk, "runId": run_id, "state": "error", "errorMessage": "No API key found"}
        return json.dumps({"type": "event", "event": "chat", "payload": payload})

    @staticmethod
    def _agent(sk, phase, run_id):
        payload = {"sessionKey": sk, "runId": run_id, "data": {"phase": phase}}
        return json.dumps({"type": "event", "event": "agent", "payload": payload})

    def _new_run(self) -> str:
        self._runs += 1
        return f"run-{self._runs}"

    async def push(self, sk, text, end=True):
        """Turno push: lo inicia OpenClaw, sin `chat.send`. Emite en las conexiones abiertas."""
        if not self._conns:
            raise RuntimeError("no hay conexión abierta para el push")
        run_id = self._new_run()
        for ws in list(self._conns):
            await ws.send(self._agent(sk, "start", run_id))
            await ws.send(self._chat(sk, "delta", text, run_id))
        self._open_pushes[sk] = run_id
        if end:
            await self.push_end(sk)

    async def push_end(self, sk):
        """Cierra el push abierto de `sk` (chat final y agent end)."""
        run_id = self._open_pushes.pop(sk, None)
        if run_id is None:  # ya abortado: el real no emite nada más
            return
        for ws in list(self._conns):
            await ws.send(self._chat(sk, "final", run_id=run_id))
            await ws.send(self._agent(sk, "end", run_id))

    async def push_fail(self, sk):
        """El push abierto de `sk` falla: agent error y chat error, sin final ni end."""
        run_id = self._open_pushes.pop(sk, None)
        if run_id is None:
            return
        for ws in list(self._conns):
            await ws.send(self._agent(sk, "error", run_id))
            await ws.send(self._chat_error(sk, run_id))

    async def wait_held(self, n, timeout=5.0):
        """Espera a que haya `n` ejecuciones `wait*` en cola (su `chat.send` ya llegó)."""
        async with asyncio.timeout(timeout):
            while len(self._held) < n:
                await asyncio.sleep(0.005)

    async def finish_next(self):
        """Deja terminar la ejecución `wait*` más antigua y espera a que salga su respuesta."""
        go, done = self._held.pop(0)
        go.set()
        await done.wait()

    async def _until_go(self, go):
        waits = {asyncio.ensure_future(go.wait()), asyncio.ensure_future(self.release.wait())}
        _, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        for w in pending:
            w.cancel()

    async def _handler(self, ws):
        self.connections += 1
        self._conns.add(ws)
        await ws.send(json.dumps({"type": "event", "event": "connect.challenge", "payload": {}}))
        try:
            async for raw in ws:
                frame = json.loads(raw)
                method, rid, params = frame.get("method"), frame.get("id"), frame.get("params", {})
                if method == "connect" and self.startup_pending > 0:
                    self.startup_pending -= 1
                    # Forma real: rejectGatewayStartupConnect (dist/message-handler-*.mjs).
                    error = {
                        "code": "UNAVAILABLE",
                        "message": "gateway starting; retry shortly",
                        "retryable": True,
                        "retryAfterMs": self.startup_retry_after_ms,
                        "details": {"reason": "startup-sidecars"},
                    }
                    await ws.send(json.dumps({"type": "res", "id": rid, "ok": False, "error": error}))
                    await ws.close(1013, "gateway starting")
                elif method == "connect":
                    await ws.send(self._res(rid, self.hello_payload))
                elif method == "agents.list":
                    await ws.send(self._res(rid, {"defaultId": "main", "agents": [{"id": "main"}]}))
                elif method == "sessions.subscribe":
                    await ws.send(self._res(rid, {"subscribed": True}))
                elif method == "health":
                    await ws.send(self._res(rid, {"ok": True}))
                elif method == "chat.abort":
                    await self._chat_abort(ws, rid, params["sessionKey"])
                elif method == "chat.send":
                    task = asyncio.create_task(self._chat_send(ws, rid, params))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._conns.discard(ws)

    async def _chat_abort(self, ws, rid, sk):
        run_id = self._open_pushes.pop(sk, None)
        if run_id is None:
            await ws.send(self._res(rid, {"ok": True, "aborted": False, "runIds": []}))
            return
        await ws.send(self._chat(sk, "aborted", run_id=run_id))
        await ws.send(self._agent(sk, "end", run_id))
        await ws.send(self._res(rid, {"ok": True, "aborted": True, "runIds": [run_id]}))

    async def _chat_send(self, ws, rid, params):
        sk, text = params["sessionKey"], params["message"]
        run_id = self._new_run()
        try:
            if text == "close":
                await ws.close(1000)
                return
            if text == "fail":
                error = {"code": "UNAVAILABLE", "message": "provider exploded"}
                await ws.send(json.dumps({"type": "res", "id": rid, "ok": False, "error": error}))
                return
            if text == "accepted":
                await ws.send(self._res(rid, {"runId": run_id, "status": "accepted"}))
            else:
                await ws.send(self._res(rid, {"runId": run_id, "status": "started"}))
            if text.startswith("wait"):
                go, done = asyncio.Event(), asyncio.Event()
                self._held.append((go, done))
                try:
                    await self._until_go(go)
                    await ws.send(self._agent(sk, "start", run_id))
                    await ws.send(self._chat(sk, "delta", f"reply-{text}", run_id))
                    await ws.send(self._chat(sk, "final", run_id=run_id))
                    await ws.send(self._agent(sk, "end", run_id))
                finally:
                    done.set()
                return
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
            if text == "agent_error":
                await ws.send(self._agent(sk, "error", run_id))
                await ws.send(self._chat_error(sk, run_id))
                return
            if text == "drop":
                await ws.send(self._chat(sk, "delta", "reply-drop", run_id))
                await ws.close(1011)
                return
            if text == "huge":
                await ws.send(self._chat(sk, "delta", "SECRETCONTENT" * (2 * MIB), run_id))
                return
            if text == "big":
                await ws.send(self._chat(sk, "delta", "x" * self.big_bytes, run_id))
                await ws.send(self._chat(sk, "final", run_id=run_id))
            else:
                await ws.send(self._chat(sk, "delta", f"reply-{text}", run_id))
                await ws.send(self._chat(sk, "final", run_id=run_id))
        except websockets.ConnectionClosed:
            pass
