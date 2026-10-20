"""Construye los proveedores de voz según `STT_PROVIDER`/`TTS_PROVIDER`."""

from src.services.speech.ports import SpeechToTextProvider, TextToSpeechProvider


def build_stt_provider(settings) -> SpeechToTextProvider:
    if settings.STT_PROVIDER == "jota":
        from src.services.speech.jota import JotaSttProvider

        return JotaSttProvider(settings)
    raise ValueError("STT_PROVIDER no soportado")


def build_tts_provider(settings) -> TextToSpeechProvider:
    if settings.TTS_PROVIDER == "jota":
        from src.services.speech.jota import JotaTtsProvider

        return JotaTtsProvider(
            url=settings.TTS_WS_URL,
            token=settings.TTS_TOKEN,
            initial_backoff=settings.TTS_RECONNECT_INITIAL_BACKOFF,
            max_backoff=settings.TTS_RECONNECT_MAX_BACKOFF,
        )
    raise ValueError("TTS_PROVIDER no soportado")
