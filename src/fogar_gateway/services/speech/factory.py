"""Construye los proveedores de voz según `STT_PROVIDER`/`TTS_PROVIDER` (o un nombre explícito).

`PROVIDER_BUILDERS` es el registro de adaptadores disponibles: los tests pueden añadir
proveedores falsos con `monkeypatch.setitem`.
"""

from collections.abc import Callable

from fogar_gateway.services.speech.ports import SpeechToTextProvider, TextToSpeechProvider


def _build_fogar_stt(settings) -> SpeechToTextProvider:
    from fogar_gateway.services.speech.fogar import FogarSttProvider

    return FogarSttProvider(settings)


def _build_fogar_tts(settings) -> TextToSpeechProvider:
    from fogar_gateway.services.speech.fogar import FogarTtsProvider

    return FogarTtsProvider(
        url=settings.TTS_WS_URL,
        token=settings.TTS_TOKEN,
        initial_backoff=settings.TTS_RECONNECT_INITIAL_BACKOFF,
        max_backoff=settings.TTS_RECONNECT_MAX_BACKOFF,
    )


def _build_elevenlabs_tts(settings) -> TextToSpeechProvider:
    from fogar_gateway.services.speech.elevenlabs.tts import ElevenLabsTtsProvider

    return ElevenLabsTtsProvider(settings)


def _build_elevenlabs_stt(settings) -> SpeechToTextProvider:
    from fogar_gateway.services.speech.elevenlabs.stt import ElevenLabsSttProvider

    return ElevenLabsSttProvider(settings)


def _build_off_stt(settings) -> SpeechToTextProvider:
    from fogar_gateway.services.speech.off import OffSttProvider

    return OffSttProvider()


def _build_off_tts(settings) -> TextToSpeechProvider:
    from fogar_gateway.services.speech.off import OffTtsProvider

    return OffTtsProvider()


PROVIDER_BUILDERS: dict[str, dict[str, Callable]] = {
    "stt": {"fogar": _build_fogar_stt, "elevenlabs": _build_elevenlabs_stt, "off": _build_off_stt},
    "tts": {"fogar": _build_fogar_tts, "elevenlabs": _build_elevenlabs_tts, "off": _build_off_tts},
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
