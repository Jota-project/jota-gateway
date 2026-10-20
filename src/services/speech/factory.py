"""Construye los proveedores de voz según `STT_PROVIDER`/`TTS_PROVIDER` (o un nombre explícito).

`PROVIDER_BUILDERS` es el registro de adaptadores disponibles: los tests pueden añadir
proveedores falsos con `monkeypatch.setitem`.
"""

from collections.abc import Callable

from src.services.speech.ports import SpeechToTextProvider, TextToSpeechProvider


def _build_jota_stt(settings) -> SpeechToTextProvider:
    from src.services.speech.jota import JotaSttProvider

    return JotaSttProvider(settings)


def _build_jota_tts(settings) -> TextToSpeechProvider:
    from src.services.speech.jota import JotaTtsProvider

    return JotaTtsProvider(
        url=settings.TTS_WS_URL,
        token=settings.TTS_TOKEN,
        initial_backoff=settings.TTS_RECONNECT_INITIAL_BACKOFF,
        max_backoff=settings.TTS_RECONNECT_MAX_BACKOFF,
    )


def _build_elevenlabs_tts(settings) -> TextToSpeechProvider:
    from src.services.speech.elevenlabs.tts import ElevenLabsTtsProvider

    return ElevenLabsTtsProvider(settings)


def _build_elevenlabs_stt(settings) -> SpeechToTextProvider:
    from src.services.speech.elevenlabs.stt import ElevenLabsSttProvider

    return ElevenLabsSttProvider(settings)


PROVIDER_BUILDERS: dict[str, dict[str, Callable]] = {
    "stt": {"jota": _build_jota_stt, "elevenlabs": _build_elevenlabs_stt},
    "tts": {"jota": _build_jota_tts, "elevenlabs": _build_elevenlabs_tts},
}


def _build(kind: str, setting: str, settings, name: str | None):
    name = name or getattr(settings, setting)
    builder = PROVIDER_BUILDERS[kind].get(name)
    if builder is None:
        raise ValueError(f"{setting} no soportado")
    return builder(settings)


def build_stt_provider(settings, name: str | None = None) -> SpeechToTextProvider:
    return _build("stt", "STT_PROVIDER", settings, name)


def build_tts_provider(settings, name: str | None = None) -> TextToSpeechProvider:
    return _build("tts", "TTS_PROVIDER", settings, name)
