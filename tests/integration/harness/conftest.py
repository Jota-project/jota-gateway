"""Arnés e2e (M7a): el gateway real (uvicorn, app completa y lifespan) contra upstreams
falsos, conducido como lo haría un cliente. Los tests solo miran lo que recibe el cliente.

Dos loops: uvicorn corre en un hilo con el suyo; los falsos, en el del test. Nada aquí
bloquea el loop del test (esperas con `asyncio.sleep` / `to_thread`).
"""

import asyncio
import json
import threading
import time
from collections.abc import Callable

import httpx
import pytest
import uvicorn
import websockets

from fogar_gateway.core.config import settings
from fogar_gateway.main import app
from fogar_gateway.services.conversations import session_key_for
from tests.fakes.fogar_upstreams import FakeTranscriber, FakeTts
from tests.fakes.openclaw import FakeOpenClawServer
from tests.integration.conftest import ADMIN_TOKEN, VALID_KEY

Message = dict | bytes


class HarnessClient:
    """Un cliente WS real: guarda todo lo recibido (JSON como dict, audio como bytes)."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.ready: dict = {}
        self.messages: list[Message] = []
        self.raw_texts: list[str] = []  # texto tal cual llegó, para buscar fugas

    async def send(self, payload: dict) -> None:
        await self.ws.send(json.dumps(payload))

    async def send_audio(self, frame: bytes) -> None:
        await self.ws.send(frame)

    async def collect(
        self, until: Callable[[list[Message]], bool] | None = None, timeout: float = 5.0
    ) -> list[Message]:
        """Recibe hasta que `until(self.messages)` se cumpla (o `timeout`; sin `until`,
        recoge durante todo `timeout`). Devuelve todos los mensajes recibidos."""
        deadline = time.monotonic() + timeout
        while until is None or not until(self.messages):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if until is None:
                    return self.messages
                raise AssertionError(f"timeout; recibido: {describe(self.messages)}")
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            except TimeoutError:
                continue
            if isinstance(raw, bytes):
                self.messages.append(raw)
            else:
                self.raw_texts.append(raw)
                self.messages.append(json.loads(raw))
        return self.messages


class Harness:
    def admin(self) -> httpx.AsyncClient:
        """Cliente HTTP de `/admin/*` del gateway, con el X-Admin-Token de los tests."""
        return httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{self.port}", headers={"x-admin-token": ADMIN_TOKEN}
        )

    def __init__(self, port, openclaw, transcriber, tts) -> None:
        self.port = port
        self.openclaw: FakeOpenClawServer = openclaw
        self.transcriber: FakeTranscriber = transcriber
        self.tts: FakeTts = tts
        self.clients: list[HarnessClient] = []
        # El uvicorn del gateway y su hilo, para apagarlo desde un test.
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    async def open(
        self,
        input_mode: str = "text",
        output_mode: tuple[str, ...] = ("text",),
        handshake: dict | None = None,
        **ws_kwargs,
    ) -> HarnessClient:
        """Abre `/ws/stream` y manda el handshake (con `handshake` encima: `conversation`,
        `admin_token`…), sin esperar a `ready`. `ws_kwargs` va a `websockets.connect`."""
        ws = await websockets.connect(f"ws://127.0.0.1:{self.port}/ws/stream", **ws_kwargs)
        client = HarnessClient(ws)
        self.clients.append(client)
        payload = {"client_key": VALID_KEY, "input_mode": input_mode, "output_mode": list(output_mode)}
        payload.update(handshake or {})
        await client.send({k: v for k, v in payload.items() if v is not None})
        return client

    async def connect(
        self,
        input_mode: str = "text",
        output_mode: tuple[str, ...] = ("text",),
        handshake: dict | None = None,
        **ws_kwargs,
    ) -> HarnessClient:
        """Como `open`, y espera `ready` (admite `status` antes)."""
        client = await self.open(input_mode, output_mode, handshake, **ws_kwargs)
        await client.collect(lambda m: bool(of_type(m, "ready")))
        client.ready = of_type(client.messages, "ready")[0]
        return client

    def session_key(self, client: HarnessClient | None = None) -> str:
        """La sessionKey de la conversación de `client` (el último conectado si no se dice)."""
        client = client or self.clients[-1]
        conversation = client.ready["conversation"]
        return session_key_for(conversation["agent"], conversation["id"])

    async def push(
        self, text: str, end: bool = True, session_key: str | None = None, progress: bool = False
    ) -> None:
        """El orquestador inicia un turno por su cuenta en la conversación del último cliente
        conectado, o en `session_key` (con `end=False` lo deja abierto hasta `push_end`; con
        `progress`, con fases de arranque y una herramienta antes del texto)."""
        await self.openclaw.push(session_key or self.session_key(), text, end=end, progress=progress)

    async def push_end(self, session_key: str | None = None) -> None:
        await self.openclaw.push_end(session_key or self.session_key())

    async def push_fail(self, session_key: str | None = None) -> None:
        await self.openclaw.push_fail(session_key or self.session_key())


def of_type(messages: list[Message], kind: str) -> list[dict]:
    return [m for m in messages if isinstance(m, dict) and m.get("type") == kind]


def audio(messages: list[Message]) -> list[bytes]:
    return [m for m in messages if isinstance(m, bytes)]


def describe(messages: list[Message]) -> list[str]:
    return [m.get("type", "?") if isinstance(m, dict) else f"<{len(m)} bytes>" for m in messages]


def interrupted(messages: list[Message]) -> list[dict]:
    """Los `turn_end` de un barge-in (M19: `reason: "interrupted"`, sin mensaje aparte)."""
    return [m for m in of_type(messages, "turn_end") if m.get("reason") == "interrupted"]


def turn_ended(messages: list[Message]) -> bool:
    """Todo `turn_start` recibido tiene ya su `turn_end` (y hubo al menos uno)."""
    starts = {m["turn_id"] for m in of_type(messages, "turn_start")}
    ends = {m["turn_id"] for m in of_type(messages, "turn_end")}
    return bool(starts) and starts <= ends


def assert_turns_paired(messages: list[Message]) -> None:
    """Cada `turn_start` tiene exactamente un `turn_end` con el mismo `turn_id`, después."""
    open_turns: set[str] = set()
    started: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("type") == "turn_start":
            tid = m["turn_id"]
            assert tid not in started, f"turn_start repetido: {tid}"
            assert m["turn_seq"] == int(tid.removeprefix("t-")) & 0xFFFF
            started.append(tid)
            open_turns.add(tid)
        elif m.get("type") == "turn_end":
            tid = m["turn_id"]
            assert tid in open_turns, f"turn_end sin turn_start abierto: {tid}"
            open_turns.discard(tid)
    assert not open_turns, f"turnos sin turn_end: {sorted(open_turns)}"


@pytest.fixture
def openclaw_seed() -> dict[str, dict]:
    """Sesiones que OpenClaw ya tiene al arrancar el gateway (`add_session`: clave → kwargs).
    Un módulo de tests lo redefine para sembrarlas (p. ej. las de antes de M19)."""
    return {}


@pytest.fixture
async def harness(seed_client, monkeypatch, openclaw_seed):
    transcriber, tts = FakeTranscriber(), FakeTts()
    await transcriber.start()
    await tts.start()
    try:
        async with FakeOpenClawServer() as openclaw:
            for key, kwargs in openclaw_seed.items():
                openclaw.add_session(key, **kwargs)
            # Antes de arrancar: el lifespan construye OpenClaw y los proveedores con estos valores.
            monkeypatch.setattr(settings, "OPENCLAW_HOST", "127.0.0.1")
            monkeypatch.setattr(settings, "OPENCLAW_PORT", openclaw.port)
            monkeypatch.setattr(settings, "TRANSCRIBER_WS_URL", f"127.0.0.1:{transcriber.port}")
            monkeypatch.setattr(settings, "TTS_WS_URL", f"127.0.0.1:{tts.port}")
            monkeypatch.setattr(settings, "STT_PROVIDER", "fogar")
            monkeypatch.setattr(settings, "TTS_PROVIDER", "fogar")

            config = uvicorn.Config(
                app, host="127.0.0.1", port=0, log_level="warning", loop="asyncio", lifespan="on"
            )
            srv = uvicorn.Server(config)
            thread = threading.Thread(target=srv.run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 10
            while not srv.started:
                if time.monotonic() > deadline or not thread.is_alive():
                    srv.should_exit = True
                    raise RuntimeError("uvicorn no arrancó")
                await asyncio.sleep(0.01)  # el loop del test sigue atendiendo a los falsos

            h = Harness(srv.servers[0].sockets[0].getsockname()[1], openclaw, transcriber, tts)
            h.server, h.thread = srv, thread
            try:
                yield h
            finally:
                for c in h.clients:
                    await c.ws.close()
                openclaw.release.set()
                srv.should_exit = True
                await asyncio.to_thread(thread.join, 15)
                # un hilo vivo seguiría corriendo con los settings y la BD ya restaurados
                assert not thread.is_alive(), "el gateway no se paró en 15 s"
    finally:
        await transcriber.stop()
        await tts.stop()
