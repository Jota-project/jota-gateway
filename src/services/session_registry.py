from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from src.services.pipeline_tracker import PipelineEvent, PipelineTracker


@dataclass
class SessionRecord:
    session_id: str
    client_id: str
    input_mode: str
    output_mode: list[str]
    started_at: datetime
    ended_at: Optional[datetime]
    status: Literal["active", "completed", "error"]
    events: "list[PipelineEvent]"
    tracker: "PipelineTracker"


class SessionRegistry:
    def __init__(self, maxsize: int = 100):
        self._sessions: dict[str, SessionRecord] = {}
        self._maxsize = maxsize

    def register(self, tracker: "PipelineTracker") -> SessionRecord:
        if tracker.session_id in self._sessions:
            del self._sessions[tracker.session_id]
        record = SessionRecord(
            session_id=tracker.session_id,
            client_id=tracker.client_id,
            input_mode=tracker.input_mode,
            output_mode=tracker.output_mode,
            started_at=datetime.now(timezone.utc),
            ended_at=None,
            status="active",
            events=tracker.events,
            tracker=tracker,
        )
        self._sessions[tracker.session_id] = record
        self._evict_if_needed()
        return record

    def close(self, session_id: str, status: Literal["active", "completed", "error"] = "completed") -> None:
        record = self._sessions.get(session_id)
        if record:
            record.status = status
            record.ended_at = datetime.now(timezone.utc)

    def get_all(self) -> list[SessionRecord]:
        return list(reversed(self._sessions.values()))

    def get(self, session_id: str) -> Optional[SessionRecord]:
        return self._sessions.get(session_id)

    def _evict_if_needed(self) -> None:
        if len(self._sessions) <= self._maxsize:
            return
        for sid, record in list(self._sessions.items()):
            if record.status != "active":
                del self._sessions[sid]
                return
