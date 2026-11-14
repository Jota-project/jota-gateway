"""OpenClaw falso con un servidor `websockets` real (handshake v4), compartido por los tests."""

import asyncio
import base64
import hashlib
import json
import secrets
import time
from http import HTTPStatus

import websockets

MIB = 1024 * 1024
_SK_B = "agent:main:client-b"  # sessionKey de los frames basura de `malformed`
# Prefijo DER (SPKI) de una clave pública Ed25519 en bruto: `createEd25519PublicKey` del real.
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
_DEVICE_SIGNATURE_SKEW_MS = 120_000  # DEVICE_SIGNATURE_SKEW_MS del real (2026.10.5)
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _lower_ascii(value) -> str:
    return value.strip().translate(_ASCII_LOWER) if isinstance(value, str) else ""


def server_v3_payload(params: dict, device: dict) -> str:
    """El payload v3 tal como lo reconstruye el servidor (`resolveDeviceSignaturePayloadVersion`),
    escrito aparte del cliente a propósito: así el test comprueba al cliente."""
    auth = params.get("auth") or {}
    token = next(
        (auth[k] for k in ("token", "deviceToken", "bootstrapToken") if auth.get(k) is not None), None
    )
    client = params.get("client") or {}
    return "|".join([
        "v3", device["id"], client.get("id", ""), client.get("mode", ""), params.get("role", ""),
        ",".join(params.get("scopes") or []), str(device["signedAt"]), token or "",
        device["nonce"].strip(), _lower_ascii(client.get("platform")),
        _lower_ascii(client.get("deviceFamily")),
    ])


def verify_device(params: dict, nonce: str) -> str | None:
    """`verifyGatewayConnectDeviceProof` del real: None si vale, si no su `reason`."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    device = params["device"]
    try:
        raw = _b64url_decode(device["publicKey"])
    except (KeyError, ValueError, TypeError):
        return "device-public-key"
    if hashlib.sha256(raw).hexdigest() != device.get("id"):
        return "device-id-mismatch"
    signed_at = device.get("signedAt")
    if not isinstance(signed_at, int) or abs(time.time() * 1000 - signed_at) > _DEVICE_SIGNATURE_SKEW_MS:
        return "device-signature-stale"
    provided = device.get("nonce").strip() if isinstance(device.get("nonce"), str) else ""
    if not provided:
        return "device-nonce-missing"
    if provided != nonce:
        return "device-nonce-mismatch"
    try:
        key = load_der_public_key(_ED25519_SPKI_PREFIX + raw)
        key.verify(_b64url_decode(device["signature"]), server_v3_payload(params, device).encode())
    except (InvalidSignature, ValueError, TypeError, KeyError):
        return "device-signature"
    return None


_DEVICE_AUTH_CODES = {
    "device-id-mismatch": "DEVICE_AUTH_DEVICE_ID_MISMATCH",
    "device-signature-stale": "DEVICE_AUTH_SIGNATURE_EXPIRED",
    "device-nonce-missing": "DEVICE_AUTH_NONCE_REQUIRED",
    "device-nonce-mismatch": "DEVICE_AUTH_NONCE_MISMATCH",
    "device-signature": "DEVICE_AUTH_SIGNATURE_INVALID",
    "device-public-key": "DEVICE_AUTH_PUBLIC_KEY_INVALID",
}


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
    progress  -> ack started, las fases de arranque de `chat` `state:"status"` (con una
                 repetida), una herramienta `ha_call_service` (start con `args` y result con
                 contenido, los dos con `SECRET-…`), delta y final (#254)
    progress_weird -> como `progress`, con una fase desconocida y un nombre de herramienta que
                 no es un identificador
    progress_late -> ack started, delta y final, y después una fase y una herramienta del
                 mismo run (nada de eso debe llegar al cliente)
    progress_hold -> ack started, una fase y una herramienta `slow_tool` (start), y espera a
                 `release` antes de delta y final
    progress_burst -> ack started, `burst_tools` herramientas seguidas (start y result), una
                 pausa de `burst_pause_s` y delta y final (el ritmo de `turn_progress`)
    wait*     -> ack started y se queda en cola hasta `finish_next()` (o `release`); entonces
                 emite su respuesta como una ejecución del agente: agent start, delta
                 `reply-<texto>`, final, agent end. Ignora `chat.abort`, como un run cuya
                 respuesta ya iba en camino cuando llegó el abort. `waitprogress*` emite
                 además, tras su agent start, una fase y una herramienta `late_tool`.
    push(sk, text, end=True, progress=False) -> agent start, chat delta, chat final y agent
                 end, sin `chat.send`; con `end=False` el push queda abierto hasta
                 `push_end(sk)`, `push_fail(sk)` o un `chat.abort`. Con `progress=True`,
                 entre el agent start y el delta: fases de arranque y una herramienta
                 `push_tool`; con `progress="early"`, dos fases antes del agent start.
    agent_noise(sk, run_id=None) -> `agent` de streams `item`/`tool` con `data.phase`
                 start/end (cada `agent` lleva su `stream`, como el real).
    chat.abort -> si hay un push abierto en esa `sessionKey`: chat `aborted`, agent end y
                 `res {ok, aborted: true, runIds}`, en el orden del real (6g); si no, solo
                 `res {aborted: false}`. Tras el abort, `push_end`/`push_fail` no emiten nada.

    Cada `chat.send` y cada push tienen su `runId` (en el ack y en sus eventos), como el real.

    Lectura (M19, forma real de 2026.10.5, docs/openclaw-protocol.md «2026-11-14»): cada
    `chat.send` y cada push dejan su sesión en `sessions` (clave → fila de `sessions.list`) con
    sus mensajes (`user` con `content` en texto, `assistant` con partes `text`, `timestamp` en
    ms). `sessions.list` pagina con `limit`/`offset` (`hasMore`, `nextOffset`) y pone
    `derivedTitle` solo con `includeDerivedTitles`; `chat.history` da los `limit` últimos.
    `add_session(key, title, messages)` siembra una (p. ej. una de antes de M19);
    `fail_reads=True` rechaza las lecturas (`UNAVAILABLE`); `reads` guarda cada una.

    `startup_pending=n`: los `n` primeros `connect` reciben el `UNAVAILABLE`/`startup-sidecars`
    de OpenClaw 2026.11.1 arrancando (`retryAfterMs = startup_retry_after_ms`) y cierre 1013.

    `token`: si no es None, un `connect` con otro `auth.token` (y sin un token de dispositivo
    válido) recibe el rechazo real (`INVALID_REQUEST`, `details.code = AUTH_TOKEN_MISMATCH`,
    `authReason = token_mismatch`) y cierre 1008.

    Identidad de dispositivo (2026.10.5, docs/openclaw-protocol.md «2026-11-13»): el reto lleva
    `nonce` y `ts` (salvo `challenge_nonce=False`, como los fakes de antes); un `device` se
    valida como el real (deviceId, plazo, nonce y firma v3; si no, `DEVICE_AUTH_*` y 1008).
    `remote=False` (por defecto): el camino local de gateway-client/backend, sin emparejar y con
    los permisos pedidos. `remote=True`: exige identidad; un dispositivo sin emparejar recibe
    `NOT_PAIRED`/`PAIRING_REQUIRED` con `requestId` hasta `approve()`; uno emparejado recibe
    `hello-ok.auth.deviceToken`, que luego vale como credencial (`auth.deviceToken`) hasta
    `revoke()` (`AUTH_DEVICE_TOKEN_MISMATCH`). Un dispositivo emparejado recibe su token también
    en local, como el real. `connects` guarda los `params` de cada `connect`.
    `GET /healthz` -> 200 `{"ok":true,"status":"live"}`, como el real (sin versión).
    `port`: 0 = uno libre; el CI del instalador lo fija en el 18789 de OpenClaw.
    """

    def __init__(
        self, hello_payload=None, big_bytes=2 * MIB, startup_pending=0, startup_retry_after_ms=50, token=None,
        port=0, remote=False, challenge_nonce=True,
    ):
        self.connections = 0
        self.token = token
        self.remote = remote
        self.challenge_nonce = challenge_nonce
        # Lo que contesta `remote` a un connect sin `device` (el real: DEVICE_IDENTITY_REQUIRED).
        self.no_device_code = "DEVICE_IDENTITY_REQUIRED"
        self.connects: list[dict] = []
        self.paired: dict[str, list[str]] = {}  # deviceId -> permisos aprobados
        self.device_tokens: dict[str, tuple[str, list[str]]] = {}  # deviceId -> (token, permisos)
        self.pending: dict[str, tuple[str, list[str]]] = {}  # requestId -> (deviceId, permisos)
        self._requests = 0
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
        self.port = port
        self._runs = 0
        self._held: list[tuple[asyncio.Event, asyncio.Event]] = []  # (seguir, terminado)
        self._open_pushes: dict[str, str] = {}  # sessionKey -> runId del push abierto
        self.sessions: dict[str, dict] = {}  # sessionKey -> fila de sessions.list + "messages"
        self.reads: list[tuple[str, dict]] = []  # (método, params) de cada lectura
        self.fail_reads = False
        self.calls: list[tuple[str, str]] = []  # (chat.send | chat.abort, sessionKey), en orden
        self.burst_tools = 20
        self.burst_pause_s = 0.6

    async def __aenter__(self):
        self._server = await websockets.serve(
            self._handler, "127.0.0.1", self.port, max_size=64 * MIB, process_request=self._healthz
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    @staticmethod
    def _healthz(connection, request):
        if request.path == "/healthz":
            return connection.respond(HTTPStatus.OK, '{"ok":true,"status":"live"}')
        return None

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
    def _status(sk, phase, run_id):
        """`chat` `state:"status"`: una fase de arranque, sin texto (2026.10.5)."""
        payload = {"sessionKey": sk, "runId": run_id, "state": "status", "phase": phase}
        return json.dumps({"type": "event", "event": "chat", "payload": payload})

    @staticmethod
    def _tool(sk, run_id, phase, name, call_id):
        """`session.tool` con la forma real: `args` en start, `result.content` en result."""
        data = {"phase": phase, "name": name, "toolCallId": call_id}
        if phase == "start":
            data["args"] = {"entity_id": "SECRET-ARG"}
        else:
            data["isError"] = False
            data["result"] = {"content": [{"type": "text", "text": "SECRET-RESULT"}]}
        payload = {"sessionKey": sk, "runId": run_id, "data": data}
        return json.dumps({"type": "event", "event": "session.tool", "payload": payload})

    async def _progress_frames(self, ws, sk, run_id, phases, tool, call_id="call-1"):
        for phase in phases:
            await ws.send(self._status(sk, phase, run_id))
        await ws.send(self._tool(sk, run_id, "start", tool, call_id))
        await ws.send(self._tool(sk, run_id, "result", tool, call_id))

    @staticmethod
    def _agent(sk, phase, run_id, stream="lifecycle"):
        """`agent` con su `stream`, como el real: `lifecycle` abre y cierra el run; los demás
        (`item`, `tool`…) también llevan `data.phase` y no abren ni cierran nada."""
        data = {"phase": phase}
        if stream == "item":
            data.update({"itemId": f"item-{run_id}", "kind": "tool", "title": "x"})
        payload = {"sessionKey": sk, "runId": run_id, "stream": stream, "data": data}
        return json.dumps({"type": "event", "event": "agent", "payload": payload})

    async def agent_noise(self, sk, run_id=None):
        """`agent` de streams que no son `lifecycle` (`item` start/end, `tool` start), del
        push abierto en `sk` o de un run nuevo: no deben abrir ni cerrar un push."""
        run_id = run_id or self._open_pushes.get(sk) or self._new_run()
        for ws in list(self._conns):
            await ws.send(self._agent(sk, "start", run_id, stream="item"))
            await ws.send(self._agent(sk, "start", run_id, stream="tool"))
            await ws.send(self._agent(sk, "end", run_id, stream="item"))

    def _new_run(self) -> str:
        self._runs += 1
        return f"run-{self._runs}"

    def add_session(self, key, title=None, messages=None, created_at=None):
        """Una sesión que OpenClaw ya tiene (p. ej. de antes de M19)."""
        now = int(time.time() * 1000)
        row = self.sessions.setdefault(key, {
            "key": key, "agentId": key.split(":")[1], "kind": "direct",
            "createdAt": created_at or now, "updatedAt": now, "messages": [],
        })
        if title is not None:
            row["label"] = title
        row["messages"].extend(messages or [])
        return row

    def _record(self, sk, role, text):
        content = text if role == "user" else [{"type": "text", "text": text}]
        row = self.add_session(sk)
        row["updatedAt"] = int(time.time() * 1000)
        row["messages"].append({"role": role, "content": content, "timestamp": row["updatedAt"]})

    async def _read(self, ws, rid, method, params):
        self.reads.append((method, params))
        if self.fail_reads:
            error = {"code": "UNAVAILABLE", "message": "store busy"}
            await ws.send(json.dumps({"type": "res", "id": rid, "ok": False, "error": error}))
            return
        if method == "sessions.list":
            rows = list(self.sessions.values())
            offset, limit = params.get("offset", 0), params.get("limit", 100)
            page = rows[offset:offset + limit]
            sessions = []
            for row in page:
                out = {k: v for k, v in row.items() if k not in ("messages", "label")}
                if params.get("includeDerivedTitles") and row.get("label"):
                    out["derivedTitle"] = row["label"]
                sessions.append(out)
            more = offset + limit < len(rows)
            payload = {"count": len(sessions), "totalCount": len(rows), "hasMore": more,
                       "nextOffset": offset + limit if more else None, "sessions": sessions}
        else:  # chat.history
            messages = self.sessions.get(params["sessionKey"], {}).get("messages", [])
            limit = params.get("limit", 200)
            payload = {"sessionKey": params["sessionKey"], "messages": messages[-limit:],
                       "hasMore": len(messages) > limit, "totalMessages": len(messages)}
        await ws.send(self._res(rid, payload))

    async def push(self, sk, text, end=True, progress=False):
        """Turno push: lo inicia OpenClaw, sin `chat.send`. Emite en las conexiones abiertas."""
        if not self._conns:
            raise RuntimeError("no hay conexión abierta para el push")
        run_id = self._new_run()
        self._record(sk, "assistant", text)
        for ws in list(self._conns):
            if progress == "early":  # fases de arranque antes del `agent start` (#300, #254)
                await ws.send(self._status(sk, "preparing_workspace", run_id))
                await ws.send(self._status(sk, "preparing_context", run_id))
            await ws.send(self._agent(sk, "start", run_id))
            if progress is True:
                phases = ("preparing_workspace", "starting_model")
                await self._progress_frames(ws, sk, run_id, phases, "push_tool")
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

    def approve(self, request_id=None):
        """`openclaw devices approve <requestId>` (sin id: la última petición)."""
        request_id = request_id or list(self.pending)[-1]
        device_id, scopes = self.pending.pop(request_id)
        self.paired[device_id] = list(scopes)

    def revoke(self, device_id):
        """`openclaw devices remove`: el token de dispositivo deja de valer."""
        self.paired.pop(device_id, None)
        self.device_tokens.pop(device_id, None)

    async def _reject(self, ws, rid, code, message, details):
        error = {"code": code, "message": message, "details": details}
        await ws.send(json.dumps({"type": "res", "id": rid, "ok": False, "error": error}))
        await ws.close(1008, message)

    async def _connect(self, ws, rid, params, nonce):
        """Autenticación y emparejamiento de `connect`, en el orden del real: firma del
        dispositivo, credencial (token compartido o de dispositivo) y emparejamiento."""
        self.connects.append(params)
        auth = params.get("auth") or {}
        device = params.get("device")
        scopes = list(params.get("scopes") or [])
        if device is not None and (reason := verify_device(params, nonce)) is not None:
            await self._reject(ws, rid, "INVALID_REQUEST", "device signature invalid",
                               {"code": _DEVICE_AUTH_CODES[reason], "reason": reason})
            return
        method = "token"
        if self.token is not None and auth.get("token") != self.token:
            candidate = (auth.get("deviceToken") or auth.get("token")) if device else None
            issued = self.device_tokens.get(device["id"]) if device else None
            if candidate and issued and issued[0] == candidate:
                if not set(scopes) <= set(issued[1]):
                    await self._reject(ws, rid, "INVALID_REQUEST", "unauthorized: scope mismatch",
                                       {"code": "AUTH_SCOPE_MISMATCH", "authReason": "scope_mismatch"})
                    return
                method = "device-token"
            elif candidate and auth.get("deviceToken"):
                await self._reject(ws, rid, "INVALID_REQUEST", "unauthorized: device token mismatch",
                                   {"code": "AUTH_DEVICE_TOKEN_MISMATCH",
                                    "authReason": "device_token_mismatch",
                                    "recommendedNextStep": "update_auth_credentials"})
                return
            else:
                # Forma real: sendHandshakeErrorResponse (dist/message-handler-*.mjs).
                await self._reject(ws, rid, "INVALID_REQUEST", "unauthorized: gateway token mismatch",
                                   {"code": "AUTH_TOKEN_MISMATCH", "authReason": "token_mismatch"})
                return
        if self.remote:
            if device is None:
                await self._reject(ws, rid, "NOT_PAIRED", "device identity required",
                                   {"code": self.no_device_code})
                return
            if device["id"] not in self.paired:
                request_id = next((r for r, (d, _) in self.pending.items() if d == device["id"]), None)
                if request_id is None:
                    self._requests += 1
                    request_id = f"req-{self._requests}"
                    self.pending[request_id] = (device["id"], scopes)
                await self._reject(ws, rid, "NOT_PAIRED", "pairing required: device is not approved yet", {
                    "code": "PAIRING_REQUIRED", "reason": "not-paired", "requestId": request_id,
                    "remediationHint": "Approve this device from the pending pairing requests.",
                    "deviceId": device["id"], "requestedRole": "operator", "requestedScopes": scopes,
                })
                return
        device_token = None
        if device is not None and device["id"] in self.paired:
            token, granted = self.device_tokens.get(device["id"], (None, []))
            if token is None or not set(scopes) <= set(granted):
                token = f"devtok-{secrets.token_hex(8)}"
                self.device_tokens[device["id"]] = (token, scopes)
            device_token = token
        payload = dict(self.hello_payload)
        if "auth" not in payload:
            payload["auth"] = {"method": method, "role": "operator", "scopes": scopes}
            if device_token is not None:
                payload["auth"]["deviceToken"] = device_token
        await ws.send(self._res(rid, payload))

    async def _handler(self, ws):
        self.connections += 1
        self._conns.add(ws)
        nonce = secrets.token_hex(16)
        challenge = {"nonce": nonce, "ts": int(time.time() * 1000)} if self.challenge_nonce else {}
        await ws.send(json.dumps({"type": "event", "event": "connect.challenge", "payload": challenge}))
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
                    await self._connect(ws, rid, params, nonce)
                elif method == "agents.list":
                    await ws.send(self._res(rid, {"defaultId": "main", "agents": [{"id": "main"}]}))
                elif method == "sessions.subscribe":
                    await ws.send(self._res(rid, {"subscribed": True}))
                elif method == "health":
                    await ws.send(self._res(rid, {"ok": True}))
                elif method in ("sessions.list", "chat.history"):
                    await self._read(ws, rid, method, params)
                elif method == "chat.abort":
                    self.calls.append((method, params["sessionKey"]))
                    await self._chat_abort(ws, rid, params["sessionKey"])
                elif method == "chat.send":
                    self.calls.append((method, params["sessionKey"]))
                    task = asyncio.create_task(self._chat_send(ws, rid, params))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._conns.discard(ws)

    async def _chat_send_progress(self, ws, sk, run_id, text):
        if text == "progress":
            phases = ("preparing_workspace", "preparing_context", "preparing_context", "starting_model")
            await self._progress_frames(ws, sk, run_id, phases, "ha_call_service")
        elif text == "progress_weird":
            await self._progress_frames(ws, sk, run_id, ("warp_drive",), "bad name; rm -rf /")
        elif text == "progress_hold":
            # Una fase (sale ya) y una herramienta (aplazada por el ritmo); después espera.
            await ws.send(self._status(sk, "preparing_workspace", run_id))
            await ws.send(self._tool(sk, run_id, "start", "slow_tool", "call-1"))
            await self.release.wait()
        elif text == "progress_burst":
            for i in range(self.burst_tools):
                await self._progress_frames(ws, sk, run_id, (), f"tool_{i}", call_id=f"call-{i}")
            await asyncio.sleep(self.burst_pause_s)
        await ws.send(self._chat(sk, "delta", f"reply-{text}", run_id))
        await ws.send(self._chat(sk, "final", run_id=run_id))
        if text == "progress_late":
            await self._progress_frames(ws, sk, run_id, ("starting_model",), "late_tool")

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
        self._record(sk, "user", text)
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
                    if text.startswith("waitprogress"):
                        await self._progress_frames(ws, sk, run_id, ("starting_model",), "late_tool")
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
            if text.startswith("progress"):
                await self._chat_send_progress(ws, sk, run_id, text)
                return
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
                self._record(sk, "assistant", f"reply-{text}")
                await ws.send(self._chat(sk, "delta", f"reply-{text}", run_id))
                await ws.send(self._chat(sk, "final", run_id=run_id))
        except websockets.ConnectionClosed:
            pass


class SendOnlyOpenClawWS:
    """An OpenClaw socket that only records what the client sends (set it as
    `OpenClawClient._ws`, no connect). The test plays the server through the dispatcher in
    the exact order it wants; `on_send(frame)` may answer a request."""

    def __init__(self, on_send=None):
        self.sent: list[dict] = []
        self.on_send = on_send

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent.append(frame)
        if self.on_send is not None:
            self.on_send(frame)

    def methods(self) -> list:
        return [f.get("method") for f in self.sent]

    def ids(self, method: str) -> list[str]:
        return [f["id"] for f in self.sent if f.get("method") == method]
