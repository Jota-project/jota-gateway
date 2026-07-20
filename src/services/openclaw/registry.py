import asyncio
import logging
from typing import Any, Optional

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


class TurnInProgress(Exception):
    """Raised when register() is called for a session_key with an active turn."""


class TurnRegistry:
    """Routes active OpenClaw turns (req_id / session_key) to asyncio queues.

    Queue message protocol:
      ("chat", payload_dict)  — streaming token
      ("done", frame_dict)    — final res frame
      ("error", str)          — internal error (e.g. reconnect)
    """

    def __init__(self) -> None:
        self._sessions: dict[str, asyncio.Queue] = {}
        self._req_to_session: dict[str, str] = {}
        # session_key -> req_id of the turn that currently owns it. Lets
        # unregister() tell an in-flight turn's belated cleanup apart from a
        # newer turn that has since taken over the same session_key, instead
        # of popping by session_key alone (see issue #99).
        self._session_owner: dict[str, str] = {}

    def register(self, req_id: str, session_key: str) -> asyncio.Queue:
        if session_key in self._sessions:
            raise TurnInProgress(session_key)
        queue: asyncio.Queue = asyncio.Queue()
        self._sessions[session_key] = queue
        self._req_to_session[req_id] = session_key
        self._session_owner[session_key] = req_id
        return queue

    def unregister(self, session_key: str, req_id: str) -> None:
        # Only pop the session_key's queue if req_id is still its current
        # owner — a stale/belated unregister() for a turn that was already
        # superseded must not evict a different turn's live queue.
        if self._session_owner.get(session_key) == req_id:
            self._sessions.pop(session_key, None)
            self._session_owner.pop(session_key, None)
        self._req_to_session.pop(req_id, None)

    def get_queue_by_session(self, session_key: str) -> Optional[asyncio.Queue]:
        return self._sessions.get(session_key)

    def get_queue_by_req(self, req_id: str) -> Optional[asyncio.Queue]:
        sk = self._req_to_session.get(req_id)
        return self._sessions.get(sk) if sk else None

    def error_all(self, message: str) -> None:
        for queue in self._sessions.values():
            queue.put_nowait(("error", message))
        self._sessions.clear()
        self._req_to_session.clear()
        self._session_owner.clear()


class ClientRegistry:
    """Maps active client_id → JotaBridge for push delivery."""

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
                "bridge was still active (reconnect race)", client_id
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

    def get(self, client_id: str) -> Optional[Any]:
        return self._clients.get(client_id)

    async def broadcast_status(self, service: str, state: str) -> None:
        for bridge in list(self._clients.values()):
            try:
                await bridge.notify_service_status(service, state)
            except Exception:
                pass  # one dead/misbehaving session must not block the rest
