import logging

from src.services.openclaw.registry import (
    ClientRegistry,
    TurnRegistry,
    client_id_from_session_key,
)

logger = logging.getLogger(__name__)


def _as_dict(value: object) -> dict:
    """`payload`/`data` may arrive as null or a non-object: treat that as empty (G-03)."""
    return value if isinstance(value, dict) else {}


class FrameDispatcher:
    """Routes incoming OpenClaw frames to the correct queue or bridge.

    Knows about TurnRegistry and ClientRegistry. Does not touch WebSocket or TTS.

    Turn queues are bounded and fed with non-blocking puts (issue #130 S3b).

    Push handlers are never awaited here: this runs inside OpenClaw's _listen loop,
    so anything slow would stall every session sharing the connection (issue #130
    S3a). The routing decision (including the #112 normal-turn check) is still taken
    synchronously, and only the resulting work is queued on the bridge's push worker.
    """

    def __init__(self, turn_registry: TurnRegistry, client_registry: ClientRegistry) -> None:
        self._turns = turn_registry
        self._clients = client_registry
        # Frame/event kinds already reported as unhandled — each is logged at INFO
        # once (issue #136), so protocol drift is visible without a per-frame flood.
        self._unknown_seen: set[str] = set()

    async def dispatch(self, frame: dict) -> None:
        ftype = frame.get("type")
        if ftype == "res":
            await self._handle_res(frame)
        elif ftype == "event":
            await self._handle_event(frame)
        else:
            self._note_unhandled(f"frame type {ftype!r}")

    def _note_unhandled(self, what: str) -> None:
        """Log an unhandled frame/event kind once at INFO, then at DEBUG. Only the
        kind is logged, never the payload (it may carry user content)."""
        if what in self._unknown_seen:
            logger.debug("unhandled OpenClaw %s", what)
            return
        self._unknown_seen.add(what)
        logger.info(
            "unhandled OpenClaw %s (first occurrence; further ones are logged at DEBUG) — "
            "possible protocol drift, see docs/openclaw-protocol.md",
            what,
        )

    async def _handle_res(self, frame: dict) -> None:
        # A res ends the turn only when it is a failure (ok:false). An ok:true ack
        # ("started", ...) is not a turn end (G-47); the real completion is the chat
        # event with state "final" (server 2026.6.11+, the minimum supported).
        if frame.get("ok") is False:
            self._turns.deliver_by_req(frame.get("id", ""), ("done", frame))

    async def _handle_event(self, frame: dict) -> None:
        event = frame.get("event", "")
        payload = _as_dict(frame.get("payload"))
        if event == "chat":
            await self._handle_chat(payload)
        elif event == "agent":
            await self._handle_agent_lifecycle(payload)
        elif event == "session.tool":
            await self._handle_session_tool(payload)
        else:
            self._note_unhandled(f"event {event!r}")

    async def _handle_chat(self, payload: dict) -> None:
        sk = payload.get("sessionKey")
        if sk is None:
            return
        if self._turns.deliver_by_session(sk, ("chat", payload)):
            return
        if self._turns.is_aborted(sk):
            return  # late frame of a turn aborted for overflow (#130 S3b)
        client_id = client_id_from_session_key(sk)
        bridge = self._clients.get(client_id)
        if bridge is not None:
            bridge.enqueue_push("chat", payload)

    async def _handle_agent_lifecycle(self, payload: dict) -> None:
        sk = payload.get("sessionKey")
        if sk is None:
            return
        phase = _as_dict(payload.get("data")).get("phase")
        client_id = client_id_from_session_key(sk)
        bridge = self._clients.get(client_id)
        if bridge is None:
            return
        if phase == "start":
            if self._turns.get_queue_by_session(sk) is not None:
                # Issue #112: a normal chat.send turn is active for this
                # session_key — OpenClaw's own agent start pairs (tool use,
                # multi-step reasoning) must not open a second client-facing
                # turn on top of it. That turn's chat tokens and session.tool
                # events already reach the client via _handle_chat/
                # _handle_session_tool's own queue routing; only the
                # lifecycle framing (which would open a *second* turn) is
                # dropped.
                logger.debug(
                    "agent start suppressed: normal turn active for sk=%s", sk
                )
                return
            if self._turns.is_aborted(sk):
                logger.debug("agent start suppressed: turn aborted for overflow sk=%s", sk)
                return
            bridge.enqueue_push("turn_start", sk)
        elif phase == "end":
            # Always forwarded, never suppressed: on_push_turn_end() already
            # no-ops when no push turn is open (issue #84's orphan-end
            # branch), so a start suppressed above never produces a
            # client-visible turn_end here. Suppressing "end" too — as this
            # guard originally did — could orphan a push turn that was
            # already open *before* a normal turn started for the same
            # session_key: its own "end" would be dropped, leaving
            # _push_turn_open stuck True, its TTS connection never closed,
            # and the push path permanently dead for the rest of the session
            # (caught in final review, 2026-08-04).
            bridge.enqueue_push("turn_end", sk)

    async def _handle_session_tool(self, payload: dict) -> None:
        sk = payload.get("sessionKey")
        if sk is None:
            return
        data = _as_dict(payload.get("data"))
        if data.get("phase") not in ("start", "result"):
            return
        if self._turns.deliver_by_session(sk, ("tool", data)):
            return
        if self._turns.is_aborted(sk):
            return  # late frame of a turn aborted for overflow (#130 S3b)
        client_id = client_id_from_session_key(sk)
        bridge = self._clients.get(client_id)
        if bridge is not None:
            bridge.enqueue_push("tool", data)
