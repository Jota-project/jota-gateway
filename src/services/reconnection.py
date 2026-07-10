from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ConnectionState(Enum):
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DEGRADED = "DEGRADED"


@dataclass
class ServiceStatus:
    name: str
    state: ConnectionState
    connected_at: Optional[datetime]
    reconnect_attempts: int
    last_error: Optional[str]


def to_wire_state(state: ConnectionState) -> str:
    """Maps a ConnectionState to the client-facing `status` wire vocabulary.

    Only call this at an actual transition — the caller decides whether a
    transition is worth notifying a client about at all (e.g. don't call
    this for CONNECTED on a session's very first successful connect, since
    nothing was ever broken).
    """
    return {
        ConnectionState.CONNECTED: "restored",
        ConnectionState.RECONNECTING: "reconnecting",
        ConnectionState.DEGRADED: "unavailable",
    }[state]
