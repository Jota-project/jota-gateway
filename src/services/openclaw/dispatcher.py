import logging

from src.services.openclaw.registry import (
    ClientRegistry,
    TurnRegistry,
    client_id_from_session_key,
)

logger = logging.getLogger(__name__)


class FrameDispatcher:
    """Routes incoming OpenClaw frames to the correct queue or bridge.

    Knows about TurnRegistry and ClientRegistry. Does not touch WebSocket or TTS.
    """

    def __init__(self, turn_registry: TurnRegistry, client_registry: ClientRegistry) -> None:
        self._turns = turn_registry
        self._clients = client_registry

    async def dispatch(self, frame: dict) -> None:
        ftype = frame.get("type")
        if ftype == "res":
            await self._handle_res(frame)
        elif ftype == "event":
            await self._handle_event(frame)

    async def _handle_res(self, frame: dict) -> None:
        payload = frame.get("payload", {})
        if payload.get("status") == "started":
            return
        q = self._turns.get_queue_by_req(frame.get("id", ""))
        if q is not None:
            await q.put(("done", frame))

    async def _handle_event(self, frame: dict) -> None:
        event = frame.get("event", "")
        payload = frame.get("payload", {})
        if event == "chat":
            await self._handle_chat(payload)
        elif event == "agent":
            await self._handle_agent_lifecycle(payload)
        elif event == "session.tool":
            await self._handle_session_tool(payload)

    async def _handle_chat(self, payload: dict) -> None:
        sk = payload.get("sessionKey")
        if sk is None:
            return
        q = self._turns.get_queue_by_session(sk)
        if q is not None:
            await q.put(("chat", payload))
            return
        client_id = client_id_from_session_key(sk)
        bridge = self._clients.get(client_id)
        if bridge is not None:
            await bridge.deliver_push(payload)

    async def _handle_agent_lifecycle(self, payload: dict) -> None:
        sk = payload.get("sessionKey")
        if sk is None:
            return
        phase = payload.get("data", {}).get("phase")
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
            await bridge.on_push_turn_start(sk)
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
            await bridge.on_push_turn_end(sk)

    async def _handle_session_tool(self, payload: dict) -> None:
        sk = payload.get("sessionKey")
        if sk is None:
            return
        data = payload.get("data", {})
        if data.get("phase") not in ("start", "result"):
            return
        q = self._turns.get_queue_by_session(sk)
        if q is not None:
            await q.put(("tool", data))
            return
        client_id = client_id_from_session_key(sk)
        bridge = self._clients.get(client_id)
        if bridge is not None:
            await bridge.deliver_push_tool_call(data)
