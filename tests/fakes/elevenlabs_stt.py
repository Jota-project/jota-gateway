"""Upstream ElevenLabs STT realtime falso (127.0.0.1, puerto 0) para los tests del adaptador.

Implementa el subconjunto que usa el gateway: cabecera `xi-api-key` (mala -> cierre 1008
"Invalid API key"), `session_started` al conectar, `input_audio_chunk` con audio base64
PCM16 y `commit`; responde `partial_transcript` / `committed_transcript`. Registra la query,
cada mensaje (con los bytes de audio decodificados, nunca el audio) y marca el audio que no
sea PCM16 válido. Los modos se activan por argumentos del constructor.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import websockets


class FakeElevenLabsStt:
    def __init__(
        self,
        api_key: str = "fake-el-key",
        *,
        on_audio: list[tuple[str, bool]] | None = None,
        on_audio_after_bytes: int = 1,
        on_end: list[tuple[str, bool]] | None = None,
        commit_delay_s: float = 0.0,
        error_type: str | None = None,
        close_after_error: bool = False,
        idle_close_s: float | None = None,
        idle_close_code: int = 1000,
        delay_session_started_s: float = 0.0,
        drop_after_n_messages: int | None = None,
        drop_only_first_connection: bool = True,
        handshake_status: int | None = None,
    ) -> None:
        self.api_key = api_key
        self.on_audio = on_audio or []  # emitido al llegar `on_audio_after_bytes` de audio
        self.on_audio_after_bytes = on_audio_after_bytes
        self.on_end = on_end or []  # emitido en cada `commit: true` (final si `True`)
        self.commit_delay_s = commit_delay_s
        self.error_type = error_type  # mensaje de error ante el primer audio de la conexión
        self.close_after_error = close_after_error
        self.idle_close_s = idle_close_s
        self.idle_close_code = idle_close_code
        self.delay_session_started_s = delay_session_started_s
        self.drop_after_n_messages = drop_after_n_messages
        self.drop_only_first_connection = drop_only_first_connection
        self.handshake_status = handshake_status
        self.messages: list[dict] = []  # todos, en orden (audio como `audio_bytes`)
        self.connections: list[dict] = []
        self.commit_times: list[float] = []
        self.audio_bytes = 0
        self.commits = 0
        self.invalid_audio: list[int] = []  # índices en `messages` con audio no PCM16
        self.sockets_open = 0
        self._server = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def _process_request(self, connection, request):
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
            "audio_bytes": 0,
        }
        self.connections.append(info)
        index = len(self.connections) - 1
        self.sockets_open += 1
        try:
            if req.headers.get("xi-api-key") != self.api_key:
                await ws.close(1008, "Invalid API key")
                return
            if self.delay_session_started_s:
                await asyncio.sleep(self.delay_session_started_s)
            await ws.send(
                json.dumps({"message_type": "session_started", "session_id": f"s{index}"})
            )
            sent_on_audio = False
            errored = False
            received = 0
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), self.idle_close_s)
                except TimeoutError:
                    await ws.close(self.idle_close_code, "idle")
                    return
                msg = json.loads(raw)
                audio_b64 = msg.get("audio_base_64", "")
                rec = {k: v for k, v in msg.items() if k != "audio_base_64"}
                try:
                    pcm = base64.b64decode(audio_b64, validate=True)
                except (binascii.Error, ValueError):
                    pcm = b""
                    self.invalid_audio.append(len(self.messages))
                else:
                    if len(pcm) % 2:
                        self.invalid_audio.append(len(self.messages))
                rec["audio_bytes"] = len(pcm)
                self.messages.append(rec)
                info["messages"].append(rec)
                info["audio_bytes"] += len(pcm)
                self.audio_bytes += len(pcm)
                received += 1
                n = self.drop_after_n_messages
                if n is not None and received >= n and (
                    index == 0 or not self.drop_only_first_connection
                ):
                    await ws.close(1011, "drop")
                    return
                if self.error_type and not errored and pcm:
                    errored = True
                    await ws.send(json.dumps({"message_type": self.error_type, "error": "x"}))
                    if self.close_after_error:
                        await ws.close(1011, "error")
                        return
                if not sent_on_audio and info["audio_bytes"] >= self.on_audio_after_bytes:
                    sent_on_audio = True
                    for text, final in self.on_audio:
                        await self._emit(ws, text, final)
                if msg.get("commit"):
                    self.commits += 1
                    self.commit_times.append(time.monotonic())
                    if self.commit_delay_s:
                        await asyncio.sleep(self.commit_delay_s)
                    for text, final in self.on_end:
                        await self._emit(ws, text, final)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.sockets_open -= 1

    @staticmethod
    async def _emit(ws, text: str, final: bool) -> None:
        kind = "committed_transcript" if final else "partial_transcript"
        await ws.send(json.dumps({"message_type": kind, "text": text}))

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handler, "127.0.0.1", 0, process_request=self._process_request
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
