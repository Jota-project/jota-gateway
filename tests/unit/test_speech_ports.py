"""Puertos de voz y factoría de proveedores (spec 2026-10-20, fase 1)."""

import pytest

from src.core.config import Settings
from src.services.speech import ports  # noqa: F401  (el módulo de puertos importa)
from src.services.speech.factory import build_stt_provider, build_tts_provider


def test_factory_unknown_provider_does_not_echo_value():
    bogus = Settings.model_construct(STT_PROVIDER="s3cr3t-value", TTS_PROVIDER="s3cr3t-value")
    with pytest.raises(ValueError, match="STT_PROVIDER no soportado") as e1:
        build_stt_provider(bogus)
    with pytest.raises(ValueError, match="TTS_PROVIDER no soportado") as e2:
        build_tts_provider(bogus)
    assert "s3cr3t-value" not in str(e1.value) + str(e2.value)
