"""Titular de los proveedores de voz vigentes, con cambio en caliente (sin reinicio).

El cambio no se persiste: al reiniciar rigen `STT_PROVIDER`/`TTS_PROVIDER`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from src.core.startup_checks import check_speech_settings
from src.services.reconnection import ConnectionState, describe_error
from src.services.speech.factory import build_stt_provider, build_tts_provider
from src.services.speech.ports import SpeechToTextProvider, TextToSpeechProvider

logger = logging.getLogger(__name__)

StateListener = Callable[[ConnectionState], None]


class SpeechSwapError(Exception):
    """El proveedor nuevo no arrancó: el cambio no se aplica. Solo nombra el proveedor."""

    def __init__(self, kind: str, provider: str) -> None:
        super().__init__(f"{kind} provider {provider} failed to start")
        self.kind = kind
        self.provider = provider


class SpeechProviders:
    def __init__(self, settings, stt: SpeechToTextProvider, tts: TextToSpeechProvider) -> None:
        self._settings = settings
        self._stt = stt
        self._tts = tts
        self._on_stt_change: StateListener | None = None
        self._on_tts_change: StateListener | None = None
        self._lock = asyncio.Lock()

    @property
    def stt(self) -> SpeechToTextProvider:
        return self._stt

    @property
    def tts(self) -> TextToSpeechProvider:
        return self._tts

    def set_state_listeners(
        self, on_stt_change: StateListener | None, on_tts_change: StateListener | None
    ) -> None:
        """Guarda los listeners y los cablea en los proveedores vigentes (y en los futuros)."""
        self._on_stt_change = on_stt_change
        self._on_tts_change = on_tts_change
        self._stt.on_state_change = on_stt_change
        self._tts.on_state_change = on_tts_change

    @staticmethod
    async def _start(provider, kind: str) -> None:
        # Operación del operador: un start() que falla rechaza el cambio (a diferencia del
        # arranque, que degrada). El mensaje nombra solo el proveedor, nunca la excepción.
        try:
            await provider.start()
        except Exception as e:
            logger.error("Speech provider %s failed to start (%s)", provider.name, describe_error(e))
            raise SpeechSwapError(kind, provider.name) from None

    async def swap(self, *, stt_name: str | None = None, tts_name: str | None = None) -> dict:
        """Cambia STT y/o TTS. Lanza SpeechConfigError (configuración inválida) o ValueError
        (proveedor desconocido/no implementado), o SpeechSwapError (el nuevo no arranca), sin
        tocar nada si algo falla."""
        async with self._lock:
            new_stt_name = stt_name if stt_name and stt_name != self._stt.name else None
            new_tts_name = tts_name if tts_name and tts_name != self._tts.name else None
            if new_stt_name is None and new_tts_name is None:
                return {"stt": self._stt.name, "tts": self._tts.name}

            check_speech_settings(
                self._settings,
                stt_provider=new_stt_name or self._stt.name,
                tts_provider=new_tts_name or self._tts.name,
            )
            new_stt = build_stt_provider(self._settings, new_stt_name) if new_stt_name else None
            try:
                new_tts = build_tts_provider(self._settings, new_tts_name) if new_tts_name else None
            except Exception:
                if new_stt is not None:
                    await new_stt.aclose()
                raise

            old_stt, old_tts = self._stt, self._tts
            try:
                if new_stt is not None:
                    await self._start(new_stt, "stt")
                if new_tts is not None:
                    await self._start(new_tts, "tts")
            except SpeechSwapError:
                for new in (new_stt, new_tts):
                    if new is not None:
                        await self._close_quietly(new.aclose)
                raise
            if new_stt is not None:
                new_stt.on_state_change = self._on_stt_change
                old_stt.on_state_change = None
                self._stt = new_stt
            if new_tts is not None:
                new_tts.on_state_change = self._on_tts_change
                old_tts.on_state_change = None
                self._tts = new_tts
            logger.info("Speech providers swapped: stt=%s tts=%s", self._stt.name, self._tts.name)

            if new_tts is not None:
                # Los TtsStream en vuelo son dueños de su socket y terminan solos.
                await self._close_quietly(old_tts.aclose)
            # STT viejo: solo se suelta la referencia; las sesiones ya abiertas lo conservan
            # hasta cerrarse (ni jota ni ElevenLabs tienen nada que cerrar a nivel proveedor).
            return {"stt": self._stt.name, "tts": self._tts.name}

    @staticmethod
    async def _close_quietly(fn) -> None:
        try:
            await fn()
        except Exception as e:
            logger.warning("Error retiring old speech provider (%s)", describe_error(e))
