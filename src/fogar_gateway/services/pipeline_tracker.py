import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fogar_gateway.services.outbound import OutboundSender

if TYPE_CHECKING:
    from fogar_gateway.services.session_registry import SessionRegistry, SessionStatus

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
        output_mode: Sequence[str],
        registry: "SessionRegistry",
        sender: OutboundSender | None = None,
    ):
        self.session_id = session_id
        self.client_id = client_id
        self.input_mode = input_mode
        self.output_mode = list(output_mode)
        self.events: list[PipelineEvent] = []
        self._sender = sender  # None: nothing is sent to a client (HTTP sessions)
        self._registry = registry
        self._started_at: float = time.monotonic()
        self._last_event_at: float = self._started_at
        # Two independent flags rather than one: if the first close() is cancelled
        # inside record(), a retry must still reach registry.close() (issue #127).
        self._session_end_recorded = False
        self._registry_closed = False
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
            extra={
                "session_id": self.session_id,
                "client_id": self.client_id,
                "stage": stage,
                "turn": self._turn,
                "meta": meta,
            },
        )

        if self._sender is not None and "status" in self.output_mode:
            try:
                await self._sender.send_json(
                    {
                        "type": "pipeline_event",
                        "stage": stage,
                        "elapsed_ms": round(elapsed_ms),
                        "turn": self._turn,
                    }
                )
            except Exception as e:  # noqa: BLE001 — informativo: nunca rompe el pipeline
                logger.debug("pipeline_event no enviado (%s)", type(e).__name__)

        return event

    async def close(self, status: "SessionStatus" = "completed", **end_meta) -> None:
        if not self._session_end_recorded:
            self._session_end_recorded = True
            duration_s = round(time.monotonic() - self._started_at, 2)
            await self.record(
                "session_end", turn_count=self._turn, duration_s=duration_s, **end_meta
            )
        if not self._registry_closed:
            self._registry_closed = True
            self._registry.close(self.session_id, status)

    def _find_last(
        self, stage: str, turn: int | None = None, origin_of: PipelineEvent | None = None
    ) -> PipelineEvent | None:
        """Last `stage` event (of `turn`; with `origin_of`, of the same origin as it)."""
        for e in reversed(self.events):
            if (
                e.stage == stage
                and (turn is None or e.turn == turn)
                and (origin_of is None or e.meta.get("origin") == origin_of.meta.get("origin"))
            ):
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
        # A push's TTS events carry origin="push" and share the counter of the last turn
        # (#271): pair each first chunk with the start of its own TTS.
        start = self._find_last("tts_start", turn=first_chunk.turn, origin_of=first_chunk)
        if start is None:
            return None
        return round(first_chunk.ts_ms - start.ts_ms, 1)

    def turn_e2e_latencies(self) -> dict[int, float]:
        """`tts_done` minus the final transcription that triggered it, per turn.

        The final is recorded before its turn starts (the client sends `send` after
        it), so the utterance behind turn N carries turn N-1 (#250).
        """
        finals: dict[int, PipelineEvent] = {}
        dones: dict[int, PipelineEvent] = {}
        for e in self.events:
            if e.stage == "transcription_final":
                finals[e.turn + 1] = e
            elif e.stage == "tts_done" and "origin" not in e.meta:
                # A push's tts_done (origin="push") answers no utterance of the user.
                dones[e.turn] = e
        return {
            turn: round(done.ts_ms - finals[turn].ts_ms, 1)
            for turn, done in dones.items()
            if turn in finals
        }

    def turn_e2e_ms(self) -> float | None:
        done = next(
            (e for e in reversed(self.events) if e.stage == "tts_done" and "origin" not in e.meta),
            None,
        )
        if done is None:
            return None
        return self.turn_e2e_latencies().get(done.turn)

