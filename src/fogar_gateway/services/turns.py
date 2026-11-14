"""Turnos de cara al cliente: `TurnController` y el TTS de cada turno (M19; #271, #285, #286).

Un turno es lo que el cliente ve entre un `turn_start` y su `turn_end`. Hay dos orígenes:
`user` (un `send` del cliente) y `push` (OpenClaw abre una ejecución por su cuenta). Este
módulo es el único que los abre y los cierra, con las mismas reglas para los dos:

- Un solo turno abierto por sesión (#202): un push que llega con un turno del usuario en
  curso espera; un `send`, un `cancel` o un barge-in cortan el push abierto y lo abortan en
  OpenClaw si es de la misma sesión de agente.
- Exactamente un `turn_end` por `turn_start`, sea cual sea el camino (`_end`, idempotente).
- Un mismo presupuesto de tiempo: `TURN_TIMEOUT_S` sin progreso del orquestador cierra el
  turno con `turn_timeout` (el normal, dentro de `stream_response`; el push, en el worker).
- Un TTS por turno (`TurnTts`): abrir, alimentar, terminar esperando el audio por
  inactividad, y liberar; las métricas (`tts_start`, `tts_first_chunk`, `tts_done`) son las
  mismas para los dos orígenes.

Cada turno lleva su conversación y su origen en `turn_start`, y el motivo de su fin
(`EndReason`) en `turn_end` (M19). La conversación de un turno del usuario es la de la sesión,
evaluada al empezarlo (cambio de día, services/conversations.py); la de un push, la de su
`sessionKey`.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from fogar_gateway.core.config import settings
from fogar_gateway.models.schemas import ClientConfig, Handshake
from fogar_gateway.services import orchestration
from fogar_gateway.services.conversations import SessionConversation
from fogar_gateway.services.openclaw.chat_text import ChatTextTracker
from fogar_gateway.services.openclaw.models import ToolCallEvent
from fogar_gateway.services.openclaw.registry import TurnRegistry
from fogar_gateway.services.outbound import ClientGone, OutboundSender, log_send_failed
from fogar_gateway.services.pacing import AudioPacer
from fogar_gateway.services.pipeline_tracker import PipelineTracker
from fogar_gateway.services.protocol import OrchestratorProtocol
from fogar_gateway.services.reconnection import describe_error, traceback_no_message
from fogar_gateway.services.speech.ports import TextToSpeechProvider, TtsStream
from fogar_gateway.services.stats import gateway_stats
from fogar_gateway.services.teardown import Teardown

logger = logging.getLogger(__name__)

TurnOrigin = Literal["user", "push"]
# done: terminó solo · error: falló (TURN_ERROR o fallo del agente) · cancelled: `cancel` del
# cliente · interrupted: el usuario habló encima (barge-in o transcripción final) · replaced:
# un `send` nuevo · timeout: TURN_TIMEOUT_S sin progreso · closed: la sesión se cierra o el
# cliente se fue.
EndReason = Literal["done", "error", "cancelled", "interrupted", "replaced", "timeout", "closed"]


def _tool_call_message(turn_id: str, tool_call: ToolCallEvent) -> dict:
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


@dataclass(eq=False)
class Turn:
    """Un turno de cara al cliente. Solo IDs, tiempos y estados: nunca texto."""

    turn_id: str  # `t-<contador completo>`
    seq: int  # el contador & 0xFFFF: el uint16 de `turn_start` y del header de audio (G-11)
    origin: TurnOrigin
    started_at: float
    progress_at: float  # último evento del orquestador (presupuesto del push, #286)
    session_key: str | None = None  # push: sesión de OpenClaw en la que corre
    conversation_id: str | None = None  # su conversación (None: push de una clave desconocida)
    run_id: str | None = None  # push: el runId de OpenClaw de su `agent start`
    task: asyncio.Task | None = None  # user: la tarea que lo ejecuta
    opened: bool = False  # su turn_start ya está en la cola del cliente
    ended: bool = False  # su turn_end ya está en la cola (o se abandonó al cerrar)
    end_reason: EndReason | None = None
    cancel_reason: EndReason | None = None  # quien cancela su tarea dice por qué
    finishing: bool = False  # push: llegó el fin del agente, solo queda su audio
    tts: "TurnTts | None" = None


@dataclass
class _TtsStats:
    """Tiempos (ms) del TTS de un turno; solo números (el evento `push_tts`)."""

    send_ms: float = 0.0
    end_ms: float = 0.0
    drain_ms: float = 0.0
    close_ms: float = 0.0
    dropped: bool = False
    chars: int = 0

    def add(self, key: str, t0: float) -> None:
        setattr(self, key, getattr(self, key) + (time.monotonic() - t0) * 1000)

    def as_meta(self) -> dict[str, Any]:
        return {
            k: round(v, 1) if isinstance(v, float) else v for k, v in self.__dict__.items()
        }


class TurnTts:
    """El TTS de un turno (#271): `start`, `feed`, `end` + `drain`, `release`.

    Toda espera está acotada por PUSH_TTS_DRAIN_TIMEOUT_S (#199, #214); la del audio, por
    inactividad (cada trozo reinicia la ventana), así que una respuesta larga que suena a su
    ritmo nunca se corta. Un TTS caído se abandona una vez (un WARNING) y el turno sigue solo
    con texto. Su audio sale con el `turn_seq` del turno y nunca después de su `turn_end`.
    """

    def __init__(
        self,
        stream: TtsStream,
        turn: Turn,
        *,
        sender: OutboundSender,
        tracker: PipelineTracker,
        client_id: str,
        on_client_gone: Callable[[], None],
        supervise: Callable[[asyncio.Task], None],
    ) -> None:
        self._stream = stream
        self._turn = turn
        self._sender = sender
        self._tracker = tracker
        self._client_id = client_id
        self._on_client_gone = on_client_gone
        self._supervise = supervise
        self._audio: asyncio.Task | None = None
        self._progress_at: float | None = None
        self._started = False
        self._shut = False
        self._released = False
        self.dropped = False
        self.stats = _TtsStats()

    @property
    def live(self) -> bool:
        return not self.dropped and not self._released

    def _meta(self) -> dict[str, str]:
        # Las métricas del push llevan su origen: turn_e2e_ms no las mezcla con las del turno
        # del usuario (las del usuario quedan igual que antes).
        return {"origin": "push"} if self._turn.origin == "push" else {}

    def progressing(self, now: float) -> bool:
        """Su audio sigue saliendo (avanzó en la ventana de PUSH_TTS_DRAIN_TIMEOUT_S)."""
        audio = self._audio
        return (
            audio is not None
            and not audio.done()
            and self._progress_at is not None
            and now - self._progress_at < settings.PUSH_TTS_DRAIN_TIMEOUT_S
        )

    async def start(self, voice: str) -> None:
        await self._tracker.record("tts_start", voice=voice, **self._meta())
        if not self.live:  # liberado mientras se registraba (corte o cancelación)
            return
        self._started = True
        self._progress_at = time.monotonic()
        name = "push_audio" if self._turn.origin == "push" else "turn_audio"
        self._audio = asyncio.create_task(self._pipe(), name=name)
        # Supervisada: se puede abandonar sin recoger su resultado (#221).
        self._supervise(self._audio)

    async def _pipe(self) -> None:
        header = bytes([0xA1]) + self._turn.seq.to_bytes(2, "big")
        pacer = AudioPacer(settings.AUDIO_LEAD_S, settings.AUDIO_PACING_ENABLED)
        first = True
        async for chunk in self._stream.get_audio_stream():
            self._progress_at = time.monotonic()
            if not chunk:
                continue
            if first:
                first = False
                await self._tracker.record("tts_first_chunk", **self._meta())
            async for frame in pacer.frames(chunk):
                if self._turn.ended:
                    return  # nada después de su turn_end
                try:
                    await self._sender.send_bytes(header + frame)
                except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
                    if isinstance(e, ClientGone):
                        self._on_client_gone()
                    logger.debug(
                        f"[{self._client_id}] envío de audio al cliente falló: {describe_error(e)}"
                    )
                    return
                self._progress_at = time.monotonic()  # cada frame con ritmo es progreso
        await self._tracker.record("tts_done", **self._meta())

    async def feed(self, text: str) -> None:
        if not self.live:
            return
        self.stats.chars += len(text)
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(
                self._stream.send_text_chunk(text), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S
            )
        except Exception as e:  # noqa: BLE001 — frontera: el TTS caído no rompe el turno
            await self.drop(f"send_text_chunk {type(e).__name__}")
        finally:
            self.stats.add("send_ms", t0)

    async def end(self) -> None:
        """Avisa al TTS de que no hay más texto."""
        if not self.live:
            return
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(self._stream.end(), timeout=settings.PUSH_TTS_DRAIN_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — frontera: el TTS caído no rompe el turno
            await self.drop(f"end {type(e).__name__}")
        finally:
            self.stats.add("end_ms", t0)

    async def drain(self) -> None:
        """Espera a que salga todo su audio, acotada por INACTIVIDAD.

        La ventana empieza al empezar a esperar (7e: tras una pausa larga del agente el sello
        de progreso es viejo). asyncio.wait, no wait_for: una cancelación ajena (barge-in,
        close_all) se propaga en vez de confundirse con la del audio.
        """
        audio = self._audio
        if not self.live or audio is None:
            return
        t0 = time.monotonic()
        self._progress_at = t0
        try:
            while not audio.done():
                idle_left = settings.PUSH_TTS_DRAIN_TIMEOUT_S - (
                    time.monotonic() - (self._progress_at or t0)
                )
                if idle_left <= 0:
                    await self.drop("el audio no progresa")
                    break
                await asyncio.wait({audio}, timeout=idle_left)
        finally:
            self.stats.add("drain_ms", t0)

    async def finish(self) -> None:
        await self.end()
        await self.drain()

    async def drop(self, reason: str, level: int = logging.WARNING) -> None:
        """Abandona el TTS (una vez por turno: las siguientes llamadas no hacen nada)."""
        if not self.live:
            return
        self.dropped = True
        self.stats.dropped = True
        rest = "; el turno sigue solo con texto." if level >= logging.WARNING else "."
        logger.log(
            level,
            f"[{self._client_id}] TTS del turno {self._turn.turn_id} abandonado ({reason}){rest}",
        )
        await self._shutdown()

    async def release(self) -> None:
        """Para el audio y cierra el stream; el push registra su evento `push_tts`."""
        if self._released:
            return
        self._released = True
        await self._shutdown()
        if self._started and self._turn.origin == "push":
            await self._tracker.record("push_tts", **self.stats.as_meta())

    async def _shutdown(self) -> None:
        """Para el audio y cierra el stream. Los dos pasos terminan aunque cancelen a quien
        libera (#221): una segunda cancelación (un barge-in y después close_all) no deja el
        stream abierto; esa cancelación se relanza al final. Una heredada no (como en el
        barge-in, `raise_if_cancelled`). Cada paso, acotado por PUSH_TTS_DRAIN_TIMEOUT_S
        (#204: un audio que ignora la cancelación no cuelga a nadie)."""
        if self._shut:
            return
        self._shut = True
        limit = settings.PUSH_TTS_DRAIN_TIMEOUT_S
        td = Teardown(f"[{self._client_id}] TTS del turno {self._turn.turn_id}")
        await td.cancel(self._audio, "audio del turno", limit)
        t0 = time.monotonic()
        await td.step(self._stream.close(), "cierre del TTS", limit)
        self.stats.add("close_ms", t0)
        td.raise_if_cancelled()


@dataclass
class _PushBacklog:
    """Eventos de push encolados y aún sin procesar (#130 S3a)."""

    pending: int = 0
    peak: int = 0
    since: float | None = None
    queue: asyncio.Queue[tuple[str, Any, str | None]] = field(default_factory=asyncio.Queue)


class TurnController:
    """Abre, alimenta y cierra los turnos de una sesión. Ver el docstring del módulo.

    Lo que es de la sesión (socket de entrada, STT, watchdogs, revalidación, cierre) queda en
    `FogarBridge`, que llama a `start_user_turn`, `cancel`, `enqueue_push`, `in_flight` y
    `close`.
    """

    def __init__(
        self,
        *,
        client_id: str,
        config: ClientConfig,
        handshake: Handshake,
        orchestrator: OrchestratorProtocol,
        tts: TextToSpeechProvider,
        tracker: PipelineTracker,
        sender: OutboundSender,
        conversation: SessionConversation,
        spawn: Callable[[Coroutine[Any, Any, None], str], asyncio.Task],
        supervise: Callable[[asyncio.Task], None],
        turn_registry: TurnRegistry,
    ) -> None:
        self._client_id = client_id
        self._config = config
        self._wants_text = "text" in handshake.output_mode
        self._wants_audio = "audio" in handshake.output_mode
        self._orchestrator = orchestrator
        self._tts = tts
        self._tracker = tracker
        self._sender = sender
        self._conversation = conversation
        self._spawn = spawn
        self._supervise = supervise
        # Runs de OpenClaw (#285): lápidas (el dispatcher descarta los frames de un run
        # abortado que aún no llegaron, y el worker de push lo que ya tenía en cola) y runs
        # activos en nuestra session_key (se abortan antes de un chat.send).
        self._turn_registry = turn_registry

        self._seq = 0
        # Serializa cancelar/arrancar (G-08, #201): un turno nuevo no arranca hasta que el
        # anterior terminó, y el abort de un push cortado sale antes del chat.send del usuario.
        self._lock = asyncio.Lock()
        self._user_task: asyncio.Task | None = None
        self._user_started_at: float | None = None
        self._user_turn: Turn | None = None
        self._push: Turn | None = None
        self._push_text = ChatTextTracker()  # #241: texto ya emitido en la respuesta del push
        # Worker de push (#130 S3a): el dispatcher encola sin esperar desde el bucle de
        # OpenClaw; un solo worker por sesión conserva el orden start → deltas → tool → end.
        self._push_worker: asyncio.Task | None = None
        self._backlog = _PushBacklog()
        # Una vez puesto no vuelve atrás: ni turnos nuevos (G-09) ni eventos de push.
        self._closing = False
        # El cliente se fue (desconexión o ClientGone): su turno deja de enviar (G-18).
        self.client_gone = False
        self.last_ended: Turn | None = None

    # ── Estado ────────────────────────────────────────────────────────────

    def _session_key(self) -> str:
        """La sesión de OpenClaw de la conversación actual de la sesión."""
        return self._conversation.session_key

    def _may_abort(self, session_key: str) -> bool:
        """Se aborta en OpenClaw solo en la clave propia, y no una vez perdida la conversación
        (M19: otra sesión la abrió y puede tener ya su turno en ella)."""
        return not self._conversation.released and session_key == self._session_key()

    def in_flight(self) -> bool:
        """Hay un turno en curso: el único cálculo para los dos watchdogs.

        El turno del usuario está acotado por dentro (TURN_TIMEOUT_S en stream_response). El
        push y la cola de push, por TURN_TIMEOUT_S sin progreso, así que un worker atascado no
        para el idle watchdog para siempre; un push cuyo audio sigue sonando (con ritmo, puede
        durar más) cuenta mientras ese audio avanza.
        """
        now = time.monotonic()
        task = self._user_task
        if task is not None and not task.done():
            return True
        push = self._push
        if push is not None:
            if now - push.progress_at < settings.TURN_TIMEOUT_S:
                return True
            if push.tts is not None and push.tts.progressing(now):
                return True
        backlog = self._backlog
        return backlog.pending > 0 and (
            backlog.since is None or now - backlog.since < settings.TURN_TIMEOUT_S
        )

    def snapshot(self, now: float) -> dict[str, Any]:
        """El turno en curso y la cola de push para /admin/state (#219): solo números."""
        kind, since = "idle", None
        task, push = self._user_task, self._push
        if task is not None and not task.done():
            kind, since = "normal", self._user_started_at
        elif push is not None:
            kind, since = "push", push.started_at
        return {
            "turn": {"kind": kind, "since_s": round(now - since, 3) if since else None},
            "push_backlog": {"pending": self._backlog.pending, "peak": self._backlog.peak},
        }

    @property
    def push_backlog_peak(self) -> int:
        return self._backlog.peak

    @property
    def user_task(self) -> asyncio.Task | None:
        """The task running the user's turn, if one was started (done or not)."""
        return self._user_task

    # ── Turno del usuario ─────────────────────────────────────────────────

    async def start_user_turn(self, text: str) -> None:
        """Sustituye el turno en curso (normal o push) por uno nuevo para `text`.

        Rechazado una vez que la sesión se cierra (G-09): el bucle de entrada sigue leyendo
        mientras corre close_all(), y un turno creado entonces sobreviviría al cierre.
        """
        if self._closing:
            logger.info(f"[{self._client_id}] send ignorado: la sesión se está cerrando.")
            return
        async with self._lock:
            if self._closing:
                return
            current = self._user_task
            if current is not None and not current.done():
                self._mark_cancel(current, "replaced")
                await self._cancel_and_wait(current)
            # Dentro del lock: el abort del push termina antes de que salga el chat.send.
            await self._cut_push(None, "replaced")
            # Después de cortar (el push de la conversación de ayer se aborta en su clave) y
            # antes de mirar los runs activos: el cambio de día se evalúa al empezar cada turno.
            await self._conversation.refresh()
            await self._abort_other_runs()
            if self._closing:  # close_all() pudo empezar durante la espera
                return
            task = asyncio.create_task(self._run_user_turn(text), name="turn")
            self._supervise(task)
            self._user_task = task
            self._user_started_at = time.monotonic()

    async def cancel(self, reason: EndReason, *, push: bool = True) -> tuple[bool, str | None]:
        """Cancela el turno en curso; con `push`, también el push abierto *ahora* (uno que el
        worker abra después sigue). Devuelve (cancelado, su turn_id si llegó a abrirse)."""
        push_id = self._push.turn_id if push and self._push is not None else None
        cancelled, turn_id = await self._cancel_user(reason)
        if push_id is not None and not cancelled:
            async with self._lock:
                if await self._cut_push(push_id, reason):
                    cancelled, turn_id = True, push_id
        return cancelled, turn_id

    def _mark_cancel(self, task: asyncio.Task, reason: EndReason) -> None:
        turn = self._user_turn
        if turn is not None and turn.task is task and turn.cancel_reason is None:
            turn.cancel_reason = reason

    async def _cancel_user(self, reason: EndReason) -> tuple[bool, str | None]:
        """G-08 (#201): el objetivo se captura al entrar y las llamadas se serializan; dos
        llamadas a la vez cancelan el turno una vez y ninguna pierde uno arrancado entre medias."""
        task = self._user_task
        if task is None or task.done():
            return False, None
        async with self._lock:
            if task.done():  # otro lo canceló mientras esperábamos el lock
                return False, None
            self._mark_cancel(task, reason)
            await self._cancel_and_wait(task)
            turn = self._user_turn
            opened = turn is not None and turn.task is task and turn.opened
            return True, turn.turn_id if opened and turn is not None else None

    async def _abort_other_runs(self) -> None:
        """Ningún otro run activo en nuestra session_key cuando sale el chat.send del
        usuario: OpenClaw lo plegaría en ese run (docs/openclaw-protocol.md, #219) y la
        respuesta llegaría con otro runId. Pasa con un push que no llegó a abrirse (aún en la
        cola del worker, `push_enabled=False`, o su turn_start falló). Lápida y chat.abort,
        bajo el lock de quien llama."""
        session_key = self._session_key()
        runs = self._turn_registry.active_runs(session_key)
        if not runs or not self._may_abort(session_key):
            return
        for run_id in runs:
            self._turn_registry.abort_run(run_id)
        logger.info(
            f"[{self._client_id}] {len(runs)} run(s) de OpenClaw activos en la sesión: "
            f"chat.abort antes del send"
        )
        await self._orchestrator.abort(session_key)

    async def _cancel_and_wait(self, task: asyncio.Task) -> None:
        # Sin tope: el turno nuevo no arranca hasta que el viejo terminó. Si cancelan a quien
        # llama, se termina de esperar y se relanza (#221); solo esa cancelación: una heredada
        # (cancelling() > 0) no debe tumbar el STT en un barge-in.
        td = Teardown(f"[{self._client_id}] cancelar turno")
        await td.cancel(task, "turno anterior")
        if self._user_task is task:
            self._user_task = None
        td.raise_if_cancelled()

    async def _run_user_turn(self, text: str) -> None:
        turn = self._new_turn("user")
        turn.conversation_id = self._conversation.id
        turn.task = asyncio.current_task()
        self._user_turn = turn
        self._tracker.start_turn()
        if not await self._open(turn):
            return
        gateway_stats.turn_started("normal")
        # Desde aquí el cliente vio turn_start: le sigue exactamente un turn_end (G-10).
        reason: EndReason = "error"
        try:
            reason = await self._converse(turn, text)
        except asyncio.CancelledError:
            reason = turn.cancel_reason or "cancelled"
            raise
        except Exception as e:  # noqa: BLE001 — frontera: un turno roto no cierra la sesión
            if isinstance(e, ClientGone):
                reason = "closed"
            else:
                # Solo el tipo: el mensaje puede llevar texto del usuario.
                logger.error(f"[{self._client_id}] Turno falló: {type(e).__name__}")
                gateway_stats.turn_error("internal_error")
                await self._send_turn_error(turn, "internal_error")
        finally:
            await self._end(turn, reason)

    async def _converse(self, turn: Turn, text: str) -> EndReason:
        """El texto va al orquestador; sus tokens y eventos, al cliente y al TTS del turno."""
        tts = await self._connect_tts(turn) if self._wants_audio else None
        # Sin await entre el connect y el try: una cancelación no deja el stream huérfano.
        try:
            if tts is not None:
                await tts.start(self._config.tts_voice or "")
            try:
                reason = await self._stream(turn, text, tts)
            finally:
                if tts is not None:
                    await tts.end()  # en todo camino: el servidor TTS sabe que no hay más
            if tts is not None and not self.client_gone:  # G-18: nadie a quien sonárselo
                await tts.drain()
            return reason
        finally:
            if tts is not None:
                await tts.release()

    async def _stream(self, turn: Turn, text: str, tts: "TurnTts | None") -> EndReason:
        async def on_token(token_text: str) -> None:
            # G-18: un cliente ido aborta el turno (al deshacer aclosing() el stream manda
            # chat.abort) en vez de seguir hasta que close_all() lo cancele.
            if self.client_gone:
                raise ClientGone("cliente no disponible")
            if self._wants_text:
                try:
                    await self._sender.send_json(
                        {"type": "token", "turn_id": turn.turn_id, "text": token_text}
                    )
                except ClientGone:
                    self.client_gone = True
                    raise
                except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
                    self._log_send_failed("token", e)
            if tts is not None:
                await tts.feed(token_text)

        async def on_tool_call(tool_call: ToolCallEvent) -> None:
            if self.client_gone:
                raise ClientGone("cliente no disponible")
            if not self._config.tool_calls_enabled:
                return
            try:
                await self._sender.send_json(_tool_call_message(turn.turn_id, tool_call))
            except ClientGone:
                self.client_gone = True
                raise
            except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
                self._log_send_failed("tool_call", e)

        try:
            await orchestration.call_orchestrator(
                self._orchestrator,
                text,
                self._session_key(),
                tracker=self._tracker,
                on_token=on_token,
                on_tool_call=on_tool_call,
            )
            return "done"
        except ClientGone:
            logger.info(f"[{self._client_id}] Cliente no disponible: turno abortado.")
            return "closed"
        except RuntimeError as e:
            # Solo OrchestratorError lleva un código estable; cualquier otro texto de
            # excepción puede llevar detalle interno y no sale del proceso (#238).
            stable = isinstance(e, orchestration.OrchestratorError)
            code = str(e) if stable else "internal_error"
            logger.error(f"[{self._client_id}] Orchestrator error: {code} ({type(e).__name__})")
            gateway_stats.turn_error(code)
            if not stable:
                logger.debug(f"[{self._client_id}] turno: {traceback_no_message(e)}")
            await self._send_turn_error(turn, code)
            return "timeout" if code == "turn_timeout" else "error"

    # ── Push ──────────────────────────────────────────────────────────────

    def enqueue_push(self, kind: str, arg: Any, run_id: str | None = None) -> None:
        """Encola un evento de push para el worker (#130 S3a). Síncrono, nunca bloquea: lo
        llama el bucle de OpenClaw vía FrameDispatcher. `kind`: "turn_start" | "chat" |
        "tool" | "turn_end" | "turn_error"; `arg`: la session_key (turn_*) o el payload
        (chat/tool); `run_id`: el runId del evento, si lo trae. No hace nada una vez que la
        sesión se cierra."""
        if self._closing:
            return
        if self._push_worker is None or self._push_worker.done():
            self._push_worker = self._spawn(self._push_worker_loop(), "push_worker")
        backlog = self._backlog
        if backlog.pending == 0:
            backlog.since = time.monotonic()
        backlog.pending += 1
        backlog.peak = max(backlog.peak, backlog.pending)
        backlog.queue.put_nowait((kind, arg, run_id))

    async def push_idle(self) -> None:
        """Espera a que el worker termine todo lo encolado (para tests)."""
        await self._backlog.queue.join()

    def _push_budget_left(self) -> float | None:
        """Segundos que le quedan al push abierto sin progreso (#286); None si no aplica."""
        push = self._push
        if push is None or push.finishing or push.ended:
            return None
        return push.progress_at + settings.TURN_TIMEOUT_S - time.monotonic()

    async def _push_worker_loop(self) -> None:
        queue = self._backlog.queue
        while not self._closing:
            budget = self._push_budget_left()
            # Con eventos en cola el push no está parado: solo se espera con tope si no hay.
            if budget is not None and queue.empty():
                try:
                    kind, arg, run_id = await asyncio.wait_for(queue.get(), timeout=budget)
                except TimeoutError:
                    await self._run_push_step("timeout", self._push_timed_out())
                    continue
            else:
                kind, arg, run_id = await queue.get()
            try:
                if self._run_aborted(run_id):
                    # #285: lo que quedaba en cola de un run abortado no llega (ni abre push).
                    logger.debug(f"[{self._client_id}] push {kind} de un run abortado descartado")
                elif kind == "turn_start":
                    await self._push_start(arg, run_id)
                elif kind == "chat":
                    await self._push_chat(arg)
                elif kind == "tool":
                    await self._push_tool(arg)
                elif kind in ("turn_end", "turn_error"):
                    await self._push_end(failed=kind == "turn_error")
                else:
                    logger.warning(f"[{self._client_id}] push event desconocido: {kind!r}")
            except Exception as e:  # noqa: BLE001 — frontera: un evento roto no para el worker
                self._log_push_failure(kind, e)
            finally:
                backlog = self._backlog
                backlog.pending = max(0, backlog.pending - 1)
                if backlog.pending == 0:
                    backlog.since = None
                queue.task_done()

    async def _run_push_step(self, what: str, step: Coroutine[Any, Any, None]) -> None:
        try:
            await step
        except Exception as e:  # noqa: BLE001 — frontera: un paso roto no para el worker
            self._log_push_failure(what, e)

    def _log_push_failure(self, what: str, e: Exception) -> None:
        # Solo el tipo: el payload puede llevar texto del usuario.
        logger.warning(f"[{self._client_id}] push {what} falló: {type(e).__name__}")

    def _run_aborted(self, run_id: str | None) -> bool:
        return run_id is not None and self._turn_registry.is_run_aborted(run_id)

    def _abort_run(self, run_id: str | None) -> None:
        if run_id is not None:
            self._turn_registry.abort_run(run_id)

    async def _push_start(self, session_key: str, run_id: str | None = None) -> None:
        if not self._config.push_enabled:
            return  # el push entero se descarta; su fin no encuentra push abierto
        while True:
            # Un turno del usuario vive hasta que sale su audio: no se abre un push encima.
            # asyncio.wait (no await): nuestra cancelación se propaga, la del turno no.
            while self._user_task is not None and not self._user_task.done():
                await asyncio.wait({self._user_task})
            # Abrirlo, bajo el lock: un `send` (start_user_turn) o un corte lo ven entero,
            # registrado y con su turn_start ya en cola, nunca a medias (#202).
            async with self._lock:
                if self._user_task is not None and not self._user_task.done():
                    continue  # un turno que se estaba sustituyendo: el usuario gana
                if self._push is not None:
                    # #84: los `agent start` intermedios (tool use, varios pasos) no abren
                    # otro turno; tampoco el de un push que se está cortando.
                    self._push.progress_at = time.monotonic()
                    logger.debug(
                        f"[{self._client_id}] push_turn_start ignorado: ya hay un push "
                        f"abierto ({self._push.turn_id})"
                    )
                    return
                turn = self._new_turn("push", session_key)
                turn.run_id = run_id
                turn.conversation_id = self._conversation.id_for_key(session_key)
                self._push = turn  # antes de cualquier await
                self._push_text.reset()
                if not await self._open(turn):
                    # Sin turn_start no hay push: se deshace, el resto de su run se descarta
                    # (su `agent start` intermedio no lo reabriría, #84) y, en nuestra
                    # session_key, se aborta en OpenClaw (no sigue corriendo para nadie).
                    if self._push is turn:
                        self._push = None
                    self._abort_run(run_id)
                    if self._may_abort(session_key):
                        await self._orchestrator.abort(session_key)
                    return
                break
        gateway_stats.turn_started("push")
        if not self._wants_audio:
            return
        tts = await self._connect_tts(turn)
        if tts is None:
            return
        if turn.ended:  # cortado mientras conectaba: este TTS es de un push cerrado
            await tts.release()
            return
        turn.tts = tts
        await tts.start(self._config.tts_voice or "")

    async def _push_chat(self, payload: dict) -> None:
        push = self._push
        if push is None or push.ended:
            return  # #109: nada fuera de un turn_start/turn_end
        push.progress_at = time.monotonic()
        # #241: `final` lleva el texto completo sin deltaText (OpenClaw 2026.10.5).
        delta = self._push_text.feed(payload)
        if not delta:
            return
        if self._wants_text:
            try:
                await self._sender.send_json(
                    {"type": "token", "turn_id": push.turn_id, "text": delta}
                )
            except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
                self._log_send_failed("token del push", e)
        if push.tts is not None:
            await push.tts.feed(delta)

    async def _push_tool(self, data: dict) -> None:
        push = self._push
        if push is None or push.ended:
            return
        push.progress_at = time.monotonic()
        if not self._config.tool_calls_enabled:
            return
        tool_call = ToolCallEvent.from_session_tool_payload(data)
        if tool_call is None:
            return
        try:
            await self._sender.send_json(_tool_call_message(push.turn_id, tool_call))
        except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
            self._log_send_failed("tool_call del push", e)

    async def _push_end(self, *, failed: bool) -> None:
        """Fin del agente (`end`, o `error` #300): su audio termina de sonar y se cierra."""
        push = self._push
        if push is None or push.ended:
            # #84: un fin sin push abierto (huérfano o ya cerrado) no emite nada.
            logger.debug(f"[{self._client_id}] push_turn_end ignorado: no hay push abierto")
            return
        push.finishing = True
        if push.tts is not None:
            await push.tts.finish()
        await self._end(push, "error" if failed else "done")

    async def _push_timed_out(self) -> None:
        """#286: un push sin progreso en TURN_TIMEOUT_S se cierra como el turno normal."""
        # Bajo el lock, como todo corte (G-08): un `send` que llegue mientras tanto espera, y
        # el chat.abort del push sale antes que su chat.send. Se decide dentro: si un `send`
        # o un barge-in lo cortaron mientras se esperaba el lock, no hubo timeout.
        async with self._lock:
            push = self._push
            if push is None or push.finishing or push.ended:
                return
            logger.warning(
                f"[{self._client_id}] push {push.turn_id} sin progreso en "
                f"TURN_TIMEOUT_S={settings.TURN_TIMEOUT_S}s — se cierra."
            )
            gateway_stats.turn_error("turn_timeout")
            await self._send_turn_error(push, "turn_timeout")
            await self._cut_push(push.turn_id, "timeout")

    async def _cut_push(self, expected: str | None, reason: EndReason) -> str | None:
        """Corta el push abierto (7e): para su audio y su TTS, lo aborta en OpenClaw si corre
        en nuestra session_key y lo cierra. Con `expected`, solo ese push. Devuelve su
        turn_id, o None si no había."""
        push = self._push
        if push is None or push.ended:
            return None
        if expected is not None and expected != push.turn_id:
            return None
        # Antes del abort: nada más de su run llega al cliente, ni lo que el worker tiene en
        # cola ni lo que venga (también si su session_key no es la nuestra y no se aborta en
        # OpenClaw). abort() ve el run ya con lápida y no espera su `aborted`.
        self._abort_run(push.run_id)
        await self._end(push, reason, abort=True)
        return push.turn_id

    # ── Comunes ───────────────────────────────────────────────────────────

    def _new_turn(self, origin: TurnOrigin, session_key: str | None = None) -> Turn:
        self._seq += 1
        now = time.monotonic()
        return Turn(
            turn_id=f"t-{self._seq}",
            seq=self._seq & 0xFFFF,
            origin=origin,
            started_at=now,
            progress_at=now,
            session_key=session_key,
        )

    async def _open(self, turn: Turn) -> bool:
        try:
            await self._sender.send_json(
                {
                    "type": "turn_start",
                    "turn_id": turn.turn_id,
                    "turn_seq": turn.seq,
                    "conversation": turn.conversation_id,
                    "origin": turn.origin,
                }
            )
        except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
            self._log_send_failed("turn_start", e)
            return False
        turn.opened = True
        self._conversation.note_turn(turn.conversation_id)
        return True

    async def _connect_tts(self, turn: Turn) -> TurnTts | None:
        stream = await self._tts.connect(
            voice=self._tts.resolve_voice(self._config.tts_voice),
            speed=self._config.tts_speed,
            client_id=self._client_id,
        )
        if stream is None:
            return None
        tts = TurnTts(
            stream,
            turn,
            sender=self._sender,
            tracker=self._tracker,
            client_id=self._client_id,
            on_client_gone=self._note_client_gone,
            supervise=self._supervise,
        )
        return tts

    def _note_client_gone(self) -> None:
        self.client_gone = True

    async def _end(
        self, turn: Turn, reason: EndReason, *, abort: bool = False, release: bool = True
    ) -> bool:
        """El único que envía un turn_end: exactamente uno por turno, quien llegue primero.

        Lo marca terminado antes de cualquier await (su audio deja de salir), libera su TTS
        (con su evento `push_tts`), aborta el push en OpenClaw si se pide y es de nuestra
        session_key, y envía turn_end. Cada paso termina aunque cancelen a quien cierra
        (#221, `Teardown`); esa cancelación se relanza después del turn_end.
        """
        if turn.ended:
            return False
        turn.ended = True
        turn.end_reason = reason
        self.last_ended = turn
        logger.debug(f"[{self._client_id}] turno {turn.turn_id} ({turn.origin}) terminado: {reason}")
        td = Teardown(f"[{self._client_id}] fin del turno {turn.turn_id}")
        if release and turn.tts is not None:
            await td.step(self._release_tts(turn.tts, reason), "TTS del turno")
        if abort and turn.session_key is not None and self._may_abort(turn.session_key):
            await td.step(self._orchestrator.abort(turn.session_key), "chat.abort del push")
        if self._push is turn:
            self._push = None
        if turn.opened:
            try:
                await self._sender.send_json(
                    {"type": "turn_end", "turn_id": turn.turn_id, "reason": reason}
                )
            except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
                self._log_send_failed("turn_end", e)
        td.raise_if_cancelled()
        return True

    @staticmethod
    async def _release_tts(tts: TurnTts, reason: EndReason) -> None:
        if reason not in ("done", "error"):
            await tts.drop(f"turno {reason}", logging.INFO)
        await tts.release()

    async def _send_turn_error(self, turn: Turn, code: str) -> None:
        try:
            await self._sender.send_json(
                {
                    "type": "error",
                    "code": "TURN_ERROR",
                    "message": code,
                    "fatal": False,
                    "turn_id": turn.turn_id,
                }
            )
        except Exception as e:  # noqa: BLE001 — frontera: envío a un cliente que puede haberse ido
            self._log_send_failed("TURN_ERROR", e)

    def _log_send_failed(self, what: str, e: BaseException) -> None:
        log_send_failed(self._client_id, what, e)

    # ── Cierre ────────────────────────────────────────────────────────────

    def stop_intake(self) -> None:
        """Desde ya, ni turnos nuevos (G-09) ni eventos de push."""
        self._closing = True

    async def close(self, td: Teardown, limit: float) -> None:
        """Pasos de close_all() para los turnos; cada uno acotado por `limit`.

        7d / G-23: el turno en curso se cancela, nunca se drena; su propio finally envía su
        turn_end con el socket aún abierto (G-10) y el aclose del stream manda chat.abort.
        """
        self._closing = True
        task, turn = self._user_task, self._user_turn
        if task is not None and not task.done():
            self._mark_cancel(task, "closed")
        await td.cancel(task, "turno activo", limit)
        if task is not None and not task.done() and turn is not None and turn.task is task:
            # Abandonado en el tope (TTS lento al limpiar): su finally enviaría turn_end
            # después de session_end, a un sender cerrado. Se envía ahora.
            await self._end(turn, "closed", release=False)

        # El worker antes que el TTS o el audio del push que pueda estar moviendo.
        await td.cancel(self._push_worker, "push worker", limit)
        backlog = self._backlog
        while not backlog.queue.empty():
            backlog.queue.get_nowait()
            backlog.queue.task_done()
        backlog.pending = 0
        backlog.since = None
        push = self._push
        if push is not None:
            # Un push abierto (start sin su end) recibe igualmente su turn_end, tras su último
            # frame de audio: nunca un turn_start solo.
            if push.tts is not None:
                await td.step(push.tts.release(), "cierre del TTS del push", limit)
            await self._end(push, "closed", release=False)
