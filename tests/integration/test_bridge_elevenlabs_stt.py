"""Sesión de audio real del bridge con el adaptador STT de ElevenLabs contra el upstream falso."""

import asyncio
import math
import struct
import time

from pydantic import SecretStr

from src.core.config import settings
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.bridge import JotaBridge
from src.services.openclaw.registry import ClientRegistry
from src.services.speech.elevenlabs.stt import ElevenLabsSttProvider
from tests.fakes.elevenlabs_stt import FakeElevenLabsStt
from tests.fakes.speech import speech_providers
from tests.unit.test_bridge_m6c_turns import RecWS, _blocking, _tracker


def _speech(samples=1600):
    return struct.pack(f"<{samples}f", *[0.1 * math.sin(i * 0.3) for i in range(samples)])


def _quiet(samples=1600):
    return struct.pack(f"<{samples}f", *([0.0] * samples))


async def _wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condición no cumplida a tiempo")


async def _session(fake):
    from unittest.mock import AsyncMock

    provider = ElevenLabsSttProvider(
        settings.model_copy(
            update={
                "ELEVENLABS_API_KEY": SecretStr("fake-el-key"),
                "ELEVENLABS_BASE_URL": fake.url,
                "STT_AUDIO_GATE": "auto",  # elevenlabs -> puerta de energía
                "ELEVENLABS_STT_DRAIN_S": 0.3,
            }
        )
    )
    ws = RecWS()
    bridge = JotaBridge(
        client=Client(id="u", client_key="k", is_active=True),
        config=ClientConfig(),
        client_ws=ws,
        orchestrator=AsyncMock(),
        speech=speech_providers(stt=provider),
        tracker=_tracker(),
        handshake=Handshake(client_key="k", input_mode="audio", output_mode=["text", "status"]),
        client_registry=ClientRegistry(),
        default_agent="main",
    )
    await bridge.connect_internal_services()
    runner = asyncio.create_task(bridge.run())
    return bridge, ws, runner


async def _stop(bridge, ws, runner):
    await ws.inbox.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(runner, 5)


async def test_audio_session_delivers_partial_and_final_through_the_gate():
    fake = FakeElevenLabsStt(
        api_key="fake-el-key",
        on_audio=[("hola que tal", False)],
        on_end=[("hola que tal estás", True)],
    )
    await fake.start()
    try:
        bridge, ws, runner = await _session(fake)
        for _ in range(5):  # silencio previo: no sale al proveedor
            await ws.inbox.put({"bytes": _quiet()})
        for _ in range(10):
            await ws.inbox.put({"bytes": _speech()})
        for _ in range(17):  # > hangover: cierra el segmento y fuerza el commit
            await ws.inbox.put({"bytes": _quiet()})
        await _wait_for(lambda: ws.of("transcription"))
        assert ws.of("transcription_partial")[0]["text"] == "hola que tal"
        assert ws.of("transcription")[0]["text"] == "hola que tal estás"
        assert fake.commits == 1
        await _stop(bridge, ws, runner)
    finally:
        await fake.stop()


async def test_barge_in_on_partial_still_works():
    fake = FakeElevenLabsStt(api_key="fake-el-key", on_audio=[("espera un momento", False)])
    await fake.start()
    try:
        bridge, ws, runner = await _session(fake)
        started = asyncio.Event()
        bridge.orchestrator.stream_response = _blocking(started)
        await bridge._start_turn("x")
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(5):
            await ws.inbox.put({"bytes": _speech()})
        await _wait_for(lambda: ws.of("interrupted"))
        assert len(ws.of("turn_end")) == 1
        await _stop(bridge, ws, runner)
    finally:
        await fake.stop()
