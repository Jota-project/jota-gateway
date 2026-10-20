"""Fixtures de la batería de contrato de proveedores de voz.

Cada adaptador aporta un arnés con la misma forma; los casos solo conocen el arnés.
Hoy solo "jota"; los adaptadores SaaS añadirán su valor a `ADAPTERS`.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

import pytest

from src.core.config import settings
from src.models.schemas import Client
from tests.fakes.jota_upstreams import FakeTranscriber, FakeTts

ADAPTERS = ["jota"]

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


@pytest.fixture(params=ADAPTERS)
async def stt_harness(request):
    assert request.param == "jota"
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


@pytest.fixture(params=ADAPTERS)
async def tts_harness(request):
    assert request.param == "jota"
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
