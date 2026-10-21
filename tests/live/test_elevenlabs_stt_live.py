"""Humo STT contra ElevenLabs real. No corre en CI: `JGW_LIVE=1 pytest -m live tests/live`.

Sintetiza "Hola, soy Jota." con el TTS de ElevenLabs y lo transcribe con el STT realtime.
Imprime solo números y booleanos; nunca la clave ni el texto.
"""

import asyncio
import os
import time

import pytest
from pydantic import SecretStr

from src.core.config import settings
from src.models.schemas import Client
from src.services.speech.audio import s16le_24k_to_f32le_16k
from src.services.speech.elevenlabs.stt import ElevenLabsSttProvider
from src.services.speech.elevenlabs.tts import ElevenLabsTtsProvider
from src.services.speech.ports import SttCallbacks, SttParams

pytestmark = pytest.mark.live


@pytest.fixture
def live_settings():
    if os.environ.get("JGW_LIVE") != "1":
        pytest.skip("JGW_LIVE=1 requerido")
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        pytest.skip("ELEVENLABS_API_KEY no está definida")
    return settings.model_copy(
        update={"ELEVENLABS_API_KEY": SecretStr(key), "STT_AUDIO_GATE": "off"}
    )


async def _synthesize(s) -> bytes:
    tts = ElevenLabsTtsProvider(s)
    stream = await tts.connect(tts.resolve_voice(None), 1.0, "live-stt")
    assert stream is not None
    pcm = bytearray()
    try:
        await stream.send_text_chunk("Hola, soy Jota.")
        await stream.end()

        async def run():
            async for chunk in stream.get_audio_stream():
                pcm.extend(chunk)

        await asyncio.wait_for(run(), 20)
    finally:
        await stream.close()
    return bytes(pcm)


async def test_transcribe_hola_soy_jota(live_settings):
    pcm24 = await _synthesize(live_settings)
    f32 = s16le_24k_to_f32le_16k(pcm24)
    provider = ElevenLabsSttProvider(live_settings)
    if not await provider.probe():
        pytest.fail("ElevenLabs STT no acepta la sonda; revisa ELEVENLABS_API_KEY")
    finals: list[str] = []
    partials = 0

    async def on_tr(text, final):
        nonlocal partials
        if final:
            finals.append(text)
        else:
            partials += 1

    async def noop(*_a):
        return None

    session = provider.open_session(
        client=Client(id="live", client_key="k", is_active=True),
        params=SttParams(language="es", vad_threshold=0.0),
        callbacks=SttCallbacks(on_tr, noop, noop, noop),
    )
    runner = asyncio.create_task(session.run())
    t0 = time.monotonic()
    try:
        step = 1600 * 4  # 0,1 s Float32
        for i in range(0, len(f32), step):
            await session.send_audio(f32[i : i + step])
            await asyncio.sleep(0.05)
        await session.send_end()
        end = time.monotonic() + 15
        while not finals and time.monotonic() < end:
            await asyncio.sleep(0.1)
    finally:
        await session.close()
        await asyncio.gather(runner, return_exceptions=True)
    ok = any("jota" in t.lower() for t in finals)
    print(f"audio_s={len(f32) / 4 / 16000:.2f} finals={len(finals)} partials={partials} "
          f"first_final_s={time.monotonic() - t0:.2f} contains_jota={ok}")
    assert ok
