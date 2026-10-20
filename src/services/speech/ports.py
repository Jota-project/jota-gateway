"""Puertos de la capa de proveedores de voz.

Describen el comportamiento del gateway, no el transporte: STT = un flujo por sesión de
audio, TTS = una síntesis por turno. Cada adaptador decide cómo lo implementa.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.models.schemas import Client
from src.services.reconnection import ConnectionState, ServiceStatus


@dataclass(frozen=True)
class SttCapabilities:
    partials: bool  # emite parciales -> habilita barge-in
    warmup_s: float  # retraso típico del primer resultado tras abrir upstream


@dataclass(frozen=True)
class SttParams:
    language: str  # ClientConfig.stt_language
    vad_threshold: float  # ClientConfig.stt_vad_thold; solo jota lo usa


@dataclass(frozen=True)
class SttCallbacks:
    on_transcription: Callable[[str, bool], Awaitable[None]]  # (text, is_final)
    on_warning: Callable[[str, str | None], Awaitable[None]]  # (code, message)
    on_status: Callable[[str, str | None], Awaitable[None]]  # ("busy"|"ok", reason)
    on_incomplete: Callable[[str | None], Awaitable[None]]  # final truncado (solo jota)


@runtime_checkable
class SttSession(Protocol):
    """Una por sesión de audio."""

    state: ConnectionState
    on_state_change: Callable[[ConnectionState], None] | None
    last_transcript_at: float | None  # monotonic

    async def start(self) -> None: ...  # nunca lanza
    async def send_audio(self, frame: bytes) -> None: ...  # Float32 LE 16 kHz mono
    async def send_end(self) -> None: ...  # fuerza el final de la frase en curso
    async def run(self) -> None: ...  # supervisa durante la vida de la sesión
    async def close(self) -> None: ...  # idempotente


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """Singleton de proceso."""

    name: str
    capabilities: SttCapabilities
    state: ConnectionState
    on_state_change: Callable[[ConnectionState], None] | None

    def open_session(
        self, *, client: Client, params: SttParams, callbacks: SttCallbacks
    ) -> SttSession: ...
    async def probe(self) -> bool: ...
    def status(self) -> ServiceStatus: ...
    async def start(self) -> None: ...
    async def aclose(self) -> None: ...


@runtime_checkable
class TtsStream(Protocol):
    """Uno por turno."""

    async def send_text_chunk(self, text: str) -> None: ...
    async def end(self) -> None: ...
    def get_audio_stream(self) -> AsyncIterator[bytes]: ...  # PCM16 LE 24 kHz mono
    async def close(self) -> None: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    """Singleton de proceso."""

    name: str
    state: ConnectionState
    on_state_change: Callable[[ConnectionState], None] | None

    async def connect(
        self, voice: str | None, speed: float | None, client_id: str
    ) -> TtsStream | None: ...
    async def probe(self) -> bool: ...
    def status(self) -> ServiceStatus: ...
    def resolve_voice(self, configured: str | None) -> str | None: ...
    async def start(self) -> None: ...
    async def aclose(self) -> None: ...
