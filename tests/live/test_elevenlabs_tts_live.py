"""Humo contra ElevenLabs real. No corre en CI: `JGW_LIVE=1 pytest -m live tests/live`.

Gasta unos pocos caracteres de cuota. Imprime solo números; nunca la clave.
"""

import asyncio
import os
import time

import pytest
from pydantic import SecretStr

from src.core.config import settings
from src.services.speech.elevenlabs.tts import ElevenLabsTtsProvider

pytestmark = pytest.mark.live

SAMPLE_RATE, SAMPLE_BYTES = 24000, 2


@pytest.fixture
def live_provider(monkeypatch):
    if os.environ.get("JGW_LIVE") != "1":
        pytest.skip("JGW_LIVE=1 requerido")
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        pytest.skip("ELEVENLABS_API_KEY no está definida")
    s = settings.model_copy(update={"ELEVENLABS_API_KEY": SecretStr(key)})
    return ElevenLabsTtsProvider(s)


async def test_synthesize_hola_soy_jota(live_provider):
    provider = live_provider
    if not await provider.probe():
        reason = "credenciales rechazadas" if provider._terminal else "servicio no disponible"
        pytest.fail(f"ElevenLabs no acepta la sonda ({reason}); revisa ELEVENLABS_API_KEY")
    stream = await provider.connect(provider.resolve_voice(None), 1.0, "live")
    assert stream is not None
    t0 = time.monotonic()
    first_at = None
    pcm = bytearray()
    try:
        await stream.send_text_chunk("Hola, soy Jota.")
        await stream.end()

        async def run():
            nonlocal first_at
            async for chunk in stream.get_audio_stream():
                if first_at is None:
                    first_at = time.monotonic() - t0
                pcm.extend(chunk)

        await asyncio.wait_for(run(), 20)
    finally:
        await stream.close()
    duration = len(pcm) / (SAMPLE_RATE * SAMPLE_BYTES)
    print(f"bytes={len(pcm)} duration_s={duration:.2f} first_audio_s={first_at}")
    assert len(pcm) % 2 == 0
    assert 0.5 <= duration <= 5.0
    assert first_at is not None and first_at < 3.0
