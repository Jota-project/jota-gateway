"""Contadores globales del gateway (#219). Solo contadores e IDs, nunca texto ni claves."""

import time
from collections import Counter
from datetime import UTC, datetime
from typing import Literal

CloseReason = Literal[
    "client_disconnect",
    "idle_timeout",
    "silence_timeout",
    "slow_client",
    "revoked",
    "shutdown",
    "error",
    "unknown",
]


class GatewayStats:
    # Sin locks: todo se llama desde el event loop (medir es O(1)).
    def __init__(self) -> None:
        self.started_at = datetime.now(UTC)
        self.started_monotonic = time.monotonic()
        self.reset()

    def reset(self) -> None:
        self._sessions_opened = 0
        self._sessions_closed: Counter[str] = Counter()
        self._handshake_rejected: Counter[str] = Counter()
        self._turns: Counter[str] = Counter()
        self._turn_errors: Counter[str] = Counter()

    def session_opened(self) -> None:
        self._sessions_opened += 1

    def session_closed(self, reason: CloseReason) -> None:
        self._sessions_closed[reason] += 1

    def handshake_rejected(self, reason: str) -> None:
        self._handshake_rejected[reason] += 1

    def turn_started(self, kind: Literal["normal", "push"]) -> None:
        self._turns[kind] += 1

    def turn_error(self, code: str) -> None:
        self._turn_errors[code] += 1

    def snapshot(self) -> dict:
        return {
            "sessions_opened": self._sessions_opened,
            "sessions_closed": dict(self._sessions_closed),
            "handshake_rejected": dict(self._handshake_rejected),
            "turns": {"normal": self._turns["normal"], "push": self._turns["push"]},
            "turn_errors": dict(self._turn_errors),
        }


gateway_stats = GatewayStats()
