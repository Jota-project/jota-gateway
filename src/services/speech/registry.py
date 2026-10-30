"""Titular de los proveedores de voz, elegidos al arrancar con STT_PROVIDER/TTS_PROVIDER."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.speech.ports import SpeechToTextProvider, TextToSpeechProvider


@dataclass(frozen=True)
class SpeechProviders:
    stt: SpeechToTextProvider
    tts: TextToSpeechProvider
