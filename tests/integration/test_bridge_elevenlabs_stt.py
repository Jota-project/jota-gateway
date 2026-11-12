"""Sesión de audio real del bridge con el adaptador STT de ElevenLabs contra el upstream falso."""

import asyncio
import math
import struct

import pytest
from pydantic import SecretStr

from fogar_gateway.core.config import settings
from fogar_gateway.services.speech.elevenlabs.stt import ElevenLabsSttProvider
from tests.fakes.bridge import blocking, make_bridge, wait_until
from tests.fakes.elevenlabs_stt import FakeElevenLabsStt


def _speech(samples=1600):
    return struct.pack(f"<{samples}f", *[0.1 * math.sin(i * 0.3) for i in range(samples)])


def _quiet(samples=1600):
    return struct.pack(f"<{samples}f", *([0.0] * samples))


@pytest.fixture(autouse=True)
def _short_drain(monkeypatch):
    from fogar_gateway.services.speech.elevenlabs import stt as stt_mod

    monkeypatch.setattr(stt_mod, "DRAIN_S", 0.3)


async def _session(fake):
    provider = ElevenLabsSttProvider(
        settings.model_copy(
            update={
                "ELEVENLABS_API_KEY": SecretStr("fake-el-key"),
                "ELEVENLABS_BASE_URL": fake.url,
                "STT_AUDIO_GATE": "energy",
            }
        )
    )
    bridge, ws = make_bridge(input_mode="audio", output_mode=("text", "status"), stt=provider)
    await bridge.connect_internal_services()
    runner = asyncio.create_task(bridge.run())
    return bridge, ws, runner


async def _stop(bridge, ws, runner):
    await ws.inbox.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(runner, 5)
    await bridge._sender.aclose(1.0)


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
        await wait_until(lambda: ws.of("transcription"), 3.0)
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
        bridge.orchestrator.stream_response = blocking(started)
        await bridge._start_turn("x")
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(5):
            await ws.inbox.put({"bytes": _speech()})
        await wait_until(lambda: ws.of("interrupted"), 3.0)
        assert len(ws.of("turn_end")) == 1
        await _stop(bridge, ws, runner)
    finally:
        await fake.stop()
