"""Adaptador STT de ElevenLabs sobre el WebSocket realtime (`scribe_v2_realtime`).

Una sesión de audio del gateway = como mucho una conexión upstream abierta a la vez, y solo
mientras hay voz: la conexión se abre de forma perezosa con el primer audio que hay que
reenviar y se cierra tras `ELEVENLABS_STT_IDLE_CLOSE_S` sin audio (el proveedor factura por
hora de audio enviado). El proveedor es un singleton de proceso que limita las conexiones
abiertas con un semáforo (no las sesiones).

Nunca se registra la clave, ninguna transcripción ni el texto de un error del proveedor: solo
su clase (`auth`/`capacity`/`other`) o el nombre del tipo de excepción.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field

import websockets
from websockets.exceptions import ConnectionClosed

from src.core.config import effective_audio_gate
from src.models.schemas import Client
from src.services.reconnection import (
    ConnectionState,
    ServiceHealth,
    ServiceStatus,
    describe_error,
    health_attr,
    reconnect_with_backoff,
)
from src.services.speech.audio import EnergyGate, f32le_to_s16le
from src.services.speech.elevenlabs.common import (
    classify_close,
    classify_exception,
    pin_websockets_logging,
    ws_url,
)
from src.services.speech.ports import SttCallbacks, SttCapabilities, SttParams
from src.services.speech.sessions import SessionRefCount

logger = logging.getLogger(__name__)

MODEL_ID = "scribe_v2_realtime"
AUDIO_FORMAT = "pcm_16000"
SAMPLE_RATE = 16000
BYTES_PER_S = SAMPLE_RATE * 2
MIN_CHUNK_BYTES = BYTES_PER_S // 10  # 0,1 s: no se envía menos salvo al confirmar/cerrar
MAX_CHUNK_BYTES = BYTES_PER_S  # 1 s
MIN_COMMIT_GAP_S = 1.0
SESSION_STARTED_TIMEOUT_S = 10.0
PROBE_TIMEOUT_S = 5.0
PROBE_CONNECT_TIMEOUT_S = 4.0  # TCP + handshake WS: vencer aquí es un fallo, no "sano"
MAX_FRAME_BYTES = 4 * 1024 * 1024
CLOSE_GRACE_S = 0.5

_AUTH_ERRORS = {"auth_error", "unaccepted_terms"}
_TERMINAL_QUOTA = {"quota_exceeded"}
_CAPACITY_ERRORS = {"rate_limited", "queue_overflow", "resource_exhausted"}
# Informativos: no cierran la conexión. `insufficient_audio_activity` llega al confirmar una
# frase ya confirmada por el VAD del servidor.
_IGNORED_ERRORS = {"commit_throttled", "insufficient_audio_activity"}
_OTHER_ERRORS = {
    "error",
    "session_time_limit_exceeded",
    "input_error",
    "invalid_request",
    "chunk_size_exceeded",
    "transcriber_error",
}


@dataclass
class _Upstream:
    ws: object = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    final: asyncio.Event = field(default_factory=asyncio.Event)  # committed_transcript recibido
    closing: bool = False  # cierre iniciado por nosotros: no se le manda más audio
    draining: bool = False  # confirmando lo pendiente antes de cerrar: audio nuevo, al búfer
    reader: asyncio.Task | None = None
    commit_sent: bool = False  # hay una confirmación nuestra sin su committed_transcript
    outcome: str = "other"  # clase de la causa del cierre


class _Terminal(Exception):
    """El proveedor quedó degradado de forma terminal durante un intento de conexión."""


class ElevenLabsSttSession:
    def __init__(
        self,
        provider: ElevenLabsSttProvider,
        client: Client,
        params: SttParams,
        callbacks: SttCallbacks,
    ) -> None:
        self._p = provider
        self._client_id = client.id
        self._params = params
        self._cb = callbacks
        s = provider._settings
        self._health = ServiceHealth("transcriber", ConnectionState.CONNECTED)
        self.last_transcript_at: float | None = None
        self._gate = (
            EnergyGate(rms_dbfs=s.STT_GATE_RMS_DBFS)
            if effective_audio_gate(s, "elevenlabs") == "energy"
            else None
        )
        self._buf = bytearray()  # PCM16 pendiente de enviar
        self._buf_cap = int(s.ELEVENLABS_STT_CONNECT_BUFFER_S * BYTES_PER_S) // 2 * 2
        self._conn: _Upstream | None = None
        self._life: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._closed_evt = asyncio.Event()
        self._close_lock = asyncio.Lock()
        self._uncommitted = False
        self._audio_since_commit = False
        self._want_commit = False
        self._commit_timer: asyncio.Task | None = None
        self._last_commit_at: float | None = None
        self._last_fwd: float | None = None  # último audio recibido para reenviar
        self._busy = False
        self._busy_cause = ""
        self._cooldown_until = 0.0
        self._warm_deadline: float | None = None
        self._tasks: set[asyncio.Task] = set()
        self._audio_bytes_sent = 0
        self._opens = 0
        self._refusals = 0
        provider._refs.opened()
        provider._sessions.add(self)
        if provider._terminal:
            self._health.state = ConnectionState.DEGRADED

    state = health_attr("state")
    on_state_change = health_attr("on_state_change")

    # --- puerto SttSession ---

    async def start(self) -> None:
        return None  # perezoso: sin red hasta que haya audio

    async def run(self) -> None:
        idle = asyncio.get_running_loop().create_task(self._idle_loop())
        try:
            await self._closed_evt.wait()
        finally:
            idle.cancel()

    async def send_audio(self, frame: bytes) -> None:
        if self._closed or self._p._terminal:
            return
        chunks, ended = self._gate.feed(frame) if self._gate else ([frame], False)
        got = False
        for chunk in chunks:
            pcm = f32le_to_s16le(chunk)
            if pcm:
                self._buf += pcm
                got = True
        if got:
            self._uncommitted = True
            self._audio_since_commit = True
            self._last_fwd = time.monotonic()
            self._warm_deadline = None
            await self._ensure_upstream()
            conn = self._conn
            if conn is not None and conn.ready.is_set() and not conn.draining:
                await self._pump()
            else:
                self._trim_buffer()
        if ended:
            await self.send_end()

    async def send_end(self) -> None:
        if self._closed or self._p._terminal or not self._uncommitted:
            return
        self._want_commit = True
        await self._pump()

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
        try:
            await self._graceful_close()
        finally:
            self._closed_evt.set()
            self._p._sessions.discard(self)
            for t in list(self._tasks):
                t.cancel()
            await self._p._refs.closed()

    def usage(self) -> dict[str, float]:
        return {
            "stt_audio_s": self._audio_bytes_sent / BYTES_PER_S,
            "stt_upstream_opens": float(self._opens),
            "stt_capacity_refusals": float(self._refusals),
        }

    # --- buffer / apertura perezosa ---

    def _trim_buffer(self) -> None:
        excess = len(self._buf) - self._buf_cap
        if excess > 0:
            del self._buf[: excess + (excess % 2)]

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def _set_busy(self, cause: str) -> None:
        if not self._busy:
            self._busy = True
            self._busy_cause = cause
            await self._notify_status("busy", "provider_capacity")

    async def _clear_busy(self) -> None:
        if self._busy:
            self._busy = False
            self._busy_cause = ""
            await self._notify_status("ok", None)

    async def _notify_status(self, state: str, reason: str | None) -> None:
        try:
            await self._cb.on_status(state, reason)
        except Exception as e:
            logger.warning("ElevenLabs STT: on_status falló (%s)", describe_error(e))

    async def _ensure_upstream(self) -> None:
        if self._conn is not None or self._closed or self._p._terminal:
            return  # una conexión viva o aún drenando: el audio espera en el búfer
        if time.monotonic() < self._cooldown_until:
            self._buf.clear()
            return
        if self._p._sem.locked():
            self._refusals += 1
            self._p._counters["capacity_refusals"] += 1
            await self._set_busy("sem")
            self._buf.clear()
            self._uncommitted = False
            return
        await self._p._sem.acquire()  # libre: no suspende
        if self._busy_cause == "sem":
            await self._clear_busy()
        up = _Upstream()
        self._conn = up
        task = asyncio.get_running_loop().create_task(self._life_main(up))
        self._life = task
        task.add_done_callback(lambda _t: self._p._sem.release())

    # --- conexión upstream ---

    async def _connect_once(self, up: _Upstream) -> None:
        """Abre el socket y espera `session_started`; deja `up` listo y con su lector."""
        try:
            ws = await websockets.connect(
                self._p._stream_url(self._params.language),
                additional_headers={"xi-api-key": self._p._key},
                max_size=MAX_FRAME_BYTES,
                close_timeout=1,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            kind = classify_exception(e)
            self._p._connect_error(kind)
            if kind == "auth":
                raise _Terminal from None
            raise
        up.ws = ws
        up.ready = asyncio.Event()
        up.final = asyncio.Event()
        up.closing = False
        reader = asyncio.get_running_loop().create_task(self._read(up))
        up.reader = reader
        waiter = asyncio.get_running_loop().create_task(up.ready.wait())
        try:
            await asyncio.wait({waiter, reader}, timeout=SESSION_STARTED_TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
        if not up.ready.is_set():
            reader.cancel()
            await self._close_ws(ws)
            if self._p._terminal:
                raise _Terminal
            raise ConnectionError("session_started")
        self._opens += 1
        self._p._counters["upstream_opens"] += 1
        self._p._record_success()
        if self._busy_cause == "error":
            await self._clear_busy()

    async def _life_main(self, up: _Upstream) -> None:
        respawn = False
        try:
            try:
                await self._connect_once(up)
            except _Terminal:
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("ElevenLabs STT: conexión upstream falló (%s)", describe_error(e))
                # Sin reintento en bucle: el audio en espera se descarta y el próximo, pasado
                # el enfriamiento, vuelve a intentarlo.
                self._buf.clear()
                self._uncommitted = False
                self._cooldown_until = (
                    time.monotonic() + self._p._settings.TRANSCRIBER_RECONNECT_INITIAL_BACKOFF
                )
                return
            while True:
                await self._pump()
                await up.reader
                up.ready.clear()
                await self._close_ws(up.ws)
                if self._closed or self._p._terminal:
                    return
                if up.closing:
                    respawn = up.outcome == "other"  # cierre nuestro (reposo/drenado)
                    return
                if up.outcome != "other":
                    return
                if not (self._uncommitted or self._buf):
                    return  # cierre en reposo: silencioso, el próximo audio reabre
                self._health.set_state(ConnectionState.RECONNECTING)
                s = self._p._settings
                try:
                    ok = await reconnect_with_backoff(
                        lambda: self._connect_once(up),
                        self._health,
                        initial_backoff=s.TRANSCRIBER_RECONNECT_INITIAL_BACKOFF,
                        max_backoff=s.TRANSCRIBER_RECONNECT_MAX_BACKOFF,
                        max_duration=s.TRANSCRIBER_RECONNECT_MAX_DURATION,
                        label="elevenlabs-stt",
                    )
                except _Terminal:
                    return
                if not ok:
                    return
        finally:
            ws = up.ws
            if ws is not None:
                await self._close_ws(ws)
            if self._conn is up:
                self._conn = None
            if respawn and self._buf and not self._closed and not self._p._terminal:
                # Audio llegado mientras la conexión anterior drenaba: abrir la siguiente.
                self._spawn(self._ensure_upstream())

    async def _close_ws(self, ws) -> None:
        try:
            await asyncio.wait_for(ws.close(), CLOSE_GRACE_S)
        except Exception:  # incluye TimeoutError
            pass

    async def _read(self, up: _Upstream) -> None:
        ws = up.ws
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
                if await self._on_message(up, data):
                    return
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("ElevenLabs STT: lectura abortada (%s)", describe_error(e))
            up.outcome = "other"
            return
        if not up.closing:
            kind = classify_close(ws.close_code, ws.close_reason)
            up.outcome = kind
            if kind == "auth":
                self._p._mark_terminal("auth")
            elif kind == "capacity":
                await self._on_capacity()

    async def _on_capacity(self) -> None:
        self._p._counters["provider_429"] += 1
        self._cooldown_until = time.monotonic() + self._p._settings.TRANSCRIBER_RECONNECT_INITIAL_BACKOFF
        await self._set_busy("error")

    async def _on_message(self, up: _Upstream, data: dict) -> bool:
        """True si hay que dejar de leer (la conexión se cierra)."""
        mt = data.get("message_type")
        text = data.get("text")
        if mt == "session_started":
            up.ready.set()
        elif mt == "partial_transcript":
            if isinstance(text, str) and text:
                await self._cb.on_transcription(text, False)
        elif mt == "committed_transcript":
            # Cubre el audio anterior; el que llegó después (o hace menos de 0,3 s, si la
            # confirmación fue del VAD del servidor) sigue pendiente de confirmar.
            if up.commit_sent:
                up.commit_sent = False
                if not self._audio_since_commit:
                    self._uncommitted = False
            elif time.monotonic() - (self._last_fwd or 0.0) > 0.3:
                self._uncommitted = False
            if isinstance(text, str) and text:
                self.last_transcript_at = time.monotonic()
                await self._cb.on_transcription(text, True)
            up.final.set()
        elif mt == "warning":
            await self._cb.on_warning("provider_warning", "")
        elif mt in _IGNORED_ERRORS:
            logger.debug("ElevenLabs STT: aviso del proveedor ignorado (%s)", mt)
        elif mt in _AUTH_ERRORS:
            up.outcome = "auth"
            up.closing = True
            self._p._mark_terminal("auth")
            return True
        elif mt in _TERMINAL_QUOTA:
            up.outcome = "quota"
            up.closing = True
            self._p._mark_terminal("quota")
            return True
        elif mt in _CAPACITY_ERRORS:
            up.outcome = "capacity"
            up.closing = True
            await self._on_capacity()
            return True
        elif mt in _OTHER_ERRORS:
            logger.warning("ElevenLabs STT: error del proveedor (%s); se cierra la conexión", mt)
            up.outcome = "error"
            up.closing = True
            return True
        return False

    # --- envío ---

    async def _pump(self) -> None:
        """Envía el búfer (trozos ≥ 0,1 s) y, si procede, la confirmación de frase."""
        async with self._send_lock:
            up = self._conn
            if (
                up is None
                or up.closing
                or up.draining
                or not up.ready.is_set()
                or up.ws is None
            ):
                return
            try:
                while len(self._buf) >= MIN_CHUNK_BYTES:
                    n = min(len(self._buf), MAX_CHUNK_BYTES)
                    await self._send_chunk(up, bytes(self._buf[:n]), commit=False)
                    del self._buf[:n]
                if self._want_commit:
                    wait = self._commit_wait()
                    if wait > 0:
                        self._schedule_commit(wait)
                    else:
                        await self._send_commit(up)
            except ConnectionClosed:
                return  # el lector ve el cierre y lo trata

    def _commit_wait(self) -> float:
        if self._last_commit_at is None:
            return 0.0
        return max(0.0, MIN_COMMIT_GAP_S - (time.monotonic() - self._last_commit_at))

    def _schedule_commit(self, delay: float) -> None:
        if self._commit_timer is not None and not self._commit_timer.done():
            return

        async def later() -> None:
            await asyncio.sleep(delay)
            await self._pump()

        self._commit_timer = self._spawn(later())

    async def _send_commit(self, up: _Upstream) -> None:
        rest = bytes(self._buf)
        self._buf.clear()
        up.final.clear()
        up.commit_sent = True
        self._audio_since_commit = False
        self._want_commit = False
        self._uncommitted = False
        await self._send_chunk(up, rest, commit=True)
        self._last_commit_at = time.monotonic()

    async def _send_chunk(self, up: _Upstream, pcm: bytes, *, commit: bool) -> None:
        msg = {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode(),
            "commit": commit,
            "sample_rate": SAMPLE_RATE,
        }
        await up.ws.send(json.dumps(msg))
        self._audio_bytes_sent += len(pcm)
        self._p._counters["audio_seconds_sent"] += len(pcm) / BYTES_PER_S

    # --- cierre ---

    async def _graceful_close(self, *, reason: str = "close") -> None:
        """Confirma lo pendiente, espera el final (≤ DRAIN_S) y cierra la conexión."""
        up = self._conn
        life = self._life
        if up is not None and life is not None and not life.done():
            drain = self._p._settings.ELEVENLABS_STT_DRAIN_S
            up.draining = True
            try:
                if (self._uncommitted or self._buf) and not up.closing:
                    await self._drain(up, drain)
            except Exception as e:
                logger.debug("ElevenLabs STT: drenado falló (%s)", describe_error(e))
            up.closing = True
            if up.ws is not None:
                await self._close_ws(up.ws)
            if not life.done():
                try:
                    await asyncio.wait_for(asyncio.shield(life), 1.5)
                except (TimeoutError, asyncio.CancelledError, Exception):
                    life.cancel()
                    await asyncio.wait({life}, timeout=0.5)

    async def _drain(self, up: _Upstream, drain: float) -> None:
        try:
            await asyncio.wait_for(up.ready.wait(), drain)
        except TimeoutError:
            return
        if up.closing:
            return
        async with self._send_lock:
            if up.closing or up.ws is None:
                return
            try:
                while len(self._buf) >= MIN_CHUNK_BYTES:
                    n = min(len(self._buf), MAX_CHUNK_BYTES)
                    await self._send_chunk(up, bytes(self._buf[:n]), commit=False)
                    del self._buf[:n]
                if self._uncommitted or self._buf:
                    await self._send_commit(up)
                else:
                    return
            except ConnectionClosed:
                return
        try:
            await asyncio.wait_for(up.final.wait(), drain)
        except TimeoutError:
            pass

    # --- temporizador de reposo ---

    async def _idle_loop(self) -> None:
        s = self._p._settings
        idle = s.ELEVENLABS_STT_IDLE_CLOSE_S
        warm = s.ELEVENLABS_STT_WARM_HOLD_S
        tick = max(0.01, min(0.1 if warm > 0 else 0.25, idle / 4))
        while not self._closed:
            await asyncio.sleep(tick)
            now = time.monotonic()
            last = self._last_fwd
            if last is None:
                continue
            quiet = now - last >= idle
            if self._busy and quiet:
                await self._clear_busy()
            up = self._conn
            if not quiet or up is None or up.closing or up.draining or not up.ready.is_set():
                continue
            if warm > 0:
                if self._warm_deadline is None:
                    self._warm_deadline = now + warm
                if now < self._warm_deadline:
                    await self._feed_silence(up, tick)
                    continue
            self._warm_deadline = None
            up.draining = True
            self._spawn(self._graceful_close_idle(up))

    async def _feed_silence(self, up: _Upstream, seconds: float) -> None:
        async with self._send_lock:
            if up.closing or up.draining or up.ws is None:
                return
            try:
                await self._send_chunk(up, b"\x00\x00" * int(SAMPLE_RATE * seconds), commit=False)
            except ConnectionClosed:
                return

    async def _graceful_close_idle(self, up: _Upstream) -> None:
        if self._conn is not up or up.closing:
            return
        drain = self._p._settings.ELEVENLABS_STT_DRAIN_S
        try:
            if self._uncommitted or self._buf:
                await self._drain(up, drain)
        except Exception as e:
            logger.debug("ElevenLabs STT: drenado falló (%s)", describe_error(e))
        up.closing = True
        if up.ws is not None:
            await self._close_ws(up.ws)

    def _on_provider_degraded(self) -> None:
        self._buf.clear()
        self._uncommitted = False
        self._want_commit = False
        self._health.set_state(ConnectionState.DEGRADED)
        if self._life is not None and not self._life.done():
            self._life.cancel()


class ElevenLabsSttProvider:
    name = "elevenlabs"
    capabilities = SttCapabilities(partials=True, warmup_s=2.5)

    state = health_attr("state")
    on_state_change = health_attr("on_state_change")

    def __init__(self, settings) -> None:
        secret = settings.ELEVENLABS_API_KEY
        key = secret.get_secret_value().strip() if secret is not None else ""
        if not key:
            raise ValueError("ELEVENLABS_API_KEY")
        pin_websockets_logging()
        self._settings = settings
        self._key = key
        self._base = settings.ELEVENLABS_BASE_URL
        self._sem = asyncio.Semaphore(settings.ELEVENLABS_STT_MAX_SESSIONS)
        self._health = ServiceHealth("transcriber", ConnectionState.CONNECTED)
        self._refs = SessionRefCount()
        self._sessions: set[ElevenLabsSttSession] = set()
        self._terminal = False
        self._closed = False
        self._counters: dict = {
            "capacity_refusals": 0,
            "provider_429": 0,
            "auth_errors": 0,
            "upstream_opens": 0,
            "audio_seconds_sent": 0.0,
        }
        self._probe_at: float | None = None
        self._probe_ok = False

    def __repr__(self) -> str:
        return f"<ElevenLabsSttProvider state={self.state.value}>"

    def _stream_url(self, language: str) -> str:
        s = self._settings
        params: dict[str, str | int | float] = {
            "model_id": MODEL_ID,
            "audio_format": AUDIO_FORMAT,
            "language_code": language,
            "commit_strategy": s.ELEVENLABS_STT_COMMIT,
            "include_timestamps": "false",
            "enable_logging": "true" if s.ELEVENLABS_STT_ENABLE_LOGGING else "false",
        }
        if s.ELEVENLABS_STT_COMMIT == "vad":
            params["vad_silence_threshold_secs"] = s.ELEVENLABS_STT_VAD_SILENCE_S
        return ws_url(self._base, "/v1/speech-to-text/realtime", params)

    # --- puerto SpeechToTextProvider ---

    def open_session(
        self, *, client: Client, params: SttParams, callbacks: SttCallbacks
    ) -> ElevenLabsSttSession:
        return ElevenLabsSttSession(self, client, params, callbacks)

    def counters(self) -> dict:
        return dict(self._counters)

    def status(self) -> ServiceStatus:
        return self._health.status()

    async def start(self) -> None:
        try:
            await self.probe()
        except Exception as e:
            logger.warning("ElevenLabs STT: probe de arranque falló (%s)", describe_error(e))

    async def retire(self) -> None:
        await self._refs.retire(self.aclose)

    async def aclose(self) -> None:
        self._closed = True

    # --- salud ---

    def _mark_terminal(self, why: str) -> None:
        """Credenciales rechazadas o cuota agotada: degradado hasta reiniciar/cambiar."""
        self._counters["auth_errors"] += 1
        if not self._terminal:
            self._terminal = True
            logger.error(
                "ElevenLabs STT: proveedor degradado (%s) hasta reiniciar o cambiar de proveedor",
                why,
            )
        self._health.mark_attempt_failed(why)
        self._health.set_state(ConnectionState.DEGRADED)
        for s in list(self._sessions):
            s._on_provider_degraded()

    def _connect_error(self, kind: str) -> None:
        if kind == "auth":
            self._mark_terminal("auth")
        elif kind == "capacity":
            self._counters["provider_429"] += 1

    def _record_success(self) -> None:
        if not self._terminal:
            self._health.mark_connected()

    async def probe(self) -> bool:
        if self._closed or self._terminal:
            return False
        now = time.monotonic()
        ttl = self._settings.ELEVENLABS_HEALTH_TTL_S
        if self._probe_at is not None and now - self._probe_at < ttl:
            return self._probe_ok
        ok = await self._probe_once()
        self._probe_ok = ok
        self._probe_at = time.monotonic()
        return ok

    async def _probe_once(self) -> bool:
        """Abre el realtime y espera `session_started` o el cierre (≤ 5 s); cierra sin enviar
        audio, así que no factura."""
        connected = False
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_S):
                async with websockets.connect(
                    self._stream_url("es"),
                    additional_headers={"xi-api-key": self._key},
                    max_size=MAX_FRAME_BYTES,
                    close_timeout=1,
                    open_timeout=PROBE_CONNECT_TIMEOUT_S,
                ) as ws:
                    connected = True
                    try:
                        raw = await ws.recv()
                    except ConnectionClosed:
                        kind = classify_close(ws.close_code, ws.close_reason)
                        return self._probe_failed(kind, None)
                    try:
                        mt = json.loads(raw).get("message_type")
                    except (ValueError, AttributeError):
                        mt = None
                    if mt in _AUTH_ERRORS or mt in _TERMINAL_QUOTA:
                        self._mark_terminal("auth" if mt in _AUTH_ERRORS else "quota")
                        return False
                    if mt == "session_started":
                        self._record_success()
                        return True
                    return self._probe_failed("other", None)
        except TimeoutError as e:
            if not connected:  # el connect/handshake no terminó: no es un servicio sano
                return self._probe_failed(classify_exception(e), e)
            self._record_success()  # conectó y nadie rechazó las credenciales en el plazo
            return True
        except Exception as e:
            return self._probe_failed(classify_exception(e), e)

    def _probe_failed(self, kind: str, exc: BaseException | None) -> bool:
        if kind == "auth":
            self._mark_terminal("auth")
            return False
        if kind == "capacity":
            self._counters["provider_429"] += 1
        self._health.mark_attempt_failed(
            "capacity" if kind == "capacity" else (describe_error(exc) if exc else "connect_failed")
        )
        self._health.set_state(ConnectionState.RECONNECTING)
        return False
