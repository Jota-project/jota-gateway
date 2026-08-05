import asyncio
from typing import Any


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

    def get_queue_by_session(self, session_key: str) -> asyncio.Queue | None:
        return self._sessions.get(session_key)

    def get_queue_by_req(self, req_id: str) -> asyncio.Queue | None:
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
        self._clients[client_id] = bridge

    def unregister(self, client_id: str) -> None:
        self._clients.pop(client_id, None)

    def get(self, client_id: str) -> Any | None:
        return self._clients.get(client_id)

    async def broadcast_status(self, service: str, state: str) -> None:
        for bridge in list(self._clients.values()):
            try:
                await bridge.notify_service_status(service, state)
            except Exception:
                pass  # one dead/misbehaving session must not block the rest
