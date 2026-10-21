"""Upstream ElevenLabs `stream-input` falso (127.0.0.1, puerto 0) para los tests del adaptador.

Implementa el subconjunto que usa el gateway: cabecera `xi-api-key`, mensaje inicial `" "`,
chunks de texto con `flush`, cierre con `{"text":""}`; responde `{"audio": <base64 PCM16>}` y
`{"isFinal": true}`. Los modos se activan por argumentos del constructor.
"""

from __future__ import annotations

import asyncio
import base64
import json
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import websockets

CHARS_PER_CHUNK = 10
CHUNK_BYTES = 960


def pcm_chunk(n: int) -> bytes:
    """Chunk PCM16 determinista (960 bytes = 480 muestras) numerado por `n`."""
    return bytes([(n + i) % 256 if i % 2 == 0 else 0 for i in range(CHUNK_BYTES)])


class FakeElevenLabsTts:
    def __init__(
        self,
        api_key: str = "fake-el-key",
        *,
        handshake_status: int | None = None,
        close_after_s: float | None = None,
        delay_handshake_s: float = 0.0,
        close_midway_after_n_messages: int | None = None,
        close_midway_only_first_connection: bool = True,
        close_code: int = 1011,
        close_reason: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.handshake_status = handshake_status
        self.close_after_s = close_after_s
        self.delay_handshake_s = delay_handshake_s
        self.close_midway_after_n_messages = close_midway_after_n_messages
        self.close_midway_only_first_connection = close_midway_only_first_connection
        self.close_code = close_code
        self.close_reason = close_reason
        self.messages: list[dict] = []  # todos los mensajes recibidos, en orden
        self.connections: list[dict] = []  # una entrada por conexión aceptada
        self.sockets_open = 0
        self._server = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def _process_request(self, connection, request):
        if self.delay_handshake_s:
            await asyncio.sleep(self.delay_handshake_s)
        if self.handshake_status is not None:
            return connection.respond(HTTPStatus(self.handshake_status), "rejected\n")
        return None

    async def _handler(self, ws) -> None:
        req = ws.request
        parts = urlsplit(req.path)
        info = {
            "path": parts.path,
            "query": {k: v[0] for k, v in parse_qs(parts.query).items()},
            "raw_path": req.path,
            "headers": dict(req.headers.items()),
            "messages": [],
        }
        self.connections.append(info)
        self.sockets_open += 1
        try:
            if req.headers.get("xi-api-key") != self.api_key:
                await ws.close(1008, "Invalid API key")
                return
            deadline = None
            if self.close_after_s is not None:
                deadline = asyncio.get_running_loop().time() + self.close_after_s
            pending = 0
            produced = 0
            received = 0
            index = len(self.connections) - 1
            while True:
                timeout = None
                if deadline is not None:
                    timeout = max(0.0, deadline - asyncio.get_running_loop().time())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout)
                except TimeoutError:
                    await ws.close(self.close_code, self.close_reason or "inactivity")
                    return
                msg = json.loads(raw)
                info["messages"].append(msg)
                self.messages.append(msg)
                received += 1
                n = self.close_midway_after_n_messages
                if (
                    n is not None
                    and received >= n
                    and (index == 0 or not self.close_midway_only_first_connection)
                ):
                    await ws.close(self.close_code, "midway")
                    return
                text = msg.get("text", "")
                if text == "" and "flush" not in msg:
                    # fin de la entrada: lo que quede de audio y cierre limpio
                    produced = await self._emit(ws, pending, produced, final=True)
                    await ws.send(json.dumps({"isFinal": True}))
                    await ws.close(1000)
                    return
                pending += len(text.strip())
                if msg.get("flush") or pending >= CHARS_PER_CHUNK:
                    produced = await self._emit(ws, pending, produced)
                    pending = 0
        except websockets.ConnectionClosed:
            pass
        finally:
            self.sockets_open -= 1

    async def _emit(self, ws, pending: int, produced: int, final: bool = False) -> int:
        n = max(1, -(-pending // CHARS_PER_CHUNK)) if pending or final else 0
        for _ in range(n):
            audio = base64.b64encode(pcm_chunk(produced)).decode()
            await ws.send(json.dumps({"audio": audio, "alignment": None}))
            produced += 1
            await asyncio.sleep(0)
        return produced

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handler, "127.0.0.1", 0, process_request=self._process_request
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
