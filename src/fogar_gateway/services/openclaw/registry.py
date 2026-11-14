import asyncio
import logging
import time
from typing import Any

from fogar_gateway.core.config import settings
from fogar_gateway.services.conversations import Conversation, ConversationIndex
from fogar_gateway.services.reconnection import describe_error

logger = logging.getLogger(__name__)


def client_id_from_session_key(session_key: str) -> str:
    """Extract client_id as the last segment after the last colon.

    "agent:main:hab_sito"                     → "hab_sito"
    "agent:plants:telegram:direct:5239228928" → "5239228928"
    """
    return session_key.rsplit(":", 1)[-1]


# Stable, distinguishable error content for a rejected duplicate registration —
# defined once here so callers (OpenClawClient.stream_response, the /v1/*
# route layer) can recognize this specific failure instead of duplicating a
# free-form string literal in multiple files.
TURN_IN_PROGRESS_ERROR = "turn_in_progress"

# Error content stream_response() emits when a turn's bounded queue overflowed
# (consumer too slow, issue #130 S3b).
TURN_OVERFLOW_ERROR = "turn_overflow"

# How long, after a turn is aborted for overflow, late frames of that session_key
# are dropped instead of being re-routed as push (issue #130 S3b). Internal
# constant, not a setting: it only has to cover frames already in flight when
# chat.abort is sent.
TURN_ABORT_TOMBSTONE_S = 5.0

# How long, after the gateway aborts a run (barge-in, a replacing `send`, cancel, timeout,
# overflow, a cut push), frames carrying that `runId` are dropped (#285). A runId is never
# reused, so a long tombstone drops nothing else: it covers frames already in flight when
# chat.abort went out and a run that keeps streaming for a while after it.
RUN_ABORT_TOMBSTONE_S = 300.0


class TurnInProgress(Exception):
    """Raised when register() is called for a session_key with an active turn."""


class TurnRegistry:
    """Routes active OpenClaw turns (req_id / session_key) to asyncio queues.

    Queue message protocol:
      ("accepted", run_id)    — the turn's chat.send was acked ok (first item; only for a
                                registration with `announce=True`, stream_response's)
      ("chat", payload_dict)  — streaming token
      ("done", frame_dict)    — final res frame
      ("error", str)          — internal error (e.g. reconnect)
      ("overflow", None)      — the bounded queue overflowed; abort the turn

    Queues are bounded (issue #130 S3b). Producers (FrameDispatcher, running inside
    OpenClaw's _listen loop) use deliver_*(), which never blocks: when a turn's queue
    is full the turn is flagged for abort instead of stalling every multiplexed
    session.
    """

    def __init__(self, max_queue_size: int | None = None) -> None:
        self._max_queue_size = max_queue_size
        # Sessions whose turn overflowed and is awaiting abort: further frames are
        # dropped. Cleared by unregister().
        self._overflowed: set[str] = set()
        # session_key -> monotonic deadline; see TURN_ABORT_TOMBSTONE_S.
        self._tombstones: dict[str, float] = {}
        # runId correlation (#285): the `chat.send` ack carries the run id of the turn.
        # req_id -> runId (None: acked without one, or a registration that does not filter
        # by run). A req_id absent here is still waiting for its ack.
        self._run_of_req: dict[str, str | None] = {}
        # req_id -> frames carrying a runId that arrived before the turn's ack: held until
        # the ack says which run is the turn's own (#285).
        self._held: dict[str, list[tuple[tuple, str | None]]] = {}
        # req_id -> deadline: turns aborted before their ack arrived; the ack tombstones it.
        self._abandoned_reqs: dict[str, float] = {}
        # runId -> deadline; see RUN_ABORT_TOMBSTONE_S.
        self._aborted_runs: dict[str, float] = {}
        # runId -> (session_key, last frame seen): runs OpenClaw is running, from their
        # frames (agent lifecycle, chat, session.tool) until a terminal one. A `chat.send`
        # while another run is active on its key is folded into that run (see
        # docs/openclaw-protocol.md), so the bridge aborts these before sending.
        self._active_runs: dict[str, tuple[str, float]] = {}
        self._sessions: dict[str, asyncio.Queue] = {}
        # session_key -> max queue depth seen; not cleared by unregister (the bridge
        # reads it after the turn and calls pop_peak) (#219).
        self._peak: dict[str, int] = {}
        self._req_to_session: dict[str, str] = {}
        # session_key -> req_id of the turn that currently owns it. Lets
        # unregister() tell an in-flight turn's belated cleanup apart from a
        # newer turn that has since taken over the same session_key, instead
        # of popping by session_key alone (see issue #99).
        self._session_owner: dict[str, str] = {}
        # req_ids whose ack is announced in their queue (register(announce=True)).
        self._announce: set[str] = set()
        # session_key -> waiters of wait_released() (M19: a displaced session letting go).
        self._release_waiters: dict[str, list[asyncio.Future]] = {}

    async def wait_released(self, session_key: str) -> None:
        """Until no turn owns `session_key` (at once if none does). A turn's stream releases
        its key in its `finally`, right before sending its chat.abort."""
        if session_key not in self._sessions:
            return
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        waiters = self._release_waiters.setdefault(session_key, [])
        waiters.append(fut)
        try:
            await fut
        finally:
            if fut in waiters:
                waiters.remove(fut)
            if not waiters:
                self._release_waiters.pop(session_key, None)

    def _wake_released(self, session_key: str) -> None:
        for fut in self._release_waiters.pop(session_key, []):
            if not fut.done():
                fut.set_result(None)

    def register(
        self, req_id: str, session_key: str, *, by_run: bool = True, announce: bool = False
    ) -> asyncio.Queue:
        """`by_run=False` (chat.abort's own registration, whose answer never reaches the
        dispatcher) takes every frame of the key, whatever its run. `announce=True` puts an
        ("accepted", runId) first in the queue when OpenClaw acks the chat.send (M19)."""
        if session_key in self._sessions:
            raise TurnInProgress(session_key)
        size = self._max_queue_size if self._max_queue_size is not None else settings.TURN_QUEUE_MAX
        queue: asyncio.Queue = asyncio.Queue(maxsize=size)
        self._sessions[session_key] = queue
        self._req_to_session[req_id] = session_key
        self._session_owner[session_key] = req_id
        if not by_run:
            self._run_of_req[req_id] = None
        if announce:
            self._announce.add(req_id)
        return queue

    def unregister(self, session_key: str, req_id: str) -> None:
        # Only pop the session_key's queue if req_id is still its current
        # owner — a stale/belated unregister() for a turn that was already
        # superseded must not evict a different turn's live queue.
        if self._session_owner.get(session_key) == req_id:
            self._sessions.pop(session_key, None)
            self._session_owner.pop(session_key, None)
            self._overflowed.discard(session_key)
            self._wake_released(session_key)
        self._req_to_session.pop(req_id, None)
        self._run_of_req.pop(req_id, None)
        self._held.pop(req_id, None)
        self._announce.discard(req_id)

    # ── runId (#285) ──────────────────────────────────────────────────────

    def note_run(self, req_id: str, run_id: str | None) -> None:
        """The ok `res` of `req_id` (its `chat.send` ack) names its run, or None if it
        carries none. A turn already aborted before its ack arrived gets its run
        tombstoned right away. Frames held for the turn until now are delivered if they are
        of its run (all of them when the ack names none) and dropped otherwise."""
        if self._abandoned_reqs.pop(req_id, None) is not None:
            if run_id is not None:
                self.abort_run(run_id)
            return
        session_key = self._req_to_session.get(req_id)
        if session_key is None or req_id in self._run_of_req:
            return
        self._run_of_req[req_id] = run_id
        held = self._held.pop(req_id, [])
        queue = self._sessions.get(session_key)
        if queue is None or self._session_owner.get(session_key) != req_id:
            return
        if req_id in self._announce:
            # OpenClaw accepted the turn's chat.send (M19: /v1 counts the turn from here).
            self._deliver(session_key, queue, ("accepted", run_id))
        for item, item_run in held:
            if run_id is None or item_run is None or item_run == run_id:
                self._deliver(session_key, queue, item)
            else:
                logger.debug("frame of another run dropped (sk=%s)", session_key)

    def abandon(self, req_id: str) -> None:
        """The turn of `req_id` is being aborted: from now on the frames of its run are
        dropped, also when its ack (with the run id) has not arrived yet."""
        run_id = self._run_of_req.get(req_id)
        if run_id is not None:
            self.abort_run(run_id)
        else:
            _prune(self._abandoned_reqs)
            self._abandoned_reqs[req_id] = time.monotonic() + RUN_ABORT_TOMBSTONE_S

    def abort_run(self, run_id: str) -> None:
        _prune(self._aborted_runs)
        self._aborted_runs[run_id] = time.monotonic() + RUN_ABORT_TOMBSTONE_S
        self._active_runs.pop(run_id, None)

    def note_run_activity(self, session_key: str, run_id: str | None, *, ended: bool) -> None:
        """A frame of `run_id` on `session_key`: the run is active until a terminal one."""
        if run_id is None:
            return
        if ended:
            self._active_runs.pop(run_id, None)
        else:
            self._active_runs[run_id] = (session_key, time.monotonic())

    def active_runs(self, session_key: str) -> list[str]:
        """Runs active on `session_key` (not ended, not tombstoned). A run with no frame for
        RUN_ABORT_TOMBSTONE_S is forgotten: its end was probably lost (a reconnect)."""
        stale = time.monotonic() - RUN_ABORT_TOMBSTONE_S
        for run_id in [r for r, (_, seen) in self._active_runs.items() if seen <= stale]:
            del self._active_runs[run_id]
        return [r for r, (sk, _) in self._active_runs.items() if sk == session_key]

    def note_abort_result(self, payload: object) -> None:
        """A `chat.abort` answer (`{ok, aborted, runIds}`) names the runs it aborted."""
        if not isinstance(payload, dict) or payload.get("aborted") is not True:
            return
        run_ids = payload.get("runIds")
        if isinstance(run_ids, list):
            for run_id in run_ids:
                if isinstance(run_id, str):
                    self.abort_run(run_id)

    def is_run_aborted(self, run_id: object) -> bool:
        """True while frames of this run (aborted by the gateway) must be dropped."""
        if not isinstance(run_id, str):
            return False
        deadline = self._aborted_runs.get(run_id)
        if deadline is None:
            return False
        if deadline <= time.monotonic():
            del self._aborted_runs[run_id]
            return False
        return True

    def depth(self, session_key: str) -> tuple[int, int]:
        """(current depth or 0, max seen); the depth counts frames held before the ack."""
        q = self._sessions.get(session_key)
        return (
            q.qsize() + self._held_count(session_key) if q is not None else 0,
            self._peak.get(session_key, 0),
        )

    def _held_count(self, session_key: str) -> int:
        owner = self._session_owner.get(session_key)
        return len(self._held.get(owner, ())) if owner is not None else 0

    def _note_peak(self, session_key: str, depth: int) -> None:
        self._peak[session_key] = max(self._peak.get(session_key, 0), depth)

    def pop_peak(self, session_key: str) -> int:
        return self._peak.pop(session_key, 0)

    def get_queue_by_session(self, session_key: str) -> asyncio.Queue | None:
        return self._sessions.get(session_key)

    def deliver_by_session(
        self, session_key: str, item: tuple, run_id: str | None = None
    ) -> bool:
        """Non-blocking put for the turn owning session_key. True if a turn owns it
        (even if the item was dropped), False if none does.

        `run_id` (#285): a frame of another run than the turn's own never reaches it — not
        a late reply of an aborted turn, not a push whose start was suppressed (#112).
        Until the turn's ack names its run, frames with a run id are held, not delivered;
        once one is held, frames without a run id are held behind it, so the turn sees
        them in arrival order. After an overflow the turn takes nothing more.
        """
        queue = self._sessions.get(session_key)
        if queue is None:
            return False
        if session_key in self._overflowed:
            return True  # flagged for abort once (#130 S3b): drop
        owner = self._session_owner.get(session_key)
        if owner is not None and owner not in self._run_of_req:
            if run_id is not None or self._held.get(owner):
                held = self._held.setdefault(owner, [])
                if queue.maxsize > 0 and len(held) >= queue.maxsize:
                    self._overflow(session_key, queue)
                else:
                    held.append((item, run_id))
                    self._note_peak(session_key, queue.qsize() + len(held))
                return True
        elif run_id is not None and owner is not None:
            own_run = self._run_of_req[owner]
            if own_run is not None and run_id != own_run:
                logger.debug("frame of another run dropped (sk=%s)", session_key)
                return True
        self._deliver(session_key, queue, item)
        return True

    def deliver_by_req(self, req_id: str, item: tuple) -> bool:
        """For the answer to the request itself (a rejected `chat.send`): delivered at
        once; whatever was held for it is of other runs (it never had one)."""
        session_key = self._req_to_session.get(req_id)
        if session_key is None:
            return False
        self._held.pop(req_id, None)
        queue = self._sessions.get(session_key)
        if queue is not None and self._session_owner.get(session_key) == req_id:
            self._deliver(session_key, queue, item)
        return True

    def _deliver(self, session_key: str, queue: asyncio.Queue, item: tuple) -> None:
        if session_key in self._overflowed:
            return  # turn already flagged for abort: drop
        try:
            queue.put_nowait(item)
            self._note_peak(session_key, queue.qsize())
        except asyncio.QueueFull:
            self._overflow(session_key, queue)

    def _overflow(self, session_key: str, queue: asyncio.Queue) -> None:
        logger.warning(
            "turn queue overflow for sk=%s (max=%d): aborting turn", session_key, queue.maxsize
        )
        self._overflowed.add(session_key)
        self._tombstones[session_key] = time.monotonic() + TURN_ABORT_TOMBSTONE_S
        owner = self._session_owner.get(session_key)
        if owner is not None:
            self._held.pop(owner, None)
        # Pending items belong to a turn that is being aborted: discard them.
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(("overflow", None))

    @staticmethod
    def _force_put(queue: asyncio.Queue, item: tuple) -> None:
        """Put item even if the queue is full, discarding the oldest entries."""
        while True:
            try:
                queue.put_nowait(item)
                return
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

    def is_aborted(self, session_key: str) -> bool:
        """True while late frames of an overflow-aborted turn must be dropped."""
        now = time.monotonic()
        for sk in [k for k, deadline in self._tombstones.items() if deadline <= now]:
            del self._tombstones[sk]
        return session_key in self._tombstones

    def error_all(self, message: str) -> None:
        for queue in self._sessions.values():
            self._force_put(queue, ("error", message))
        self._overflowed.clear()
        for session_key in list(self._sessions):
            self._wake_released(session_key)
        self._sessions.clear()
        self._req_to_session.clear()
        self._session_owner.clear()
        self._run_of_req.clear()
        self._held.clear()
        self._announce.clear()
        # The connection is gone: what was running on it is not known any more.
        self._active_runs.clear()


def _prune(deadlines: dict[str, float]) -> None:
    now = time.monotonic()
    for key in [k for k, deadline in deadlines.items() if deadline <= now]:
        del deadlines[key]


class ClientRegistry:
    """Sesiones vivas (#113, M19): quién recibe un push y qué conversación está abierta dónde.

    Cada sesión se registra con su cliente y su conversación (un cliente puede tener varias
    sesiones, cada una en una conversación). Una conversación está abierta en una sola sesión:
    abrirla en otra devuelve la anterior, que el llamador cierra (1008 «conversation opened
    elsewhere»). El push se enruta por su `sessionKey`: conversación (`index`) → la sesión que
    la tiene abierta o, si ninguna, la sesión más reciente de su cliente (p. ej. un recordatorio
    en la conversación de ayer de la tele, que ya está en la de hoy).

    Las identidades se comparan con `is` (#113): el cierre tardío de una sesión vieja nunca
    quita a la nueva.
    """

    def __init__(self, index: ConversationIndex | None = None) -> None:
        self.index = index if index is not None else ConversationIndex()
        self._clients: dict[str, list[Any]] = {}  # client_id -> sesiones vivas, la última al final
        self._open: dict[str, Any] = {}  # conversation id -> la sesión que la tiene abierta

    def register(
        self, client_id: str, bridge: Any, conversation: Conversation | None = None
    ) -> Any | None:
        """Registra la sesión (y abre su conversación). Devuelve la sesión que tenía abierta
        esa conversación, si era otra."""
        live = self._clients.setdefault(client_id, [])
        if not any(b is bridge for b in live):
            live.append(bridge)
        return self.open(conversation, bridge) if conversation is not None else None

    def open(self, conversation: Conversation, bridge: Any) -> Any | None:
        """`bridge` abre `conversation`; devuelve la sesión que la tenía abierta, si era otra."""
        self.index.add(conversation)
        previous = self._open.get(conversation.id)
        self._open[conversation.id] = bridge
        return previous if previous is not None and previous is not bridge else None

    def close(self, conversation_id: str, bridge: Any) -> None:
        if self._open.get(conversation_id) is bridge:
            del self._open[conversation_id]

    def unregister(self, client_id: str, expected_bridge: Any) -> None:
        live = self._clients.get(client_id)
        if live is not None:
            live[:] = [b for b in live if b is not expected_bridge]
            if not live:
                del self._clients[client_id]
        for conversation_id in [c for c, b in self._open.items() if b is expected_bridge]:
            del self._open[conversation_id]

    def get(self, client_id: str) -> Any | None:
        """La sesión viva más reciente del cliente."""
        live = self._clients.get(client_id)
        return live[-1] if live else None

    def holder(self, conversation_id: str) -> Any | None:
        """La sesión que tiene abierta la conversación, si hay una."""
        return self._open.get(conversation_id)

    def route(self, session_key: str) -> Any | None:
        """La sesión que recibe un push de `session_key`, o None."""
        conversation = self.index.get(session_key)
        if conversation is not None:
            return self._open.get(conversation.id) or self.get(conversation.client_id)
        # Clave sin conversación en el índice: una de antes de M19 (`agent:{agent}:{client.id}`)
        # que aún no se importó (OpenClaw estaba caído al arrancar).
        return self.get(client_id_from_session_key(session_key))

    def bridges(self) -> list[Any]:
        return [b for live in self._clients.values() for b in live]

    async def broadcast_status(self, service: str, state: str) -> None:
        for bridge in self.bridges():
            try:
                await bridge.notify_service_status(service, state)
            except Exception as e:  # noqa: BLE001 — frontera: una sesión rota no bloquea al resto
                logger.debug("broadcast_status: notify failed (%s)", describe_error(e))
