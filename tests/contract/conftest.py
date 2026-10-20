"""Fixtures de la batería de contrato de proveedores de voz.

Cada adaptador aporta un arnés con la misma forma; los casos solo conocen el arnés.
`jota` y `elevenlabs` (ambos contra upstreams falsos; en ElevenLabs la puerta de audio va en `off`).
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

import pytest

from src.core.config import settings
from src.models.schemas import Client
from tests.fakes.jota_upstreams import FakeTranscriber, FakeTts

ADAPTERS = ["jota", "elevenlabs"]
STT_ADAPTERS = ["jota", "elevenlabs"]

SECRET_KEY = "client-key-SECRET-0123456789"
SECRET_TTS_TOKEN = "tts-token-SECRET-0123456789"
# Más de 40 caracteres: jota loguea a DEBUG un prefijo de 40 de las transcripciones
# (permitido por la política de logs); el texto completo nunca debe aparecer.
USER_TEXT = "frase privada del usuario que supera con mucho los cuarenta caracteres"


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class SttHarness:
    provider: Any
    client: Client
    upstream: Any  # el arnés programa qué emite el upstream


@dataclass
class TtsHarness:
    provider: Any
    token: str
    upstream: Any
    backend_down_provider: Any  # mismo adaptador apuntando a un backend caído


@pytest.fixture(params=STT_ADAPTERS)
async def stt_harness(request):
    if request.param == "elevenlabs":
        async for h in _elevenlabs_stt_harness():
            yield h
        return
    from src.services.speech.jota import JotaSttProvider

    up = FakeTranscriber()
    await up.start()
    s = settings.model_copy(update={"TRANSCRIBER_WS_URL": f"127.0.0.1:{up.port}"})
    provider = JotaSttProvider(s)
    await provider.start()
    yield SttHarness(
        provider=provider,
        client=Client(id="c-contract", client_key=SECRET_KEY, is_active=True),
        upstream=up,
    )
    await provider.aclose()
    await up.stop()


async def _elevenlabs_stt_harness():
    from pydantic import SecretStr

    from src.services.speech.elevenlabs.stt import ElevenLabsSttProvider
    from tests.fakes.elevenlabs_stt import FakeElevenLabsStt

    key = "el-key-SECRET-0123456789"
    up = FakeElevenLabsStt(api_key=key)
    await up.start()
    provider = ElevenLabsSttProvider(
        settings.model_copy(
            update={
                "ELEVENLABS_API_KEY": SecretStr(key),
                "ELEVENLABS_BASE_URL": up.url,
                "STT_AUDIO_GATE": "off",
                "ELEVENLABS_STT_DRAIN_S": 0.3,
            }
        )
    )
    await provider.start()
    yield SttHarness(
        provider=provider,
        client=Client(id="c-contract", client_key=SECRET_KEY, is_active=True),
        upstream=up,
    )
    await provider.aclose()
    await up.stop()


@pytest.fixture(params=ADAPTERS)
async def tts_harness(request):
    if request.param == "elevenlabs":
        async for h in _elevenlabs_tts_harness():
            yield h
        return
    from src.services.speech.jota import JotaTtsProvider

    up = FakeTts()
    await up.start()
    mk = lambda port: JotaTtsProvider(  # noqa: E731
        url=f"127.0.0.1:{port}",
        token=SECRET_TTS_TOKEN,
        initial_backoff=1.0,
        max_backoff=60.0,
    )
    provider = mk(up.port)
    await provider.start()
    yield TtsHarness(
        provider=provider,
        token=SECRET_TTS_TOKEN,
        upstream=up,
        backend_down_provider=mk(_closed_port()),
    )
    await provider.aclose()
    await up.stop()


async def _elevenlabs_tts_harness():
    from pydantic import SecretStr

    from src.services.speech.elevenlabs.tts import ElevenLabsTtsProvider
    from tests.fakes.elevenlabs_tts import FakeElevenLabsTts

    key = "el-key-SECRET-0123456789"
    up = FakeElevenLabsTts(api_key=key)
    await up.start()

    def mk(base: str) -> ElevenLabsTtsProvider:
        return ElevenLabsTtsProvider(
            settings.model_copy(
                update={
                    "ELEVENLABS_API_KEY": SecretStr(key),
                    "ELEVENLABS_BASE_URL": base,
                    "ELEVENLABS_TTS_DEFAULT_VOICE": "gc61tTk93h3LBXPxew2V",
                    "TTS_VOICE_MAP": {},
                }
            )
        )

    provider = mk(up.url)
    await provider.start()
    yield TtsHarness(
        provider=provider,
        token=key,
        upstream=up,
        backend_down_provider=mk(f"ws://127.0.0.1:{_closed_port()}"),
    )
    await provider.aclose()
    await up.stop()
