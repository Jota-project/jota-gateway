import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from src.core.config import settings
from src.core.exceptions import ClientInactive, ClientNotFound
from src.models.schemas import Client, ClientConfig, Handshake
from src.services.db_client import db_client
from src.services.openclaw.chat_text import ChatTextTracker
from src.services.openclaw.models import ToolCallEvent
from src.services.openclaw.registry import ClientRegistry, TurnRegistry
from src.services.orchestration import OrchestratorError
from src.services.outbound import ClientGone, DirectSender, OutboundSender
from src.services.pipeline_tracker import PipelineTracker
from src.services.protocol import OrchestratorProtocol
from src.services.reconnection import (
    ConnectionState,
    describe_error,
    to_wire_state,
    traceback_no_message,
)
from src.services.session_registry import SessionStatus
from src.services.speech.ports import (
    SpeechToTextProvider,
    SttCallbacks,
    SttParams,
    SttSession,
    TextToSpeechProvider,
    TtsStream,
)
from src.services.speech.registry import SpeechProviders
from src.services.stats import CloseReason, gateway_stats

logger = logging.getLogger(__name__)

# Silence-watchdog poll interval (issue #129). Each tick with no transcription for
# longer than `silence_timeout_s` is one strike (and one `degraded` notice), so
# `max_silence_turns` counts ticks, not whole `silence_timeout_s` windows.
_WATCHDOG_POLL_S = 2


def _tool_call_message(turn_id: str | None, tool_call: ToolCallEvent) -> dict:
    return {
        "type": "tool_call",
        "turn_id": turn_id,
        "phase": tool_call.phase,
        "name": tool_call.name,
        "tool_call_id": tool_call.tool_call_id,
        "args": tool_call.args,
        "result": tool_call.result,
        "is_error": tool_call.is_error,
    }


_REASON_BY_STATUS: dict[str, CloseReason] = {
    "shutdown": "shutdown",
    "revoked": "revoked",
    "error": "error",
}


class JotaBridge:
    """
    Titiritero principal. Gestiona la conexión de un cliente físico
    y enruta asincrónicamente los mensajes utilizando los adaptadores de microservicio.
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
        sender: OutboundSender | None = None,
        turn_registry: TurnRegistry | None = None,
    ):
        # Contada al crearse: toda sesión que luego cierra close_all() cuenta como abierta,
        # también la que falla antes de run() (1011, ready fallido…).
        gateway_stats.session_opened()
        self.client = client
        self.config = config
        self.client_id = client.id  # nombre legible del cliente (hab_sito, jota_desktop…)
        self.client_ws = client_ws
        # All client-bound writes go through the sender so their order across tasks
        # is the enqueue order (issue #130 S1). client_ws stays for receive()/close.
        self._sender: OutboundSender = sender or DirectSender(client_ws)
        self.handshake: Handshake = handshake
        self.orchestrator: OrchestratorProtocol = orchestrator  # injected, not created here
        # Cada turno TTS y cada sesión STT nueva usa el proveedor VIGENTE de `speech`; una
        # sesión STT ya abierta conserva el suyo hasta que termina.
        self._speech = speech
        self._session_stt: SpeechToTextProvider | None = None  # el que abrió la sesión
        self.tracker: PipelineTracker = tracker
        self._client_registry = client_registry
        self._turn_registry = turn_registry  # solo para el máximo de la cola de turno (#219)
        self._default_agent = default_agent
        self.transcriber: SttSession | None = None
        self._push_tts: TtsStream | None = None
        # Tiempos del TTS del push en curso (ms); None si el turno no tuvo TTS.
        self._push_tts_stats: dict[str, Any] | None = None
        self._push_audio_task: asyncio.Task | None = None

        self.tasks: list[asyncio.Task] = []
        # Fire-and-forget tasks (status notifications, the idle-watchdog closer) are not
        # part of the session lifecycle managed by `tasks`/close_all() — they're
        # short-lived and self-cleaning. Kept here only so the event loop's weak
        # reference doesn't let them get GC'd mid-flight.
        self._background_tasks: set[asyncio.Task] = set()
        self._active_turn: asyncio.Task | None = None
        self._active_turn_started_at: float | None = None  # para snapshot() (#219)
        # True while the transcriber reports `status busy` (GPU saturated, #143).
        self._transcriber_busy: bool = False
        self._session_start: float = 0.0
        self._last_client_activity: float = 0.0
        self._turn_seq: int = 0
        self._push_turn_id: str | None = None
        # Issue #84: OpenClaw emits multiple agent start/end pairs during a single
        # LLM response (tool use, multi-step reasoning). Track whether a push turn
        # is already open so we emit exactly one turn_start/turn_end to the client
        # for the whole multi-step reply, not one per agent event.
        self._push_turn_open: bool = False
        self._push_text = ChatTextTracker()  # #241: text already emitted in the push reply
        # Bounds how long an open push turn can gate _idle_watchdog() (issue #115
        # follow-up): a push has no timeout of its own the way _active_turn does
        # via stream_response()'s TURN_TIMEOUT_S, so an orphaned push (agent
        # start with no matching agent end — OpenClaw crash, reconnect mid-push)
        # combined with a silent client would otherwise block the idle watchdog
        # from ever calling close_all() and leak the session indefinitely.
        self._push_turn_opened_at: float | None = None
        # Push worker (issue #130 S3a): the dispatcher no longer awaits push handlers
        # inside OpenClaw's _listen loop. It calls enqueue_push(); a single worker per
        # bridge runs the handlers, so one session's slow TTS can't stall every other
        # session multiplexed on the same connection, and the total order
        # start -> deltas -> tool -> end the _push_turn_open logic relies on is kept.
        # Unbounded on purpose in S3a (bounding it is S3b).
        self._push_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self._push_worker: asyncio.Task | None = None
        self._push_accepting: bool = True
        # Events enqueued and not yet finished, and since when the backlog is
        # non-empty: _idle_watchdog() counts a bounded pending backlog as in flight.
        self._push_pending: int = 0
        self._push_pending_peak: int = 0
        self._push_pending_since: float | None = None
        # Guards close_all() so it's safe to call more than once (issue #101):
        # routes.py's outer try/finally always calls it on the way out, even
        # when bridge.run() already called it from its own internal finally.
        # _closed is only set True once teardown fully completes; _close_lock
        # serializes concurrent callers so a call interrupted mid-teardown
        # doesn't race a second one, and a later call retries instead of
        # observing a half-finished, permanently-poisoned guard.
        self._closed: bool = False
        self._close_lock = asyncio.Lock()
        # Set on entry to close_all() and never reset: from then on no new turn may
        # start (G-09, #215). Distinct from `_closed`, which only flips once teardown
        # completes so an interrupted close_all() can be retried (#101).
        self._closing: bool = False
        # Motivo de cierre (#219): la primera nota gana; se fija en session_end una vez.
        self._close_reason: CloseReason | None = None
        self._turn_in_flight_at_close: bool | None = None
        self._close_counted: bool = False
        # Serializes turn cancel/start (G-08, #201).
        self._turn_lock = asyncio.Lock()
        # Id of the turn currently running (set after its turn_start is enqueued).
        self._active_turn_id: str | None = None
        # True once the client is known to be gone (disconnect seen by the input loop,
        # or ClientGone from the sender): its turn is cancelled, not drained (G-18).
        self._client_gone: bool = False
        # Audio activity, for the silence watchdog (#227): last audio frame, and the
        # first frame of the current burst (a frame after an idle gap starts a new one).
        self._last_audio_at: float | None = None
        self._audio_burst_start: float | None = None

    @property
    def tts(self) -> TextToSpeechProvider:
        return self._speech.tts

    @property
    def _stt(self) -> SpeechToTextProvider:
        return self._speech.stt

    async def connect_internal_services(self):
        """Inicializa clientes de microservicios dependiendo del handshake."""
        if self.handshake.input_mode == "audio":
            # Las callbacks se entregan al abrir la sesión (no en run()): el bucle de
            # entrada del cliente arranca antes que run() y el primer evento no puede
            # quedarse sin destinatario.
            self._session_stt = self._stt
            self.transcriber = self._session_stt.open_session(
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
        from src.core.session_key import make_session_key

        # _default_agent es el agente ya resuelto y normalizado por resolve_agent (incluye
        # handshake.agent sin espacios): " main" debe ser la misma sesión que "main".
        return make_session_key(self._default_agent, self.client_id)

    def snapshot(self) -> dict[str, Any]:
        """Estado vivo de la sesión para /admin/state (#219): solo números, estados e
        IDs, nunca texto ni client_key. `started_at` y `last_latencies` los añade la ruta
        desde el SessionRecord (aquí van a None)."""
        now = time.monotonic()
        kind, since = "idle", None
        if self._active_turn is not None and not self._active_turn.done():
            kind, since = "normal", self._active_turn_started_at
        elif self._push_turn_open:
            kind, since = "push", self._push_turn_opened_at
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
            "turn": {"kind": kind, "since_s": round(now - since, 3) if since else None},
            "turn_count": self.tracker.turn_count,
            "sender": self._sender.queue_stats(),
            "turn_queue": {"depth": depth, "peak": peak},
            "push_backlog": {"pending": self._push_pending, "peak": self._push_pending_peak},
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
            "push_backlog_peak": self._push_pending_peak,
        }
        # Una métrica nunca debe impedir el cierre.
        try:
            q = self._sender.queue_stats()
            meta["sender_peak_msgs"] = int(q["peak_msgs"])
            meta["sender_peak_bytes"] = int(q["peak_bytes"])
        except Exception as e:
            logger.debug(f"[{self.client_id}] sender_peak no disponible: {type(e).__name__}")
        if self._turn_registry is not None:
            try:
                meta["turn_queue_peak"] = self._turn_registry.pop_peak(self._session_key())
            except Exception as e:
                logger.debug(f"[{self.client_id}] turn_queue_peak no disponible: {type(e).__name__}")
        return meta

    async def close_all(
        self, status: SessionStatus = "completed", reason: CloseReason | None = None
    ):
        """Tear down every microservice client and mark the session closed.

        Idempotent — the second call in a row is a no-op, and the *first
        call to finish* wins the `status`. Callable from three independent
        sites (the silence watchdog, run()'s own finally, and routes.py's
        outer finally on the setup-phase paths from issue #101) without
        knowing whether one of the others already ran.

        `_closed` is only set to True once teardown actually completes — not
        at entry — and the whole body is serialized by `_close_lock`. If a
        call is interrupted mid-teardown (e.g. cancelled during shutdown), it
        never sets `_closed`, so the next call retries the teardown instead of
        silently no-op'ing forever (issue #101 follow-up: the eager guard used
        to defeat the very safety net routes.py relies on).
        """
        # Before taking the lock, so a `send` arriving while another close_all() is
        # draining the turn is already refused (G-09).
        self._closing = True
        self.note_close_reason(reason)
        if self._turn_in_flight_at_close is None:
            self._turn_in_flight_at_close = self._turn_in_flight()
        async with self._close_lock:
            if self._closed:
                return

            self._client_registry.unregister(self.client_id, self)
            # Await (don't cancel outright) the active turn so the orchestrator
            # response is delivered before we tear down microservice clients.
            # The wait is bounded by SHUTDOWN_DRAIN_S (default 30s), not truly
            # unbounded/natural — it's a safety net, primarily relevant when a
            # turn is genuinely stuck rather than merely slow. TURN_TIMEOUT_S's
            # idle-reset semantics mean a turn making steady progress (events
            # keep arriving within TURN_TIMEOUT_S of each other) can still be
            # running when close_all() is called for an unrelated reason (e.g.
            # client disconnect) — SHUTDOWN_DRAIN_S gives it room to finish
            # naturally without holding up shutdown forever if it never does.
            # Explicit cancellation otherwise only happens via
            # _cancel_active_turn() (barge-in) or task cancellation from outside.
            if self._active_turn and not self._active_turn.done() and self._client_gone:
                # G-18: nobody is listening any more, so draining the turn would only
                # hold the teardown for up to SHUTDOWN_DRAIN_S. Cancel it (the
                # orchestrator stream's aclose sends chat.abort).
                turn = self._active_turn
                turn.cancel()
                try:
                    await asyncio.wait({turn}, timeout=settings.SHUTDOWN_DRAIN_S)
                except asyncio.CancelledError:
                    pass
            elif self._active_turn and not self._active_turn.done():
                try:
                    await asyncio.wait_for(self._active_turn, timeout=settings.SHUTDOWN_DRAIN_S)
                except asyncio.CancelledError:
                    pass
                except TimeoutError:
                    # asyncio.wait_for() already cancelled _active_turn and
                    # awaited that cancellation before raising — nothing left
                    # to do here but log. No orphaned task.
                    logger.warning(
                        f"[{self.client_id}] _active_turn no terminó dentro de "
                        f"SHUTDOWN_DRAIN_S={settings.SHUTDOWN_DRAIN_S}s — forzando cierre."
                    )
                except Exception as e:
                    logger.error(f"[{self.client_id}] _active_turn falló: {describe_error(e)}")
                    logger.debug(f"[{self.client_id}] _active_turn: {traceback_no_message(e)}")

            # Stop push intake and the worker *before* touching _push_tts / the audio
            # task it may be driving (issue #130 S3a). The worker is also in
            # self.tasks (via _spawn), but cancelling it there would happen after
            # _push_tts is closed. asyncio.wait + timeout (not a bare await) so a
            # handler that failed to honour the cancellation can't hang teardown.
            self._push_accepting = False
            worker = self._push_worker
            if (
                worker is not None
                and worker is not asyncio.current_task()
                and not worker.done()
            ):
                worker.cancel()
                try:
                    await asyncio.wait({worker}, timeout=settings.SHUTDOWN_DRAIN_S)
                except asyncio.CancelledError:
                    logger.debug(
                        f"[{self.client_id}] close_all: cancelación externa absorbida "
                        f"durante el cierre del push worker."
                    )
                else:
                    if not worker.done():
                        logger.warning(
                            f"[{self.client_id}] push worker no terminó dentro de "
                            f"SHUTDOWN_DRAIN_S={settings.SHUTDOWN_DRAIN_S}s."
                        )
            while not self._push_queue.empty():
                self._push_queue.get_nowait()
                self._push_queue.task_done()
            self._push_pending = 0
            self._push_pending_since = None
            if self._push_audio_task and not self._push_audio_task.done():
                self._push_audio_task.cancel()
                try:
                    await self._push_audio_task
                except (asyncio.CancelledError, Exception):
                    pass
            if self._push_tts:
                try:
                    await self._push_tts.close()
                except Exception:
                    pass
                self._push_tts = None

            # Defensive reset — if the session ends between an agent start and its
            # matching agent end (disconnect, orchestrator crash, reconnect mid-push),
            # the flag would otherwise stay set forever, causing the next agent start
            # received on this bridge instance to be silently dropped.
            self._push_turn_open = False
            self._push_turn_opened_at = None

            # Exclude the calling task from cancellation: _idle_watchdog and
            # _transcription_watchdog both call close_all() on themselves
            # (to end their own session). If close_all() cancelled its own
            # caller here, the self-delivered CancelledError would land at
            # the next real suspension point below (the transcriber.close()
            # gather) and abort teardown before tracker.close()/_closed=True
            # ever run. Awaiting the tasks we DO cancel guarantees nothing
            # is left dangling when this method returns.
            current_task = asyncio.current_task()
            tasks_to_cancel = [
                t for t in self.tasks if t is not current_task and not t.done()
            ]
            for task in tasks_to_cancel:
                task.cancel()
            if tasks_to_cancel:
                try:
                    await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                except asyncio.CancelledError:
                    # asyncio.gather(return_exceptions=True) still re-raises
                    # CancelledError when the *awaiting* task itself is cancelled
                    # at that moment (e.g. server or test-framework task.cancel()
                    # landing right after ws.close()). The caller cannot honor the
                    # cancellation mid-teardown — the session is already closing —
                    # so absorb it and finish teardown instead of letting it abort
                    # tracker.close()/_closed=True below.
                    logger.debug(
                        f"[{self.client_id}] close_all: cancelación externa absorbida "
                        f"durante el drenado de tareas."
                    )

            close_aws = []
            if self.transcriber:
                close_aws.append(self.transcriber.close())

            if close_aws:
                try:
                    await asyncio.gather(*close_aws, return_exceptions=True)
                except asyncio.CancelledError:
                    logger.debug(
                        f"[{self.client_id}] close_all: cancelación externa absorbida "
                        f"durante el cierre de microservicios."
                    )

            end_meta = self._session_end_meta(status)
            if not self._close_counted:
                self._close_counted = True
                gateway_stats.session_closed(end_meta["reason"])
            try:
                await self.tracker.close(status=status, **end_meta)
            except asyncio.CancelledError:
                # Same reasoning as above — the session must still be marked
                # closed so the routes.py outer-finally safety net (issue #101)
                # does not see a half-torn-down session and retry forever.
                logger.debug(
                    f"[{self.client_id}] close_all: cancelación externa absorbida "
                    f"durante el cierre del tracker."
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
            and (self._session_stt or self._stt).capabilities.partials,
            "tts": tts_live,
            "transcriber": transcriber_live,
        }

    async def _cancel_turn(self) -> tuple[bool, str | None]:
        """Cancel the turn running *now*. Returns (cancelled, its turn_id).

        G-08 (#201): the target is captured on entry, calls are serialized, and
        `_active_turn` is only cleared if it is still that same task. Two concurrent
        callers therefore cancel the turn once (the second gets False) and neither
        can wipe the reference to a turn started in between.
        """
        turn, turn_id = self._active_turn, self._active_turn_id
        if turn is None or turn.done():
            return False, None
        async with self._turn_lock:
            if turn.done():  # another caller cancelled it while we waited for the lock
                return False, None
            await self._cancel_and_wait(turn)
            return True, turn_id

    async def _cancel_active_turn(self) -> bool:
        """Cancel the active orchestrator turn if one is running. Returns True if cancelled."""
        return (await self._cancel_turn())[0]

    async def _cancel_and_wait(self, turn: asyncio.Task) -> None:
        turn.cancel()
        try:
            await turn
        except (asyncio.CancelledError, Exception):
            pass
        if self._active_turn is turn:
            self._active_turn = None

    async def _start_turn(self, text: str) -> None:
        """Replace the active turn (if any) with a new one for `text`.

        Refused once the session is closing (G-09): the input loop is still reading
        while close_all() drains, and a turn created then would survive the teardown.
        """
        if self._closing:
            logger.info(f"[{self.client_id}] send ignorado: la sesión se está cerrando.")
            return
        async with self._turn_lock:
            if self._closing:
                return
            current = self._active_turn
            if current is not None and not current.done():
                await self._cancel_and_wait(current)
            if self._closing:  # close_all() may have started during the cancel wait
                return
            task = asyncio.create_task(self._call_orchestrator(text), name="turn")
            self._supervise(task)
            self._active_turn = task
            self._active_turn_started_at = time.monotonic()

    def _turn_in_flight(self) -> bool:
        """A normal turn is running, or a push turn is open (bounded by TURN_TIMEOUT_S
        like the idle watchdog's check, so an orphaned push can't pause silence
        monitoring forever)."""
        if self._active_turn is not None and not self._active_turn.done():
            return True
        return self._push_turn_open and (
            self._push_turn_opened_at is None
            or time.monotonic() - self._push_turn_opened_at < settings.TURN_TIMEOUT_S
        )

    async def _transcription_watchdog(self):
        """Notifica al cliente si hay silencio prolongado; cierra tras max_silence_turns consecutivos."""
        # Esperar a que el cliente empiece a enviar audio antes de vigilar
        while self._last_audio_at is None:
            await asyncio.sleep(0.5)
            if not self.transcriber or self.transcriber.state == ConnectionState.DEGRADED:
                return

        silence_count = 0
        last_seen_transcription_at = self.transcriber.last_transcript_at
        was_connected = True
        recovery_baseline: float | None = None

        while True:
            await asyncio.sleep(_WATCHDOG_POLL_S)
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
            if self._turn_in_flight():
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
            if last == burst_start and self._session_stt is not None:
                # First window after the burst opened: a provider that needs a while to
                # return its first result (`warmup_s`; jota = 0) isn't blamed for it.
                timeout += self._session_stt.capabilities.warmup_s

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
        """Cierra la sesión si el cliente no manda ningún mensaje (audio o
        texto) durante IDLE_TIMEOUT_S (issue #115). Sin aviso previo al
        cliente — a diferencia del silence watchdog, que sí notifica
        degradación progresiva de la transcripción.

        Gated on activity actually in flight (issue #115 follow-up): a client
        that goes quiet while the orchestrator is mid-turn, or a push-only
        session that never sends anything by design, must not be cut off just
        because IDLE_TIMEOUT_S has elapsed since the client's *last inbound
        message*. When the idle window has expired but `_active_turn` or
        `_push_turn_open` shows something is actively in flight, this skips
        the close for this tick and re-checks shortly after — it does not
        reset the idle window, it just defers the decision until nothing is
        in flight anymore. A third signal is a non-empty push backlog
        (`_push_pending > 0`, issue #130 S3a: queued push events the worker
        has not processed yet); like `_push_turn_open` it only counts for
        TURN_TIMEOUT_S from `_push_pending_since`, so a wedged worker cannot
        gate this watchdog forever.

        `_active_turn` is safe to trust indefinitely because it's implicitly
        bounded — the orchestrator call underneath it is capped by
        TURN_TIMEOUT_S inside stream_response(). `_push_turn_open` has no
        such bound of its own (it's just a flag toggled by on_push_turn_start/
        on_push_turn_end), so an orphaned push — OpenClaw crashes or a
        reconnect happens between an agent start and its matching agent end —
        would otherwise gate this watchdog forever, leaking the session. A
        push only counts as "in flight" here while it's within its own
        TURN_TIMEOUT_S grace period since it opened (`_push_turn_opened_at`);
        past that, close_all()'s existing defensive reset handles clearing
        the stale flag.
        """
        while True:
            remaining = settings.IDLE_TIMEOUT_S - (
                time.monotonic() - self._last_client_activity
            )
            if remaining <= 0:
                push_in_flight = self._push_turn_open and (
                    self._push_turn_opened_at is None
                    or time.monotonic() - self._push_turn_opened_at < settings.TURN_TIMEOUT_S
                )
                # Issue #130 S3a: queued-but-unprocessed push events are in flight too,
                # bounded the same way as an open push turn so a wedged worker can't
                # gate this watchdog forever.
                push_backlog_in_flight = self._push_pending > 0 and (
                    self._push_pending_since is None
                    or time.monotonic() - self._push_pending_since < settings.TURN_TIMEOUT_S
                )
                if (
                    (self._active_turn and not self._active_turn.done())
                    or push_in_flight
                    or push_backlog_in_flight
                ):
                    # Not idle — a turn/push is actively in flight. Re-check
                    # shortly rather than closing or resetting the full window.
                    await asyncio.sleep(2)
                    continue
                logger.info(f"[{self.client_id}] Idle timeout — cerrando sesión.")
                self.note_close_reason("idle_timeout")
                await self.close_all()
                return
            await asyncio.sleep(remaining)

    async def _session_revalidation_loop(self) -> None:
        """Revalida la sesión contra la BD cada SESSION_REVALIDATE_INTERVAL_S (G-06, #218).

        Si el cliente fue borrado, desactivado o su clave rotada (`ClientNotFound` /
        `ClientInactive`) se cierra la sesión con 1008 «revoked». Cualquier otro error
        (BD ocupada, etc.) es transitorio: se registra solo el tipo y se reintenta en el
        siguiente tick, nunca cierra la sesión. La lectura va directa a la BD (sin caché,
        #265), así que un cambio desde /admin o la CLI se ve en el siguiente tick.
        """
        while True:
            await asyncio.sleep(settings.SESSION_REVALIDATE_INTERVAL_S)
            try:
                current, _ = await db_client.get_session(self.client.client_key)
                if current.id != self.client.id:
                    # Borrado y recreado con la MISMA clave: es otro cliente.
                    await self._revoke_session()
                    return
            except (ClientNotFound, ClientInactive):
                await self._revoke_session()
                return
            except Exception as e:
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
        # Nadie autorizado escucha ya: el turno en curso se cancela, no se drena (G-18).
        self._client_gone = True
        try:
            await asyncio.wait_for(
                self.client_ws.close(code=1008, reason="revoked"),
                timeout=settings.CLIENT_SEND_TIMEOUT_S,
            )
        except Exception as e:
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
                closer = asyncio.create_task(self.close_all(), name="idle_watchdog_close")
                self._background_tasks.add(closer)
                closer.add_done_callback(self._background_tasks.discard)

        task.add_done_callback(_on_done)

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
        try:
            await client_task
            # Terminó sola (no la canceló un watchdog ni el shutdown): el cliente se fue.
            self.note_close_reason("client_disconnect")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.note_close_reason("error")
            logger.error(f"[{self.client_id}] client_input_loop crasheó: {describe_error(e)}")
            logger.debug(f"[{self.client_id}] client_input_loop: {traceback_no_message(e)}")
        finally:
            try:
                await self.close_all()
            except asyncio.CancelledError:
                # close_all() absorbs external cancellations at its own
                # suspension points, but if one still leaks out (e.g. a future
                # teardown step without a guard), run()'s finally must not let
                # it propagate into the WebSocket endpoint — the session is
                # already ending either way.
                logger.debug(
                    f"[{self.client_id}] run(): cancelación externa absorbida "
                    f"durante el teardown."
                )

    async def _client_input_loop(self):
        """Atrapa entradas del cliente (Micrófono o Texto)."""
        try:
            while True:
                message = await self.client_ws.receive()
                self._last_client_activity = time.monotonic()

                if message.get("type") == "websocket.disconnect":
                    logger.info(f"[{self.client_id}] Cliente físico desconectado.")
                    self._client_gone = True
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
                    except Exception:
                        json_msg = None
                        parsed = False

                    if not parsed:
                        if self.handshake.input_mode == "text":
                            # Texto plano en modo texto — compatibilidad con clientes de texto puro
                            await self._start_turn(text_data)
                    elif not isinstance(json_msg, dict):
                        # G-20: a JSON scalar/list (`42`, `[1]`, `"s"`, `true`, `null`)
                        # is not a protocol message. Non-fatal error, session goes on.
                        await self._send_invalid_message()
                    elif json_msg.get("type") == "end":
                        # Cliente terminó de hablar → señalizar fin al transcriber
                        if self.transcriber:
                            await self.transcriber.send_end()

                    elif json_msg.get("type") == "cancel":
                        # Cliente cancela el turn activo sin lanzar uno nuevo
                        await self._cancel_active_turn()

                    elif json_msg.get("type") == "send":
                        # Cliente confirma/edita la transcripción y la envía al orquestador
                        raw_text = json_msg.get("text", "")
                        if not isinstance(raw_text, str):
                            await self._send_invalid_message()
                        else:
                            text = raw_text.strip()
                            if text:
                                logger.info(f"[{self.client_id}] send recibido: len={len(text)}")
                                await self._start_turn(text)

        except WebSocketDisconnect as e:
            logger.info(f"[{self.client_id}] Cliente físico desconectado.")
            self._client_gone = True
            self._note_disconnect_code(e.code)
        except Exception as e:
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
        except Exception:
            pass

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
        except Exception:
            if propagate:
                raise
            # cliente desconectado

    def _on_transcriber_state_change(self, state: ConnectionState) -> None:
        task = asyncio.create_task(self.notify_service_status("transcriber", to_wire_state(state)))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

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
            except Exception:
                return  # client disconnected
            await self.tracker.record("transcription_partial", text_len=len(text))

            # Barge-in: interrupt active turn if enabled and partial is substantial enough
            if self.config.barge_in_enabled and len(text) >= self.config.barge_in_min_chars:
                cancelled, cancelled_turn_id = await self._cancel_turn()
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
                    except Exception:
                        pass
            return  # partials never reach the orchestrator

        await self.tracker.record("transcription_final", text_len=len(text))

        # Final: cancel any running turn, notify client.
        # El orquestador se llama cuando el cliente envíe {"type": "send", "text": "..."}.
        await self._cancel_active_turn()
        logger.debug(f"[{self.client_id}] Transcripción final: '{text[:40]}'")
        try:
            await self._sender.send_json({"type": "transcription", "text": text})
        except Exception as e:
            logger.warning(
                f"[{self.client_id}] send_json(transcription) falló: {describe_error(e)}"
            )

    async def _call_orchestrator(self, text: str):
        """
        Envía texto al Orchestrator y despacha tokens/eventos al cliente y al TTS.

        Si el cliente pidió audio, crea un TTSClient por petición y corre
        pipe_tokens + pipe_audio concurrentemente (pipe_audio como tarea; todas las
        esperas del TTS acotadas, #214).
        """
        self._turn_seq += 1
        # `turn_id` keeps the full counter; `turn_seq` is the uint16 the audio header
        # carries (G-11), sent as the same value in `turn_start` so clients can match.
        turn_id = f"t-{self._turn_seq}"
        turn_seq = self._turn_seq & 0xFFFF

        self.tracker.start_turn()

        try:
            await self._sender.send_json(
                {"type": "turn_start", "turn_id": turn_id, "turn_seq": turn_seq}
            )
        except Exception:
            return
        gateway_stats.turn_started("normal")

        # From here a client has seen `turn_start`: exactly one `turn_end` must follow
        # on every path — normal, error, cancel, barge-in, override (G-10).
        self._active_turn_id = turn_id
        try:
            await self._run_turn(text, turn_id, turn_seq)
        except Exception as e:
            if not isinstance(e, ClientGone):
                # Type name only: the message may carry user text.
                logger.error(f"[{self.client_id}] Turno falló: {type(e).__name__}")
                gateway_stats.turn_error("internal_error")
                try:
                    await self._sender.send_json(
                        {
                            "type": "error",
                            "code": "TURN_ERROR",
                            "message": "internal_error",
                            "fatal": False,
                            "turn_id": turn_id,
                        }
                    )
                except Exception:
                    pass
        finally:
            if self._active_turn_id == turn_id:
                self._active_turn_id = None
            try:
                await self._sender.send_json({"type": "turn_end", "turn_id": turn_id})
            except Exception:
                pass

    async def _run_turn(self, text: str, turn_id: str, turn_seq: int) -> None:
        from src.services.orchestration import call_orchestrator

        needs_audio = "audio" in self.handshake.output_mode

        tts: TtsStream | None = None
        if needs_audio:
            tts = await self.tts.connect(
                voice=self.tts.resolve_voice(self.config.tts_voice),
                speed=self.config.tts_speed,
                client_id=self.client_id,
            )
            if tts:
                try:
                    await self.tracker.record("tts_start", voice=self.config.tts_voice or "")
                except BaseException:
                    # Cancelado (barge-in) antes de llegar al try/finally que cierra el TTS:
                    # sin esto el stream (y su hueco del semáforo) quedaría huérfano.
                    try:
                        await asyncio.wait_for(
                            tts.close(), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S
                        )
                    except Exception:
                        pass
                    raise

        session_key = self._session_key()

        # Issue #214 (G-02): every TTS wait of the turn is bounded by
        # PUSH_TTS_DRAIN_TIMEOUT_S, same mechanism as push (#199). A dead/hung TTS is
        # dropped once (one WARNING) and the turn goes on text-only.
        audio_task: asyncio.Task | None = None
        tts_live = tts

        async def _drop_tts(reason: str) -> None:
            nonlocal tts_live
            if tts_live is None:
                return
            tts_live = None
            logger.warning(
                f"[{self.client_id}] TTS del turno abandonado ({reason}); "
                f"el turno sigue solo con texto."
            )
            if audio_task is not None and not audio_task.done():
                audio_task.cancel()
                await asyncio.wait({audio_task}, timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S)

        async def _on_token(token_text: str):
            # G-18: a gone client aborts the turn (raising unwinds call_orchestrator's
            # aclosing(), whose stream finally sends chat.abort) instead of letting it
            # stream into the void and be drained for SHUTDOWN_DRAIN_S.
            if self._client_gone:
                raise ClientGone("cliente no disponible")
            try:
                if "text" in self.handshake.output_mode:
                    await self._sender.send_json(
                        {"type": "token", "turn_id": turn_id, "text": token_text}
                    )
            except ClientGone:
                self._client_gone = True
                raise
            except Exception:
                pass
            if tts_live:
                try:
                    await asyncio.wait_for(
                        tts_live.send_text_chunk(token_text),
                        timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S,
                    )
                except Exception as e:
                    await _drop_tts(f"send_text_chunk {type(e).__name__}")

        async def _on_tool_call(tool_call: ToolCallEvent):
            if self._client_gone:
                raise ClientGone("cliente no disponible")
            if not self.config.tool_calls_enabled:
                return
            try:
                await self._sender.send_json(_tool_call_message(turn_id, tool_call))
            except ClientGone:
                self._client_gone = True
                raise
            except Exception:
                pass

        async def pipe_tokens():
            try:
                await call_orchestrator(
                    self.orchestrator,
                    text,
                    session_key,
                    tracker=self.tracker,
                    on_token=_on_token,
                    on_tool_call=_on_tool_call,
                )
            except ClientGone:
                logger.info(f"[{self.client_id}] Cliente no disponible: turno abortado.")
            except RuntimeError as e:
                # Solo OrchestratorError lleva un código estable; cualquier otro texto de
                # excepción puede llevar detalle interno y no sale del proceso (#238).
                code = str(e) if isinstance(e, OrchestratorError) else "internal_error"
                logger.error(
                    f"[{self.client_id}] Orchestrator error: {code} ({type(e).__name__})"
                )
                gateway_stats.turn_error(code)
                if not isinstance(e, OrchestratorError):
                    logger.debug(f"[{self.client_id}] turno: {traceback_no_message(e)}")
                try:
                    await self._sender.send_json(
                        {
                            "type": "error",
                            "code": "TURN_ERROR",
                            "message": code,
                            "fatal": False,
                            "turn_id": turn_id,
                        }
                    )
                except Exception:
                    pass
            finally:
                if tts_live:
                    try:
                        await asyncio.wait_for(
                            tts_live.end(), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S
                        )
                    except Exception as e:
                        await _drop_tts(f"end {type(e).__name__}")

        async def pipe_audio():
            nonlocal audio_progress_at
            header = bytes([0xA1]) + turn_seq.to_bytes(2, "big")
            _first_chunk = True
            async for chunk in tts.get_audio_stream():
                audio_progress_at = time.monotonic()
                if _first_chunk:
                    await self.tracker.record("tts_first_chunk")
                    _first_chunk = False
                try:
                    await self._sender.send_bytes(header + chunk)
                except Exception as e:
                    if isinstance(e, ClientGone):
                        self._client_gone = True
                    return
            await self.tracker.record("tts_done")

        if tts:
            audio_task = asyncio.create_task(pipe_audio())
            try:
                await pipe_tokens()
                if self._client_gone:
                    audio_task.cancel()  # G-18: nobody to play it to
                # Cap by INACTIVITY, not absolute: the window restarts with every audio
                # chunk, so a long healthy reply is never truncated. asyncio.wait (not
                # wait_for): an external cancellation (barge-in) propagates instead of
                # being mistaken for the audio task's own.
                audio_progress_at = time.monotonic()
                while not audio_task.done():
                    idle_left = settings.PUSH_TTS_DRAIN_TIMEOUT_S - (
                        time.monotonic() - audio_progress_at
                    )
                    if idle_left <= 0:
                        await _drop_tts("el audio no progresa (sin done)")
                        break
                    await asyncio.wait({audio_task}, timeout=idle_left)
            finally:
                if not audio_task.done():
                    audio_task.cancel()
                    await asyncio.wait({audio_task}, timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S)
                if audio_task.done() and not audio_task.cancelled() and audio_task.exception():
                    logger.warning(
                        f"[{self.client_id}] pipe_audio falló ({type(audio_task.exception()).__name__})"
                    )
                try:
                    await asyncio.wait_for(tts.close(), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S)
                except Exception as e:
                    logger.warning(
                        f"[{self.client_id}] TTS close del turno falló ({type(e).__name__})"
                    )
        else:
            await pipe_tokens()

    def _push_allowed(self) -> bool:
        """Single decision point for whether an unsolicited (orchestrator-pushed)
        frame may reach the client (issue #109): the client must have push
        enabled AND a push turn must currently be open. Used by deliver_push
        and deliver_push_tool_call so the check isn't duplicated/inconsistent
        across the start vs. payload code paths — on_push_turn_start is the
        only place that sets _push_turn_open True, and it already gates on
        push_enabled before doing so.
        """
        return self.config.push_enabled and self._push_turn_open

    async def on_push_turn_start(self, session_key: str) -> None:
        if not self.config.push_enabled:
            # Intentionally do NOT set _push_turn_open here — push_enabled=False
            # means the whole push reply is dropped. The matching agent end will
            # then hit the orphan-end branch in on_push_turn_end and be ignored too.
            return
        # Issue #84: if a push turn is already open, intermediate agent starts
        # (tool-use, multi-step reasoning) must not open a new logical turn.
        # The turn is already open; tokens/audio continue arriving via deliver_push.
        if self._push_turn_open:
            logger.debug(
                f"[{self.client_id}] push_turn_start ignored: a push turn is already open "
                f"({self._push_turn_id})"
            )
            return

        self._turn_seq += 1
        turn_seq = self._turn_seq & 0xFFFF  # uint16 on the wire (G-11)
        self._push_turn_id = f"t-{self._turn_seq}"
        self._push_turn_open = True
        self._push_text.reset()
        self._push_turn_opened_at = time.monotonic()
        self._push_tts_stats = None
        gateway_stats.turn_started("push")

        try:
            await self._sender.send_json(
                {
                    "type": "turn_start",
                    "turn_id": self._push_turn_id,
                    "turn_seq": turn_seq,
                }
            )
        except Exception:
            pass

        if "audio" not in self.handshake.output_mode:
            return
        tts = await self.tts.connect(
            voice=self.tts.resolve_voice(self.config.tts_voice),
            speed=self.config.tts_speed,
            client_id=self.client_id,
        )
        if tts is None:
            return
        self._push_tts = tts
        self._push_tts_stats = {
            "send_ms": 0.0, "end_ms": 0.0, "drain_ms": 0.0, "close_ms": 0.0,
            "dropped": False, "chars": 0,
        }  # fmt: skip

        async def _pipe_push_audio():
            header = bytes([0xA1]) + turn_seq.to_bytes(2, "big")
            async for chunk in tts.get_audio_stream():
                try:
                    await self._sender.send_bytes(header + chunk)
                except Exception:
                    return

        self._push_audio_task = asyncio.create_task(_pipe_push_audio())

    async def deliver_push(self, payload: dict) -> None:
        if not self._push_allowed():
            return
        # #241: `final` carries the full text without deltaText (OpenClaw 2026.10.5).
        delta = self._push_text.feed(payload)
        if not delta:
            return
        if "text" in self.handshake.output_mode:
            try:
                await self._sender.send_json(
                    {
                        "type": "token",
                        "turn_id": self._push_turn_id,
                        "text": delta,
                    }
                )
            except Exception:
                pass
        tts = self._push_tts
        if tts:
            t0 = time.monotonic()
            try:
                if self._push_tts_stats is not None:
                    self._push_tts_stats["chars"] += len(delta)
                await asyncio.wait_for(
                    tts.send_text_chunk(delta), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S
                )
            except Exception as e:
                # Issue #199: a hung/failed push TTS must not wedge the worker nor
                # log one warning per token. Drop it; the turn continues text-only.
                await self._drop_push_tts(f"send_text_chunk {type(e).__name__}")
            finally:
                self._add_push_ms("send_ms", t0)

    def _add_push_ms(self, key: str, t0: float) -> None:
        if self._push_tts_stats is not None:
            self._push_tts_stats[key] += (time.monotonic() - t0) * 1000

    async def _drop_push_tts(self, reason: str) -> None:
        """Abandon this turn's push TTS (issue #199). Every wait is bounded by
        PUSH_TTS_DRAIN_TIMEOUT_S; logs once per turn (a later call is a no-op)."""
        tts = self._push_tts
        if tts is None:
            return
        self._push_tts = None
        if self._push_tts_stats is not None:
            self._push_tts_stats["dropped"] = True
        logger.warning(
            f"[{self.client_id}] TTS de push abandonado ({reason}); "
            f"el turno sigue solo con texto."
        )
        audio_task, self._push_audio_task = self._push_audio_task, None
        if audio_task is not None and not audio_task.done():
            audio_task.cancel()
            await asyncio.wait({audio_task}, timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S)
        try:
            await asyncio.wait_for(tts.close(), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S)
        except Exception:
            pass

    async def deliver_push_tool_call(self, data: dict) -> None:
        if not self._push_allowed():
            return
        if not self.config.tool_calls_enabled:
            return
        tool_call = ToolCallEvent.from_session_tool_payload(data)
        if tool_call is None:
            return
        try:
            await self._sender.send_json(_tool_call_message(self._push_turn_id, tool_call))
        except Exception:
            pass

    async def on_push_turn_end(self, session_key: str) -> None:
        # Issue #84: if no push turn is open, this agent end has no matching
        # turn_start on the client side. Ignoring it avoids orphan turn_end frames.
        if not self._push_turn_open:
            logger.debug(f"[{self.client_id}] push_turn_end ignored: no push turn open")
            return

        tts = self._push_tts
        if tts:
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(tts.end(), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S)
            except Exception as e:
                # Issue #199: end() hung or failed — no audio to wait for.
                await self._drop_push_tts(f"end {type(e).__name__}")
            finally:
                self._add_push_ms("end_ms", t0)
        t0 = time.monotonic()
        audio_task = self._push_audio_task
        if self._push_tts is not None and audio_task is not None:
            if not audio_task.done():
                # asyncio.wait (not wait_for) on purpose: it never re-raises the
                # task's own outcome, so an *external* cancellation of this
                # coroutine (close_all() cancelling the push worker, #130 S3a)
                # propagates instead of being mistaken for the audio task's.
                done, _ = await asyncio.wait(
                    {audio_task}, timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S
                )
                if not done:
                    logger.warning(
                        f"[{self.client_id}] push audio no terminó dentro de "
                        f"PUSH_TTS_DRAIN_TIMEOUT_S={settings.PUSH_TTS_DRAIN_TIMEOUT_S}s "
                        f"— cancelando."
                    )
                    audio_task.cancel()
                    # Issue #204: bounded, an audio ignoring cancellation can't hang us.
                    await asyncio.wait({audio_task}, timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S)
            if audio_task.done():
                # Retrieves the outcome so nothing is reported as "never retrieved".
                await asyncio.gather(audio_task, return_exceptions=True)
        self._push_audio_task = None
        self._add_push_ms("drain_ms", t0)
        if self._push_tts is not None:
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(
                    self._push_tts.close(), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S
                )
            except Exception:
                pass
            self._push_tts = None
            self._add_push_ms("close_ms", t0)

        stats, self._push_tts_stats = self._push_tts_stats, None
        if stats is not None:
            await self.tracker.record(
                "push_tts",
                **{k: round(v, 1) if isinstance(v, float) else v for k, v in stats.items()},
            )
        try:
            await self._sender.send_json({"type": "turn_end", "turn_id": self._push_turn_id})
        except Exception:
            pass
        self._push_turn_open = False
        self._push_turn_opened_at = None

    def enqueue_push(self, kind: str, arg: Any) -> None:
        """Queue a push event for this bridge's worker (issue #130 S3a).

        Synchronous and never blocks: it is called from OpenClaw's _listen loop via
        FrameDispatcher. `kind` is "turn_start" | "chat" | "tool" | "turn_end" and
        `arg` is what the matching handler takes (session_key for turn_*, the frame
        payload for chat/tool). No-op once the bridge is closing.
        """
        if not self._push_accepting:
            return
        if self._push_worker is None or self._push_worker.done():
            self._push_worker = self._spawn(self._push_worker_loop(), "push_worker")
        if self._push_pending == 0:
            self._push_pending_since = time.monotonic()
        self._push_pending += 1
        self._push_pending_peak = max(self._push_pending_peak, self._push_pending)
        self._push_queue.put_nowait((kind, arg))

    async def push_idle(self) -> None:
        """Wait until the worker has finished every queued push event (for tests)."""
        await self._push_queue.join()

    async def _push_worker_loop(self) -> None:
        while self._push_accepting:
            kind, arg = await self._push_queue.get()
            try:
                if kind == "turn_start":
                    await self.on_push_turn_start(arg)
                elif kind == "chat":
                    await self.deliver_push(arg)
                elif kind == "tool":
                    await self.deliver_push_tool_call(arg)
                elif kind == "turn_end":
                    await self.on_push_turn_end(arg)
                else:
                    logger.warning(f"[{self.client_id}] push event desconocido: {kind!r}")
            except Exception as e:
                # Type name only: the payload may carry user text.
                logger.warning(f"[{self.client_id}] push {kind} falló: {type(e).__name__}")
            finally:
                self._push_pending = max(0, self._push_pending - 1)
                if self._push_pending == 0:
                    self._push_pending_since = None
                self._push_queue.task_done()
