"""Adaptador TTS de ElevenLabs sobre el WebSocket `stream-input`.

Una síntesis por turno = una conexión (`ElevenLabsTtsStream`, dueña de su socket). El proveedor
es un singleton de proceso: limita los streams simultáneos con un semáforo y comparte con el
resto de proveedores la misma salud (`ServiceHealth`) y la misma compuerta de backoff perezoso
(`LazyBackoffGate`) que `ReconnectingTTSClient`.

Nunca se registra la clave, el texto, ni el texto de un error/cierre del proveedor: solo su clase
(`auth`/`capacity`/`other`).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from src.core.config import settings
from src.services.reconnection import (
    ConnectionState,
    LazyBackoffGate,
    ServiceHealth,
    ServiceStatus,
    describe_error,
    health_attr,
)
from src.services.speech.elevenlabs.common import (
    ErrorKind,
    classify_close,
    classify_exception,
    pin_websockets_logging,
    ws_url,
)
from src.services.speech.voices import resolve_voice

logger = logging.getLogger(__name__)

KEEPALIVE_S = 15.0  # sin texto durante este tiempo y sin end(): `{"text":" "}`
MIN_SPEED, MAX_SPEED = 0.7, 1.2
INACTIVITY_TIMEOUT_S = 180
OUTPUT_FORMAT = "pcm_24000"
MAX_FRAME_BYTES = 16 * 1024 * 1024
CLOSE_GRACE_S = 0.5  # cierre limpio del socket al terminar; close() total < 1 s
PROBE_TIMEOUT_S = 5.0
PROBE_CONNECT_TIMEOUT_S = 4.0  # TCP + handshake WS: vencer aquí es un fallo, no "sano"
# Fin de frase: . ? ! … (más cierres de comillas/paréntesis opcionales) seguido de espacio o del
# final del chunk. `:` y `;` no cierran frase.
_SENTENCE_END = re.compile(r"([.?!…])[)\]}\"'»\u201d\u2019]*(?:\s+|$)")
# Abreviaturas (y números de lista) tras las que un "." no cierra frase.
_ABBREVIATIONS = frozenset(
    {"sr", "sra", "srta", "dr", "dra", "prof", "mr", "mrs", "ms", "etc", "ud", "uds"}
    | {"vs", "núm", "pág", "ej", "aprox", "tel", "avda"}
)
_NATIVE_VOICE = re.compile(r"[A-Za-z0-9]{20}")
_MAX_CLAMP_CLIENTS = 1024


def _is_abbreviation(prefix: str) -> bool:
    """¿El "." que sigue a `prefix` es de una abreviatura ("Sr.", "etc."), una inicial ("J.")
    o un número corto de lista ("3.")?"""
    words = prefix.split()
    token = words[-1].lstrip("([{\"'«“¿¡") if words else ""
    if not token:
        return False
    if token.isdigit():
        return len(token) <= 2
    return token.lower() in _ABBREVIATIONS or (len(token) == 1 and token.isupper())


def _ends_sentence(text: str) -> bool:
    for m in _SENTENCE_END.finditer(text):
        if m.group(1) == "." and _is_abbreviation(text[: m.start()]):
            continue
        return True
    return False


class ElevenLabsTtsStream:
    """Una síntesis. Construirlo lanza la tarea que abre el socket; nada espera a la red."""

    def __init__(
        self,
        provider: ElevenLabsTtsProvider,
        *,
        url: str,
        voice: str,
        speed: float,
        gate_token: int,
    ) -> None:
        self._p = provider
        self._url = url
        self._voice = voice
        self._speed = speed
        self._gate_token = gate_token
        self._pending: collections.deque[dict] = collections.deque()
        self._wakeup = asyncio.Event()
        self._out: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._buffered = 0
        self._carry = b""
        self._ws: Any = None
        self._closed = False
        self._end_requested = False
        self._end_sent = False
        self._final = False
        self._got_audio = False
        self._reopened = False
        self._warned_close = False
        self.chars_sent = 0
        self._task = asyncio.get_running_loop().create_task(
            self._runner(), name="elevenlabs-tts-stream"
        )
        self._task.add_done_callback(self._finished)

    # --- API pública (puerto TtsStream) ---

    async def send_text_chunk(self, text: str) -> None:
        # Un texto vacío significa "fin de la entrada" para ElevenLabs: nunca se envía.
        if self._closed or self._end_requested or not text:
            return
        self.chars_sent += len(text)
        msg: dict = {"text": text}
        if _ends_sentence(text):
            msg["flush"] = True
        self._enqueue(msg)

    async def end(self) -> None:
        if self._closed or self._end_requested:
            return
        self._end_requested = True
        self._enqueue({"text": "", "flush": True})
        self._enqueue({"text": ""})

    async def get_audio_stream(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._out.get()
            if item is None:
                self._out.put_nowait(None)  # otra iteración posterior también termina
                return
            self._buffered -= len(item)
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending.clear()
        if not self._task.done():
            self._task.cancel()
            await asyncio.wait({self._task}, timeout=0.9)

    # --- internos ---

    def _enqueue(self, msg: dict) -> None:
        self._pending.append(msg)
        self._wakeup.set()

    def _finished(self, task: asyncio.Task) -> None:
        # Una sola vez y siempre, incluso si la tarea se canceló antes de empezar a ejecutarse
        # (en ese caso su `finally` no corre): libera el hueco y termina el iterador de audio.
        self._p._sem.release()
        self._out.put_nowait(None)
        exc = None if task.cancelled() else task.exception()
        if exc is not None:
            logger.warning("ElevenLabs TTS stream failed (%s)", describe_error(exc))

    def _init_message(self) -> dict:
        return {
            "text": " ",
            "voice_settings": {"speed": self._speed},
            "generation_config": {"chunk_length_schedule": self._p._chunk_schedule},
        }

    async def _runner(self) -> None:
        try:
            while True:
                try:
                    ws = await websockets.connect(
                        self._url,
                        additional_headers={"xi-api-key": self._p._key},
                        max_size=MAX_FRAME_BYTES,
                        close_timeout=1,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._p._connect_failed(self._gate_token, classify_exception(e), e)
                    return
                self._ws = ws
                if await self._serve(ws) != "reopen":
                    return
        finally:
            await self._close_ws()

    async def _close_ws(self) -> None:
        ws, self._ws = self._ws, None
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.close(), CLOSE_GRACE_S)
        except Exception:  # incluye TimeoutError: websockets aborta el transporte solo
            pass

    async def _serve(self, ws) -> str:
        """Escribe hasta el final de la entrada; devuelve "reopen" si procede reabrir."""
        reader = asyncio.get_running_loop().create_task(self._read(ws))
        try:
            last_text = time.monotonic()
            last_sent: float | None = last_text
            try:
                await ws.send(json.dumps(self._init_message()))
            except ConnectionClosed:
                last_sent = None  # el lector ve el cierre y lo clasifica
            while last_sent is not None and not self._end_sent and not reader.done():
                if self._pending:
                    msg = self._pending.popleft()
                    try:
                        await ws.send(json.dumps(msg))
                    except ConnectionClosed:
                        self._pending.appendleft(msg)
                        break
                    last_sent = last_text = time.monotonic()
                    if msg == {"text": ""}:
                        self._end_sent = True
                    continue
                self._wakeup.clear()
                waiter = asyncio.get_running_loop().create_task(self._wakeup.wait())
                try:
                    wait_s = max(0.0, KEEPALIVE_S - (time.monotonic() - last_sent))
                    if not self._end_requested:  # tope de vida si nadie envía texto ni end()
                        wait_s = min(
                            wait_s,
                            max(0.0, settings.TURN_TIMEOUT_S - (time.monotonic() - last_text)),
                        )
                    done, _ = await asyncio.wait(
                        {waiter, reader}, timeout=wait_s, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    waiter.cancel()
                if (
                    not done
                    and not self._end_requested
                    and time.monotonic() - last_text >= settings.TURN_TIMEOUT_S
                ):
                    self._warn_close_once("stream sin texto ni end() más allá del tope; se cierra")
                    self._closed = True
                    self._pending.clear()
                    return "done"
                if not done:
                    try:
                        await ws.send('{"text": " "}')
                    except ConnectionClosed:
                        break
                    last_sent = time.monotonic()
            outcome = await reader
        finally:
            reader.cancel()
        if outcome in ("final", "overflow", "error") or self._closed:
            return "done"
        # Cierre del upstream antes del final de audio.
        if outcome in ("auth", "quota"):
            self._p._mark_auth_failed(outcome)
            return "done"
        if outcome == "capacity":
            self._p._capacity_close(self._gate_token)
            return "done"
        if self._end_sent or self._reopened:
            self._warn_close_once("cierre inesperado del upstream antes del final")
            return "done"
        self._reopened = True
        self._warn_close_once("cierre inesperado del upstream: se reabre una vez")
        await self._close_ws()
        return "reopen"

    def _warn_close_once(self, msg: str) -> None:
        if not self._warned_close:
            self._warned_close = True
            logger.warning("ElevenLabs TTS: %s", msg)

    async def _read(self, ws) -> str:
        """Vuelca el audio al búfer. Devuelve "final", "overflow", "error" o, si el socket se
        cerró sin `isFinal`, la clase del cierre (`auth`/`capacity`/`other`)."""
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                audio = data.get("audio")
                if isinstance(audio, str) and audio:
                    try:
                        pcm = self._carry + base64.b64decode(audio)
                    except (binascii.Error, ValueError):
                        continue
                    self._carry = pcm[-1:] if len(pcm) % 2 else b""
                    pcm = pcm[: len(pcm) - len(self._carry)]
                    if not pcm:
                        continue
                    if self._buffered + len(pcm) > self._p._max_buffer:
                        logger.warning(
                            "ElevenLabs TTS: el búfer de audio supera %d bytes; se corta la "
                            "síntesis (el turno sigue solo con texto)",
                            self._p._max_buffer,
                        )
                        return "overflow"
                    if not self._got_audio:
                        self._got_audio = True
                        self._p._record_success()
                    self._buffered += len(pcm)
                    self._out.put_nowait(pcm)
                if data.get("isFinal"):
                    self._final = True
                    return "final"
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("ElevenLabs TTS: lectura abortada (%s)", describe_error(e))
            return "error"
        return classify_close(ws.close_code, ws.close_reason)


class ElevenLabsTtsProvider:
    name = "elevenlabs"

    state = health_attr("state")
    on_state_change = health_attr("on_state_change")

    def __init__(self, settings) -> None:
        secret = settings.ELEVENLABS_API_KEY
        key = secret.get_secret_value().strip() if secret is not None else ""
        if not key:
            raise ValueError("ELEVENLABS_API_KEY")
        pin_websockets_logging()
        self._key = key
        self._base = settings.ELEVENLABS_BASE_URL
        self._model = settings.ELEVENLABS_TTS_MODEL
        self._chunk_schedule = list(settings.ELEVENLABS_TTS_CHUNK_SCHEDULE)
        self._max_buffer = settings.ELEVENLABS_TTS_MAX_BUFFER_BYTES
        self._health_ttl = settings.ELEVENLABS_HEALTH_TTL_S
        self._default_voice = settings.ELEVENLABS_TTS_DEFAULT_VOICE
        self._voice_map = dict(settings.TTS_VOICE_MAP)
        self._sem = asyncio.Semaphore(settings.ELEVENLABS_TTS_MAX_STREAMS)
        self._gate = LazyBackoffGate(
            settings.TTS_RECONNECT_INITIAL_BACKOFF, settings.TTS_RECONNECT_MAX_BACKOFF
        )
        self._health = ServiceHealth("tts", ConnectionState.CONNECTED)
        self._auth_failed = False
        self._closed = False
        self._counters = {"capacity_refusals": 0, "provider_429": 0, "auth_errors": 0}
        self._clamp_logged: set[str] = set()
        self._probe_at: float | None = None
        self._probe_ok = False

    def __repr__(self) -> str:
        return f"<ElevenLabsTtsProvider state={self.state.value}>"

    # --- URL / voz ---

    def _stream_url(self, voice: str) -> str:
        return ws_url(
            self._base,
            f"/v1/text-to-speech/{voice}/stream-input",
            {
                "model_id": self._model,
                "output_format": OUTPUT_FORMAT,
                "inactivity_timeout": INACTIVITY_TIMEOUT_S,
            },
        )

    def resolve_voice(self, configured: str | None) -> str | None:
        return resolve_voice(
            configured,
            voice_map=self._voice_map,
            accepts_native=lambda v: _NATIVE_VOICE.fullmatch(v) is not None,
            default=self._default_voice,
        )

    def _clamp_speed(self, speed: float | None, client_id: str) -> float:
        if speed is None:
            return 1.0
        clamped = min(MAX_SPEED, max(MIN_SPEED, speed))
        if clamped != speed and client_id not in self._clamp_logged:
            if len(self._clamp_logged) >= _MAX_CLAMP_CLIENTS:
                self._clamp_logged.clear()
            self._clamp_logged.add(client_id)
            logger.info(
                "ElevenLabs TTS: velocidad %.2f fuera de [%.1f, %.1f]; se usa %.2f",
                speed,
                MIN_SPEED,
                MAX_SPEED,
                clamped,
            )
        return clamped

    # --- puerto TextToSpeechProvider ---

    async def connect(
        self, voice: str | None, speed: float | None, client_id: str
    ) -> ElevenLabsTtsStream | None:
        if self._closed or self._auth_failed:
            return None
        token = self._gate.begin()
        if token is None:
            return None
        if self._sem.locked():
            # Límite propio de streams: no es una caída del proveedor, no toca la salud.
            self._counters["capacity_refusals"] += 1
            return None
        await self._sem.acquire()  # libre: no suspende
        voice = voice or self._default_voice or ""  # el bridge ya llamó a resolve_voice()
        try:
            return ElevenLabsTtsStream(
                self,
                url=self._stream_url(voice),
                voice=voice,
                speed=self._clamp_speed(speed, client_id),
                gate_token=token,
            )
        except BaseException:
            self._sem.release()
            raise

    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    def status(self) -> ServiceStatus:
        return self._health.status()

    # --- bookkeeping de salud ---

    def _mark_auth_failed(self, why: str = "auth") -> None:
        self._counters["auth_errors"] += 1
        if not self._auth_failed:
            self._auth_failed = True
            logger.error(
                "ElevenLabs TTS: proveedor degradado (%s) hasta reiniciar o cambiar de proveedor",
                why,
            )
        self._health.mark_attempt_failed(why)
        self._health.set_state(ConnectionState.DEGRADED)

    def _connect_failed(self, token: int, kind: ErrorKind, exc: BaseException | None) -> None:
        if kind in ("auth", "quota"):
            self._mark_auth_failed(kind)
            return
        if kind == "capacity":
            self._counters["provider_429"] += 1
            error = "capacity"
        else:
            error = describe_error(exc) if exc is not None else "connect_failed"
        self._record_failure(token, error)

    def _capacity_close(self, token: int) -> None:
        """Cierre por capacidad tras el handshake: avanza solo el backoff de la compuerta (el
        siguiente connect espera), sin tocar la salud: no es una caída del servicio."""
        self._counters["provider_429"] += 1
        self._gate.record_failure(token)

    def _record_failure(self, token: int, error: str) -> None:
        if not self._gate.record_failure(token):
            logger.debug("ElevenLabs TTS connect failure ignored (same outage): %s", error)
            return
        self._health.mark_attempt_failed(error)
        logger.warning(
            "ElevenLabs TTS connect failed (attempt %d): %s",
            self._health.reconnect_attempts,
            error,
        )
        self._health.set_state(ConnectionState.RECONNECTING)

    def _record_success(self) -> None:
        if self._auth_failed:
            return
        self._gate.record_success()
        self._health.mark_connected()

    # --- sonda de salud ---

    async def probe(self) -> bool:
        if self._closed or self._auth_failed:
            return False
        now = time.monotonic()
        if self._probe_at is not None and now - self._probe_at < self._health_ttl:
            return self._probe_ok
        token = self._gate.begin()
        if token is None:
            return False  # dentro de la ventana de backoff: sin red
        ok = await self._probe_once(token)
        self._probe_ok = ok
        self._probe_at = time.monotonic()
        return ok

    async def _probe_once(self, token: int) -> bool:
        """Abre el stream de la voz por defecto, manda `" "` y el fin de entrada y espera el
        cierre. No factura más que un espacio."""
        voice = self._default_voice or ""
        init = {
            "text": " ",
            "voice_settings": {"speed": 1.0},
            "generation_config": {"chunk_length_schedule": self._chunk_schedule},
        }
        connected = False
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_S):
                async with websockets.connect(
                    self._stream_url(voice),
                    additional_headers={"xi-api-key": self._key},
                    max_size=MAX_FRAME_BYTES,
                    close_timeout=1,
                    open_timeout=PROBE_CONNECT_TIMEOUT_S,
                ) as ws:
                    connected = True
                    await ws.send(json.dumps(init))
                    await ws.send(json.dumps({"text": ""}))
                    try:
                        async for _ in ws:
                            pass
                    except ConnectionClosed:
                        pass
                    kind = classify_close(ws.close_code, ws.close_reason)
                    if ws.close_code in (None, 1000, 1001, 1005):
                        kind = "other"
                        clean = True
                    else:
                        clean = False
        except TimeoutError as e:
            if not connected:  # el connect/handshake no terminó: no es un servicio sano
                self._connect_failed(token, classify_exception(e), e)
                return False
            # Conectó y nadie rechazó las credenciales en el plazo: el servicio responde.
            self._record_success()
            return True
        except Exception as e:
            self._connect_failed(token, classify_exception(e), e)
            return False
        if clean:
            self._record_success()
            return True
        self._connect_failed(token, kind, None)
        return False

    async def start(self) -> None:
        try:
            await self.probe()
        except Exception as e:
            logger.warning("ElevenLabs TTS: probe de arranque falló (%s)", describe_error(e))

    async def aclose(self) -> None:
        self._closed = True
