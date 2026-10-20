"""Upstreams jota falsos (transcriber y TTS) en el puerto 0, para la batería de contrato.

Hablan el protocolo real mínimo: transcriber `config` -> `ready`, audio binario,
`transcription{text,is_final}`, `end`; TTS `auth` -> `auth_ok`, `token`*, `end` ->
binarios PCM16 + `done`.
"""

from __future__ import annotations

import asyncio
import json

import websockets


class FakeTranscriber:
    def __init__(self) -> None:
        self.on_audio: list[tuple[str, bool]] = []  # emitido tras el primer frame de audio
        self.on_end: list[tuple[str, bool]] = []  # emitido al recibir {"type":"end"}
        self.configs: list[dict] = []
        self.audio_frames = 0
        self._server = None
        self.port = 0

    async def _handler(self, ws) -> None:
        cfg = json.loads(await ws.recv())
        self.configs.append(cfg)
        await ws.send(json.dumps({"type": "ready", "protocol_version": 1, "session_id": "s"}))
        sent_audio = False
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    self.audio_frames += 1
                    if not sent_audio:
                        sent_audio = True
                        for text, final in self.on_audio:
                            await ws.send(
                                json.dumps({"type": "transcription", "text": text, "is_final": final})
                            )
                elif json.loads(msg).get("type") == "end":
                    for text, final in self.on_end:
                        await ws.send(
                            json.dumps({"type": "transcription", "text": text, "is_final": final})
                        )
        except websockets.ConnectionClosed:
            pass

    async def start(self) -> None:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()


class FakeTts:
    def __init__(self, chunk: bytes = b"\x01\x00" * 240, chunks_per_reply: int = 3) -> None:
        self.chunk = chunk
        self.chunks_per_reply = chunks_per_reply
        self.tokens: list[str] = []
        self.auths: list[dict] = []
        self._server = None
        self.port = 0

    async def _handler(self, ws) -> None:
        self.auths.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "auth_ok"}))
        try:
            async for msg in ws:
                data = json.loads(msg)
                if data.get("type") == "token":
                    self.tokens.append(data["text"])
                elif data.get("type") == "end":
                    for _ in range(self.chunks_per_reply):
                        await ws.send(self.chunk)
                        await asyncio.sleep(0)
                    await ws.send(json.dumps({"type": "done"}))
        except websockets.ConnectionClosed:
            pass

    async def start(self) -> None:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
