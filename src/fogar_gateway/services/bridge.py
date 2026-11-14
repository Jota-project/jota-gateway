import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from fogar_gateway.core.config import settings
from fogar_gateway.core.exceptions import ClientInactive, ClientNotFound
from fogar_gateway.models.schemas import Client, ClientConfig, Handshake
from fogar_gateway.services.db_client import db_client
from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry
from fogar_gateway.services.outbound import OutboundSender, log_send_failed
from fogar_gateway.services.pipeline_tracker import PipelineTracker
from fogar_gateway.services.protocol import OrchestratorProtocol
from fogar_gateway.services.reconnection import (
    ConnectionState,
    describe_error,
    to_wire_state,
    traceback_no_message,
)
from fogar_gateway.services.session_registry import SessionStatus
from fogar_gateway.services.speech.ports import (
    SpeechToTextProvider,
    SttCallbacks,
    SttParams,
    SttSession,
    TextToSpeechProvider,
)
from fogar_gateway.services.speech.registry import SpeechProviders
from fogar_gateway.services.stats import CloseReason, gateway_stats
from fogar_gateway.services.teardown import Teardown, cancel_requested
from fogar_gateway.services.turns import TurnController

logger = logging.getLogger(__name__)

# Silence-watchdog poll interval (issue #129). Each tick with no transcription for
# longer than `silence_timeout_s` is one strike (and one `degraded` notice), so
# `max_silence_turns` counts ticks, not whole `silence_timeout_s` windows.
_WATCHDOG_POLL_S = 2
# Tests swap this name for a virtual clock; patching asyncio.sleep would change it process-wide.
_sleep = asyncio.sleep


_REASON_BY_STATUS: dict[str, CloseReason] = {
    "revoked": "revoked",
    "error": "error",
}


class FogarBridge:
    """Una sesión de cliente: bucle de entrada, STT, watchdogs, revalidación y cierre.

    Los turnos (normal y push: abrir, alimentar, TTS, cortar, cerrar) son de
    `TurnController` (`self.turns`, services/turns.py).
    """

    def __init__(
        self,
        client: Client,
        config: ClientConfig,
        client_ws: WebSocket,
        orchestrator: OrchestratorProtocol,
        speech: SpeechProviders,
        tracker: PipelineTracker,
        handshake: Handshake,
        client_registry: ClientRegistry,
        default_agent: str,
        sender: OutboundSender,
        turn_registry: TurnRegistry | None = None,
    ):
        # Contada al crearse: toda sesión que luego cierra close_all() cuenta como abierta,
        # también la que falla antes de run() (1011, ready fallido…).
        gateway_stats.session_opened()
        self.client = client
        self.config = config
        self.client_id = client.id  # nombre legible del cliente (hab_sito, fogar_desktop…)
        self.client_ws = client_ws
        # All client-bound writes go through the sender so their order across tasks
        # is the enqueue order (issue #130 S1). client_ws stays for receive()/close.
        self._sender: OutboundSender = sender
        self.handshake: Handshake = handshake
        self.orchestrator: OrchestratorProtocol = orchestrator  # injected, not created here
        self.tts: TextToSpeechProvider = speech.tts
        self._stt: SpeechToTextProvider = speech.stt
        self.tracker: PipelineTracker = tracker
        self._client_registry = client_registry
        # Máximo de la cola de turno (#219) y lápidas de los runs de push cortados (#285).
        self._turn_registry = turn_registry
        self._default_agent = default_agent
        self.transcriber: SttSession | None = None

        self.tasks: list[asyncio.Task] = []
        # Fire-and-forget tasks (status notifications, the idle-watchdog closer) are not
        # part of the session lifecycle managed by `tasks`/close_all() — they're
        # short-lived and self-cleaning. Kept here only so the event loop's weak
        # reference doesn't let them get GC'd mid-flight.
        self._background_tasks: set[asyncio.Task] = set()
        # True while the transcriber reports `status busy` (GPU saturated, #143).
        self._transcriber_busy: bool = False
        self._session_start: float = 0.0
        self._last_client_activity: float = 0.0
        # Guards close_all() so it's safe to call more than once (issue #101):
        # routes.py's outer try/finally always calls it on the way out, even
        # when bridge.run() already called it from its own internal finally.
        # _closed is set once every teardown step ran (even if the caller was
        # cancelled meanwhile, #221); _close_lock serializes concurrent callers.
        self._closed: bool = False
        self._close_lock = asyncio.Lock()
        # Motivo de cierre (#219): la primera nota gana; se fija en session_end una vez.
        self._close_reason: CloseReason | None = None
        self._turn_in_flight_at_close: bool | None = None
        self._close_counted: bool = False
        # Audio activity, for the silence watchdog (#227): last audio frame, and the
        # first frame of the current burst (a frame after an idle gap starts a new one).
        self._last_audio_at: float | None = None
        self._audio_burst_start: float | None = None
        self.turns = TurnController(
            client_id=self.client_id,
            config=config,
            handshake=handshake,
            orchestrator=orchestrator,
            tts=self.tts,
            tracker=tracker,
            sender=sender,
            session_key=self._session_key,
            spawn=self._spawn,
            supervise=self._supervise,
            turn_registry=turn_registry,
        )

    async def connect_internal_services(self):
        """Inicializa clientes de microservicios dependiendo del handshake."""
        if self.handshake.input_mode == "audio":
            # Las callbacks se entregan al abrir la sesión (no en run()): el bucle de
            # entrada del cliente arranca antes que run() y el primer evento no puede
            # quedarse sin destinatario.
            self.transcriber = self._stt.open_session(
                client=self.client,
                params=SttParams(self.config.stt_language, self.config.stt_vad_thold),
                callbacks=SttCallbacks(
                    on_transcription=self._on_transcription,
                    on_warning=self._on_transcriber_warning,
                    on_status=self._on_transcriber_status,
                    on_incomplete=self._on_transcriber_incomplete,
                ),
            )
            await self.transcriber.start()
            # Wired *after* the initial connect on purpose: on_state_change firing
            # CONNECTED on a normal first-time success would send a spurious
            # "restored" notice for a session where nothing ever broke.
            # health_check() already reports the initial-failure case, if any.
            self.transcriber.on_state_change = self._on_transcriber_state_change

        self._client_registry.register(self.client_id, self)

    def _session_key(self) -> str:
        from fogar_gateway.core.session_key import make_session_key

        # _default_agent es el agente ya resuelto y normalizado por resolve_agent (incluye
        # handshake.agent sin espacios): " main" debe ser la misma sesión que "main".
        return make_session_key(self._default_agent, self.client_id)

    def snapshot(self) -> dict[str, Any]:
        """Estado vivo de la sesión para /admin/state (#219): solo números, estados e
        IDs, nunca texto ni client_key. `started_at` y `last_latencies` los añade la ruta
        desde el SessionRecord (aquí van a None)."""
        now = time.monotonic()
        turns = self.turns.snapshot(now)
        depth, peak = (
            self._turn_registry.depth(self._session_key())
            if self._turn_registry is not None
            else (0, 0)
        )
        return {
            "session_id": self.tracker.session_id,
            "client_id": self.client_id,
            "client_name": self.client.name,
            "input_mode": self.handshake.input_mode,
            "output_mode": list(self.handshake.output_mode),
            "started_at": None,
            "age_s": round(now - self._session_start, 3) if self._session_start else 0.0,
            "turn": turns["turn"],
            "turn_count": self.tracker.turn_count,
            "sender": self._sender.queue_stats(),
            "turn_queue": {"depth": depth, "peak": peak},
            "push_backlog": turns["push_backlog"],
            "last_activity_s": (
                round(now - self._last_client_activity, 3) if self._last_client_activity else None
            ),
            "stt_state": self.transcriber.state.value if self.transcriber is not None else None,
            "last_latencies": None,
        }

    def note_close_reason(self, reason: CloseReason | None) -> None:
        """Anota por qué se cierra la sesión; la primera llamada gana (#219)."""
        if self._close_reason is None:
            self._close_reason = reason

    def _session_end_meta(self, status: SessionStatus) -> dict[str, Any]:
        """Motivo y máximos de las colas para session_end; solo números y literales."""
        reason = self._close_reason or _REASON_BY_STATUS.get(status, "unknown")
        meta: dict[str, Any] = {
            "reason": reason,
            "turn_in_flight": bool(self._turn_in_flight_at_close),
            "sender_peak_msgs": 0,
            "sender_peak_bytes": 0,
            "turn_queue_peak": 0,
            "push_backlog_peak": self.turns.push_backlog_peak,
        }
        # Una métrica nunca debe impedir el cierre.
        try:
            q = self._sender.queue_stats()
            meta["sender_peak_msgs"] = int(q["peak_msgs"])
            meta["sender_peak_bytes"] = int(q["peak_bytes"])
        except Exception as e:  # noqa: BLE001 — una métrica nunca impide el cierre
            logger.debug(f"[{self.client_id}] sender_peak no disponible: {type(e).__name__}")
        if self._turn_registry is not None:
            try:
                meta["turn_queue_peak"] = self._turn_registry.pop_peak(self._session_key())
            except Exception as e:  # noqa: BLE001 — una métrica nunca impide el cierre
                logger.debug(f"[{self.client_id}] turn_queue_peak no disponible: {type(e).__name__}")
        return meta

    async def close_all(
        self, status: SessionStatus | None = None, reason: CloseReason | None = None
    ):
        """Tear down every microservice client and mark the session closed.

        Idempotent — the second call in a row is a no-op, and the *first
        call to finish* wins the `status`. Callable from several independent
        sites (the watchdogs, run()'s own finally, and routes.py's outer
        finally on the setup-phase paths from issue #101) without knowing
        whether one of the others already ran. Without an explicit `status`,
        a session whose close reason is `error` ends as `error` (G-26), any
        other as `completed`.

        Every step runs to the end, each bounded by SESSION_TEARDOWN_S, even if
        the caller is cancelled meanwhile (#221): `_closed` is set before that
        cancellation is re-raised, so the routes.py safety net (#101) does not
        repeat the teardown.
        """
        # Before taking the lock, so a `send` arriving while another close_all() is
        # running is already refused (G-09), and push intake stops.
        self.turns.stop_intake()
        self.note_close_reason(reason)
        if self._turn_in_flight_at_close is None:
            self._turn_in_flight_at_close = self.turns.in_flight()
        async with self._close_lock:
            if self._closed:
                return
            if status is None:
                status = "error" if self._close_reason == "error" else "completed"

            self._client_registry.unregister(self.client_id, self)
            limit = settings.SESSION_TEARDOWN_S
            async with Teardown(f"[{self.client_id}] close_all") as td:
                # The turns first: the one in flight is cancelled (never drained) and every
                # open turn gets its turn_end while the socket is still open (G-10, G-23).
                await self.turns.close(td, limit)

                # Never the calling task: the watchdogs call close_all() on themselves.
                # Cancel them all first, then wait them under one shared limit.
                current_task = asyncio.current_task()
                pending = [t for t in self.tasks if t is not current_task and not t.done()]
                for task in pending:
                    task.cancel()
                loop = asyncio.get_running_loop()
                deadline = loop.time() + limit
                for task in pending:
                    remaining = max(0.0, deadline - loop.time())
                    await td.wait(task, f"tarea {task.get_name()}", remaining)

                if self.transcriber:
                    await td.step(self.transcriber.close(), "cierre del transcriber", limit)

                end_meta = self._session_end_meta(status)
                if not self._close_counted:
                    self._close_counted = True
                    gateway_stats.session_closed(end_meta["reason"])
                await td.step(
                    self.tracker.close(status=status, **end_meta), "cierre del tracker", limit
                )
                self._closed = True

    async def health_check(self) -> dict[str, bool] | None:
        """Ping each microservice and return a snapshot of live capabilities.

        Returns None if the orchestrator is unavailable — the caller must close
        the WebSocket and skip sending `ready`. For non-fatal services the
        snapshot records their current availability, and the same `status`
        warnings sent today are still emitted so the client sees both the
        pre-ready notification and the authoritative capabilities block.
        """
        if not await self.orchestrator.ping():
            await self.notify_service_status("orchestrator", "unavailable", propagate=True)
            return None

        transcriber_requested = self.handshake.input_mode == "audio"
        tts_requested = "audio" in self.handshake.output_mode

        if transcriber_requested:
            transcriber_live = bool(
                self.transcriber and self.transcriber.state == ConnectionState.CONNECTED
            )
            if not transcriber_live:
                await self.notify_service_status("transcriber", "unavailable", propagate=True)
        else:
            transcriber_live = False

        if tts_requested:
            tts_live = bool(await self.tts.probe())
            if not tts_live:
                await self.notify_service_status("tts", "unavailable", propagate=True)
        else:
            tts_live = False

        return {
            "barge_in": bool(self.config.barge_in_enabled)
            and transcriber_live
            and self._stt.capabilities.partials,
            "tts": tts_live,
            "transcriber": transcriber_live,
        }

    async def _transcription_watchdog(self):
        """Notifica al cliente si hay silencio prolongado; cierra tras max_silence_turns consecutivos."""
        # Esperar a que el cliente empiece a enviar audio antes de vigilar
        while self._last_audio_at is None:
            await _sleep(0.5)
            if not self.transcriber or self.transcriber.state == ConnectionState.DEGRADED:
                return

        silence_count = 0
        last_seen_transcription_at = self.transcriber.last_transcript_at
        was_connected = True
        recovery_baseline: float | None = None

        while True:
            await _sleep(_WATCHDOG_POLL_S)
            if not self.transcriber or self.transcriber.state == ConnectionState.DEGRADED:
                return
            if self.transcriber.state != ConnectionState.CONNECTED:
                was_connected = False
                continue  # RECONNECTING: skip silence-counting this tick, don't exit
            if self._transcriber_busy:
                # GPU saturated (#143): no transcriptions are expected. Skip counting
                # and reuse the recovery-baseline path so the busy period isn't
                # blamed on the client once it clears.
                was_connected = False
                continue

            current_transcription_at = self.transcriber.last_transcript_at
            if current_transcription_at != last_seen_transcription_at:
                silence_count = 0
                last_seen_transcription_at = current_transcription_at
                recovery_baseline = None
            elif not was_connected:
                # Just recovered from a drop with no new transcription yet.
                # TranscriberClient.connect() never resets _last_transcription_at,
                # so it still predates the outage — restart the silence clock from
                # the recovery moment instead of blaming the client for however
                # long the reconnect took (issue #149).
                silence_count = 0
                recovery_baseline = time.monotonic()
            was_connected = True

            now = time.monotonic()
            if self.turns.in_flight():
                # Decision of Sito (#215): a turn in progress is activity, not silence.
                # Strikes reset and the window restarts from now, so the first window
                # after the turn is a fresh one (no strike on the first tick after it).
                silence_count = 0
                recovery_baseline = now
                continue

            last_audio = self._last_audio_at
            if now - last_audio > self._audio_gap_s():
                # #227: the client is not sending audio (wake-word client idle between
                # turns, keepalive only). Silence is only counted while audio flows:
                # no strikes, no `degraded`, accumulated strikes are forgotten.
                silence_count = 0
                continue

            burst_start = self._audio_burst_start
            # A burst that opened after the last transcription starts a fresh window,
            # so a stale pre-idle timestamp is never blamed on the client.
            last = max(
                t for t in (recovery_baseline, current_transcription_at, burst_start) if t is not None
            )
            if last_audio <= last:
                # No audio since the window's reference point (the last transcription):
                # the client already said its piece and went quiet. Nothing to blame it for.
                silence_count = 0
                continue
            elapsed = now - last
            timeout = self.config.silence_timeout_s
            if last == burst_start:
                # First window after the burst opened: a provider that needs a while to
                # return its first result (`warmup_s`; fogar = 0) isn't blamed for it.
                timeout += self._stt.capabilities.warmup_s

            if elapsed > timeout:
                silence_count += 1
                logger.warning(
                    f"[{self.client_id}] Watchdog: {elapsed:.1f}s sin transcripción "
                    f"({silence_count}/{self.config.max_silence_turns})"
                )
                await self.notify_service_status("transcriber", "degraded")
                if silence_count >= self.config.max_silence_turns:
                    self.note_close_reason("silence_timeout")
                    await self.close_all()
                    return

    async def _idle_watchdog(self):
        """Cierra la sesión si el cliente no manda ningún mensaje (audio o texto) durante
        IDLE_TIMEOUT_S (issue #115). Sin aviso previo al cliente — a diferencia del silence
        watchdog, que sí notifica degradación progresiva de la transcripción.

        Gated on activity actually in flight (issue #115 follow-up): a client that goes
        quiet while the orchestrator is mid-turn, or a push-only session that never sends
        anything by design, must not be cut off just because IDLE_TIMEOUT_S has elapsed
        since the client's *last inbound message*. While `turns.in_flight()` (a turn, an
        open push, push audio still progressing or a queued push backlog, each bounded so
        an orphan can't gate this forever) the close is deferred and re-checked shortly
        after; the idle window is not reset.
        """
        while True:
            remaining = settings.IDLE_TIMEOUT_S - (
                time.monotonic() - self._last_client_activity
            )
            if remaining <= 0:
                if self.turns.in_flight():
                    # Not idle — re-check shortly rather than closing or resetting the window.
                    await _sleep(2)
                    continue
                logger.info(f"[{self.client_id}] Idle timeout — cerrando sesión.")
                self.note_close_reason("idle_timeout")
                await self.close_all()
                return
            await _sleep(remaining)

    async def _session_revalidation_loop(self) -> None:
        """Revalida la sesión contra la BD cada SESSION_REVALIDATE_INTERVAL_S (G-06, #218).

        Si el cliente fue borrado, desactivado o su clave rotada (`ClientNotFound` /
        `ClientInactive`) se cierra la sesión con 1008 «revoked». Cualquier otro error
        (BD ocupada, etc.) es transitorio: se registra solo el tipo y se reintenta en el
        siguiente tick, nunca cierra la sesión. La lectura va directa a la BD (sin caché,
        #265), así que un cambio desde /admin o la CLI se ve en el siguiente tick.
        """
        while True:
            await _sleep(settings.SESSION_REVALIDATE_INTERVAL_S)
            try:
                current, _ = await db_client.get_session(self.client.client_key)
                if current.id != self.client.id:
                    # Borrado y recreado con la MISMA clave: es otro cliente.
                    await self._revoke_session()
                    return
            except (ClientNotFound, ClientInactive):
                await self._revoke_session()
                return
            except Exception as e:  # noqa: BLE001 — frontera: un fallo transitorio no cierra la sesión
                logger.warning(
                    f"[{self.client_id}] Revalidación de sesión fallida "
                    f"({type(e).__name__}); se reintenta."
                )

    async def _revoke_session(self) -> None:
        """Cierra la sesión con 1008 «revoked» por el camino de cierre existente."""
        # Antes del close 1008: el bucle de entrada lo verá como desconexión del cliente.
        self.note_close_reason("revoked")
        logger.warning(f"[{self.client_id}] Cliente revocado (borrado/desactivado/clave rotada).")
        await self.tracker.record("session_revoked")
        # Nadie autorizado escucha ya: close_all cancela el turno en curso (G-18).
        self.turns.client_gone = True
        try:
            await asyncio.wait_for(
                self.client_ws.close(code=1008, reason="revoked"),
                timeout=settings.CLIENT_SEND_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
            logger.debug(f"[{self.client_id}] close 1008 revoked falló: {type(e).__name__}")
        await self.close_all(status="revoked", reason="revoked")

    def _spawn(self, coro, name: str) -> asyncio.Task:
        """Create a supervised session task and track it in self.tasks."""
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        self._supervise(task)
        return task

    def _supervise(self, task: asyncio.Task) -> None:
        """Log a session task's crash instead of leaving it unobserved (issue #131).

        A dead idle watchdog leaves the session without an idle timeout, so that
        one additionally closes the session.
        """

        def _on_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()  # always retrieved: no "never retrieved" warning
            if exc is None:
                return
            # Solo el tipo; el traceback sin mensaje a DEBUG (#238).
            logger.error(
                f"[{self.client_id}] Tarea de sesión '{t.get_name()}' falló: {describe_error(exc)}"
            )
            logger.debug(f"[{self.client_id}] '{t.get_name()}': {traceback_no_message(exc)}")
            if t.get_name() == "idle_watchdog":
                self.note_close_reason("error")
                self._spawn_background(self.close_all(), "idle_watchdog_close")

        task.add_done_callback(_on_done)

    def _spawn_background(self, coro, name: str) -> None:
        """Fire-and-forget task, supervised: its failure is logged by type (#221)."""
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        self._supervise(task)

    def _log_send_failed(self, what: str, e: BaseException) -> None:
        log_send_failed(self.client_id, what, e)

    async def run(self):
        self._session_start = time.monotonic()
        self._last_client_activity = time.monotonic()
        await self.tracker.record(
            "session_start",
            input_mode=self.handshake.input_mode,
            output_mode=self.handshake.output_mode,
        )

        # Loop principal de lectura del cliente
        # Handle kept directly: the push worker may already sit in self.tasks[0]
        # (enqueue_push can run between connect_internal_services() and run()).
        client_task = self._spawn(self._client_input_loop(), "client_input_loop")
        # Idle watchdog: cierra la sesión si el cliente no manda nada (issue #115).
        self._spawn(self._idle_watchdog(), "idle_watchdog")
        # Revocación de clientes borrados/desactivados/rotados con la sesión abierta (G-06).
        self._spawn(self._session_revalidation_loop(), "session_revalidation")

        # Loop del Transcriptor (solo si hay audio de entrada)
        if self.transcriber:
            self._spawn(
                self.transcriber.run(),
                "transcriber_run",
            )
            self._spawn(self._transcription_watchdog(), "transcription_watchdog")

        # El ciclo de vida de la sesión lo marca _client_input_loop.
        # listen_loop y watchdog corren en background y terminan solos sin cerrar la sesión.
        status: SessionStatus | None = None
        try:
            await client_task
            # Terminó sola (no la canceló un watchdog ni el shutdown): el cliente se fue.
            self.note_close_reason("client_disconnect")
        except asyncio.CancelledError:
            if cancel_requested():
                # Cancelación ajena de run() (G-26): la sesión acaba en error y la
                # cancelación se relanza tras el cierre.
                self.note_close_reason("error")
                status = "error"
                raise
            # Si no, la cancelación es nuestra: un close_all() (watchdog, revocación)
            # canceló el bucle de entrada. Fin normal; el motivo ya está anotado.
        except Exception as e:  # noqa: BLE001 — frontera: el crash cierra la sesión como error
            self.note_close_reason("error")
            logger.error(f"[{self.client_id}] client_input_loop crasheó: {describe_error(e)}")
            logger.debug(f"[{self.client_id}] client_input_loop: {traceback_no_message(e)}")
        finally:
            await self.close_all(status=status)

    async def _client_input_loop(self):
        """Atrapa entradas del cliente (Micrófono o Texto)."""
        try:
            while True:
                message = await self.client_ws.receive()
                self._last_client_activity = time.monotonic()

                if message.get("type") == "websocket.disconnect":
                    logger.info(f"[{self.client_id}] Cliente físico desconectado.")
                    self.turns.client_gone = True
                    self._note_disconnect_code(message.get("code"))
                    break

                # AUDIO → Transcriber
                if "bytes" in message:
                    if self.transcriber:
                        now = time.monotonic()
                        # A frame after an idle gap opens a new audio burst (#227).
                        if (
                            self._last_audio_at is None
                            or now - self._last_audio_at > self._audio_gap_s()
                        ):
                            self._audio_burst_start = now
                        self._last_audio_at = now
                        await self.transcriber.send_audio(message["bytes"])

                # TEXTO → Orchestrator (prompt directo desde un cliente de texto)
                elif "text" in message:
                    text_data = message["text"]
                    try:
                        json_msg = json.loads(text_data)
                        parsed = True
                    except (ValueError, RecursionError):
                        # No es JSON (RecursionError: anidamiento excesivo, `[[[[…`).
                        json_msg = None
                        parsed = False

                    if not parsed:
                        if self.handshake.input_mode == "text":
                            # Texto plano en modo texto — compatibilidad con clientes de texto puro
                            await self.turns.start_user_turn(text_data)
                    elif not isinstance(json_msg, dict):
                        # G-20: a JSON scalar/list (`42`, `[1]`, `"s"`, `true`, `null`)
                        # is not a protocol message. Non-fatal error, session goes on.
                        await self._send_invalid_message()
                    elif json_msg.get("type") == "end":
                        # Cliente terminó de hablar → señalizar fin al transcriber
                        if self.transcriber:
                            await self.transcriber.send_end()

                    elif json_msg.get("type") == "cancel":
                        # Cliente cancela el turn activo (o el push que suena) sin lanzar uno nuevo
                        await self.turns.cancel("cancelled")

                    elif json_msg.get("type") == "send":
                        # Cliente confirma/edita la transcripción y la envía al orquestador
                        raw_text = json_msg.get("text", "")
                        if not isinstance(raw_text, str):
                            await self._send_invalid_message()
                        else:
                            text = raw_text.strip()
                            if text:
                                logger.info(f"[{self.client_id}] send recibido: len={len(text)}")
                                await self.turns.start_user_turn(text)

        except WebSocketDisconnect as e:
            logger.info(f"[{self.client_id}] Cliente físico desconectado.")
            self.turns.client_gone = True
            self._note_disconnect_code(e.code)
        except Exception as e:  # noqa: BLE001 — frontera: el bucle de entrada termina la sesión
            self.note_close_reason("error")
            logger.error(f"[{self.client_id}] Error en input loop: {describe_error(e)}")
            logger.debug(f"[{self.client_id}] input loop: {traceback_no_message(e)}")

    def _note_disconnect_code(self, code: Any) -> None:
        # uvicorn cierra cada WS con 1012 antes del shutdown del lifespan: no es el cliente.
        if code == 1012:
            self.note_close_reason("shutdown")

    def _audio_gap_s(self) -> float:
        """Silence in the audio stream after which the client counts as idle (#227).

        `silence_timeout_s`, floored at one watchdog poll. An always-streaming client
        sends frames far more often than that, so for it nothing changes; a client
        that only sends audio after a local wake word is idle between turns.
        """
        return max(self.config.silence_timeout_s, _WATCHDOG_POLL_S)

    async def _send_invalid_message(self) -> None:
        """Non-fatal error for a malformed client message (G-20). Never echoes the payload."""
        try:
            await self._sender.send_json(
                {
                    "type": "error",
                    "code": "INVALID_MESSAGE",
                    "message": "invalid message",
                    "fatal": False,
                }
            )
        except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
            self._log_send_failed("INVALID_MESSAGE", e)

    async def _on_transcriber_warning(self, code: str, message: str | None):
        """Reenvía warnings del transcriber al cliente (e.g. buffer_full)."""
        await self.notify_service_status(
            "transcriber", "degraded", code=code, message=message or code
        )

    async def _on_transcriber_status(self, state: str, reason: str | None) -> None:
        """Transcriber capacity signal (#143): `busy` → degraded, `ok` → restored.

        Transition-only: a repeated `busy`, or an `ok` that was never preceded by
        a `busy` (the transcriber's initial status), notifies nothing.
        """
        if state == "busy" and not self._transcriber_busy:
            self._transcriber_busy = True
            code = reason or "busy"
            await self.notify_service_status("transcriber", "degraded", code=code, message=code)
        elif state == "ok" and self._transcriber_busy:
            self._transcriber_busy = False
            await self.notify_service_status("transcriber", "restored")
        elif state not in ("busy", "ok"):
            logger.debug(f"[{self.client_id}] Transcriber status desconocido ignorado: {state!r}")

    async def _on_transcriber_incomplete(self, reason: str | None) -> None:
        """A final transcription was truncated by the transcriber (complete=false, #143)."""
        await self.tracker.record("transcription_incomplete", reason=reason)

    async def notify_service_status(
        self,
        service: str,
        state: str,
        code: str | None = None,
        message: str | None = None,
        *,
        propagate: bool = False,
    ) -> None:
        payload: dict[str, str] = {"type": "status", "service": service, "state": state}
        if code is not None:
            payload["code"] = code
        if message is not None:
            payload["message"] = message
        try:
            await self._sender.send_json(payload)
        except Exception as e:
            if propagate:
                raise
            self._log_send_failed(f"status {service}", e)

    def _on_transcriber_state_change(self, state: ConnectionState) -> None:
        self._spawn_background(
            self.notify_service_status("transcriber", to_wire_state(state)), "transcriber_status"
        )

    async def _on_transcription(self, text: str, is_final: bool):
        """Callback dispatched by TranscriberClient on every transcription event.

        Partials: trigger barge-in if text is substantial and a turn is active.
        Finals: cancel any running turn, notify the client, start a new turn.
        All client_ws sends are guarded — the client may disconnect at any time.
        """
        if not is_final:
            # Forward partial to client for live display
            try:
                await self._sender.send_json({"type": "transcription_partial", "text": text})
            except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
                self._log_send_failed("transcription_partial", e)
                return
            await self.tracker.record("transcription_partial", text_len=len(text))

            # Barge-in: interrupt active turn if enabled and partial is substantial enough
            if self.config.barge_in_enabled and len(text) >= self.config.barge_in_min_chars:
                # The turn or the push open now: one the worker opens after the cancel stays.
                cancelled, cancelled_turn_id = await self.turns.cancel("interrupted")
                if cancelled:
                    logger.info(
                        f"[{self.client_id}] Barge-in: turno cancelado por parcial len={len(text)}"
                    )
                    await self.tracker.record("barge_in")
                    interrupted: dict[str, str] = {"type": "interrupted"}
                    if cancelled_turn_id:
                        interrupted["turn_id"] = cancelled_turn_id
                    try:
                        # After the cancelled turn's own turn_end (G-10).
                        await self._sender.send_json(interrupted)
                    except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
                        self._log_send_failed("interrupted", e)
            return  # partials never reach the orchestrator

        await self.tracker.record("transcription_final", text_len=len(text))

        # Final: cancel any running turn (not a push: the `send` that follows cuts it),
        # notify client. El orquestador se llama cuando el cliente envíe {"type": "send"}.
        await self.turns.cancel("interrupted", push=False)
        logger.debug(f"[{self.client_id}] Transcripción final: '{text[:40]}'")
        try:
            await self._sender.send_json({"type": "transcription", "text": text})
        except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
            logger.warning(
                f"[{self.client_id}] send_json(transcription) falló: {describe_error(e)}"
            )

    def enqueue_push(self, kind: str, arg: Any, run_id: str | None = None) -> None:
        """Entry point of FrameDispatcher for push events (synchronous, never blocks)."""
        self.turns.enqueue_push(kind, arg, run_id)

    async def push_idle(self) -> None:
        """Wait until every queued push event has been handled (for tests)."""
        await self.turns.push_idle()
