import asyncio
from typing import Any, Optional


def client_id_from_session_key(session_key: str) -> str:
    """Extract client_id as the last segment after the last colon.

    "agent:main:hab_sito"                     → "hab_sito"
    "agent:plants:telegram:direct:5239228928" → "5239228928"
    """
    return session_key.rsplit(":", 1)[-1]


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

    def register(self, req_id: str, session_key: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._sessions[session_key] = queue
        self._req_to_session[req_id] = session_key
        return queue

    def unregister(self, session_key: str, req_id: str) -> None:
        self._sessions.pop(session_key, None)
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


class ClientRegistry:
    """Maps active client_id → JotaBridge for push delivery."""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}

    def register(self, client_id: str, bridge: Any) -> None:
        self._clients[client_id] = bridge

    def unregister(self, client_id: str) -> None:
        self._clients.pop(client_id, None)

    def get(self, client_id: str) -> Optional[Any]:
        return self._clients.get(client_id)
