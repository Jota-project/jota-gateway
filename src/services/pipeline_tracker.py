import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.session_registry import SessionRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineEvent:
    stage: str
    ts_ms: float
    elapsed_ms: float
    turn: int = 0
    meta: dict = field(default_factory=dict)


class PipelineTracker:
    def __init__(
        self,
        session_id: str,
        client_id: str,
        input_mode: str,
        output_mode: list[str],
        client_ws,
        registry: "SessionRegistry",
    ):
        self.session_id = session_id
        self.client_id = client_id
        self.input_mode = input_mode
        self.output_mode = output_mode
        self.events: list[PipelineEvent] = []
        self._ws = client_ws
        self._registry = registry
        self._started_at: float = time.monotonic()
        self._last_event_at: float = self._started_at
        self._turn: int = 0

    def start_turn(self) -> int:
        self._turn += 1
        return self._turn

    @property
    def turn_count(self) -> int:
        return self._turn

    async def record(self, stage: str, **meta) -> PipelineEvent:
        now = time.monotonic()
        ts_ms = (now - self._started_at) * 1000
        elapsed_ms = (now - self._last_event_at) * 1000
        self._last_event_at = now

        event = PipelineEvent(
            stage=stage, ts_ms=ts_ms, elapsed_ms=elapsed_ms, turn=self._turn, meta=meta
        )
        self.events.append(event)

        logger.info(
            "pipeline [%s] stage=%s ts_ms=%.0f elapsed_ms=%.0f %s",
            self.session_id,
            stage,
            ts_ms,
            elapsed_ms,
            meta,
        )

        if "status" in self.output_mode:
            try:
                await self._ws.send_json(
                    {
                        "type": "pipeline_event",
                        "stage": stage,
                        "elapsed_ms": round(elapsed_ms),
                        "turn": self._turn,
                    }
                )
            except Exception:
                pass

        return event

    async def close(self, status: str = "completed") -> None:
        duration_s = round(time.monotonic() - self._started_at, 2)
        await self.record("session_end", turn_count=self._turn, duration_s=duration_s)
        self._registry.close(self.session_id, status)

    def _find_last(self, stage: str, turn: int | None = None) -> PipelineEvent | None:
        for e in reversed(self.events):
            if e.stage == stage and (turn is None or e.turn == turn):
                return e
        return None

    def llm_first_token_ms(self) -> float | None:
        first_token = self._find_last("llm_first_token")
        if first_token is None:
            return None
        start = self._find_last("llm_start", turn=first_token.turn)
        if start is None:
            return None
        return round(first_token.ts_ms - start.ts_ms, 1)

    def tts_first_chunk_ms(self) -> float | None:
        first_chunk = self._find_last("tts_first_chunk")
        if first_chunk is None:
            return None
        start = self._find_last("tts_start", turn=first_chunk.turn)
        if start is None:
            return None
        return round(first_chunk.ts_ms - start.ts_ms, 1)

    def turn_e2e_ms(self) -> float | None:
        done = self._find_last("tts_done")
        if done is None:
            return None
        final = self._find_last("transcription_final", turn=done.turn)
        if final is None:
            return None
        return round(done.ts_ms - final.ts_ms, 1)


class _NullWS:
    """Absorbs send_json calls silently. Used by HTTP sessions that have no WebSocket."""

    async def send_json(self, data: dict) -> None:
        pass
