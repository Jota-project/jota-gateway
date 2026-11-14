import asyncio
import logging
import time
from typing import Any

from fogar_gateway.core.config import settings
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
        self._held: dict[str, list[tuple[tuple, str]]] = {}
        # req_id -> deadline: turns aborted before their ack arrived; the ack tombstones it.
        self._abandoned_reqs: dict[str, float] = {}
        # runId -> deadline; see RUN_ABORT_TOMBSTONE_S.
        self._aborted_runs: dict[str, float] = {}
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

    def register(self, req_id: str, session_key: str, *, by_run: bool = True) -> asyncio.Queue:
        """`by_run=False` (chat.abort's own registration, whose answer never reaches the
        dispatcher) takes every frame of the key, whatever its run."""
        if session_key in self._sessions:
            raise TurnInProgress(session_key)
        size = self._max_queue_size if self._max_queue_size is not None else settings.TURN_QUEUE_MAX
        queue: asyncio.Queue = asyncio.Queue(maxsize=size)
        self._sessions[session_key] = queue
        self._req_to_session[req_id] = session_key
        self._session_owner[session_key] = req_id
        if not by_run:
            self._run_of_req[req_id] = None
        return queue

    def unregister(self, session_key: str, req_id: str) -> None:
        # Only pop the session_key's queue if req_id is still its current
        # owner — a stale/belated unregister() for a turn that was already
        # superseded must not evict a different turn's live queue.
        if self._session_owner.get(session_key) == req_id:
            self._sessions.pop(session_key, None)
            self._session_owner.pop(session_key, None)
            self._overflowed.discard(session_key)
        self._req_to_session.pop(req_id, None)
        self._run_of_req.pop(req_id, None)
        self._held.pop(req_id, None)

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
        for item, item_run in held:
            if run_id is None or item_run == run_id:
                self._deliver(session_key, queue, item)
            else:
                logger.debug("frame of another run dropped (sk=%s)", session_key)

    def run_of(self, req_id: str) -> str | None:
        return self._run_of_req.get(req_id)

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
        """(current qsize or 0, max seen)."""
        q = self._sessions.get(session_key)
        return (q.qsize() if q is not None else 0, self._peak.get(session_key, 0))

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
        Until the turn's ack names its run, frames with a run id are held, not delivered.
        """
        queue = self._sessions.get(session_key)
        if queue is None:
            return False
        owner = self._session_owner.get(session_key)
        if run_id is not None and owner is not None:
            if owner not in self._run_of_req:
                held = self._held.setdefault(owner, [])
                if queue.maxsize > 0 and len(held) >= queue.maxsize:
                    self._overflow(session_key, queue)
                else:
                    held.append((item, run_id))
                return True
            own_run = self._run_of_req[owner]
            if own_run is not None and run_id != own_run:
                logger.debug("frame of another run dropped (sk=%s)", session_key)
                return True
        self._deliver(session_key, queue, item)
        return True

    def deliver_by_req(self, req_id: str, item: tuple) -> bool:
        session_key = self._req_to_session.get(req_id)
        if session_key is None:
            return False
        return self.deliver_by_session(session_key, item)

    def _deliver(self, session_key: str, queue: asyncio.Queue, item: tuple) -> None:
        if session_key in self._overflowed:
            return  # turn already flagged for abort: drop
        try:
            queue.put_nowait(item)
            self._peak[session_key] = max(self._peak.get(session_key, 0), queue.qsize())
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
        self._sessions.clear()
        self._req_to_session.clear()
        self._session_owner.clear()
        self._run_of_req.clear()
        self._held.clear()


def _prune(deadlines: dict[str, float]) -> None:
    now = time.monotonic()
    for key in [k for k, deadline in deadlines.items() if deadline <= now]:
        del deadlines[key]


class ClientRegistry:
    """Maps active client_id → FogarBridge for push delivery."""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}

    def register(self, client_id: str, bridge: Any) -> None:
        # A live mapping being overwritten means a client reconnected while the
        # previous session's bridge was still registered (reconnect race, #113).
        # The stale bridge's later unregister() is now a safe no-op (identity
        # check below), but surface the overlap so it's diagnosable. client_id
        # is the client UUID, safe to log (never the client_key).
        if self._clients.get(client_id) is not None:
            logger.warning(
                "ClientRegistry: client_id=%s re-registered while a previous "
                "bridge was still active (reconnect race)",
                client_id,
            )
        self._clients[client_id] = bridge

    def unregister(self, client_id: str, expected_bridge: Any) -> None:
        # Only remove the mapping if expected_bridge is still the current owner
        # of client_id. A reconnect race can register a newer bridge under the
        # same client_id (register() overwrites); when the older bridge tears
        # down late it must not evict the live newer one (issue #113). Mirrors
        # TurnRegistry.unregister's owner check for #99.
        if self._clients.get(client_id) is expected_bridge:
            self._clients.pop(client_id, None)

    def get(self, client_id: str) -> Any | None:
        return self._clients.get(client_id)

    def bridges(self) -> list[Any]:
        # Un solapamiento de reconexión del mismo client_id solo deja el bridge más nuevo.
        return list(self._clients.values())

    async def broadcast_status(self, service: str, state: str) -> None:
        for bridge in list(self._clients.values()):
            try:
                await bridge.notify_service_status(service, state)
            except Exception as e:  # noqa: BLE001 — frontera: una sesión rota no bloquea al resto
                logger.debug("broadcast_status: notify failed (%s)", describe_error(e))
