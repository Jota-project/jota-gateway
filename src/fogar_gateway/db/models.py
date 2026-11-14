import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class ClientRecord(SQLModel, table=True):
    __tablename__ = "clients"

    id: str = Field(default_factory=_gen_uuid, primary_key=True)
    name: str
    client_key: str = Field(unique=True, index=True)
    is_active: bool = Field(default=True)
    client_type: str | None = Field(default=None)
    default_agent: str | None = Field(default=None)
    allowed_agents: str | None = Field(default=None)  # JSON list serializado
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Pipeline config
    stt_language: str = Field(default="es")
    stt_vad_thold: float = Field(default=0.0)
    tts_voice: str = Field(default="af_heart")
    tts_speed: float = Field(default=1.0)
    barge_in_enabled: bool = Field(default=True)
    barge_in_min_chars: int = Field(default=5)
    output_mode: str | None = Field(default=None)  # JSON list, e.g. '["audio","text"]'
    silence_timeout_s: float = Field(default=2.0)
    max_silence_turns: int = Field(default=3)
    push_enabled: bool = Field(default=True)
    tool_calls_enabled: bool = Field(default=False)


class ConversationRecord(SQLModel, table=True):
    """Una conversación (M19): el índice de una sesión de OpenClaw. Sin texto: lo dicho vive en
    OpenClaw (`chat.history`) y el título también se lee de allí."""

    __tablename__ = "conversations"
    # Una conversación por defecto por cliente, agente y día. `day` es NULL en las demás, y en
    # SQLite los NULL no chocan entre sí en un índice único.
    __table_args__ = (
        Index("uq_conversations_default_day", "client_id", "agent", "day", unique=True),
    )

    id: str = Field(default_factory=_gen_uuid, primary_key=True)
    client_id: str = Field(foreign_key="clients.id", index=True, ondelete="CASCADE")
    agent: str
    session_key: str = Field(unique=True, index=True)
    kind: str  # default | created | legacy
    day: date | None = Field(default=None)  # solo `default`
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_turn_at: datetime | None = Field(default=None)
    turn_count: int = Field(default=0)
