"""Titular mínimo de proveedores de voz para construir un `FogarBridge` en tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fogar_gateway.core.config import settings
from fogar_gateway.services.speech.factory import build_stt_provider


def speech_providers(tts=None, stt=None) -> SimpleNamespace:
    """Como `SpeechProviders`: `.tts`/`.stt`."""
    return SimpleNamespace(
        tts=tts if tts is not None else AsyncMock(),
        stt=stt if stt is not None else build_stt_provider(settings),
    )
