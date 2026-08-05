import uuid
from datetime import UTC, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class ClientRecord(SQLModel, table=True):
    __tablename__ = "clients"

    id: str = Field(default_factory=_gen_uuid, primary_key=True)
    name: str
    client_key: str = Field(unique=True, index=True)
    is_active: bool = Field(default=True)
    client_type: Optional[str] = Field(default=None)
    default_agent: Optional[str] = Field(default=None)
    allowed_agents: Optional[str] = Field(default=None)   # JSON list serializado
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Pipeline config
    stt_language: str = Field(default="es")
    stt_vad_thold: float = Field(default=0.0)
    tts_voice: str = Field(default="af_heart")
    tts_speed: float = Field(default=1.0)
    barge_in_enabled: bool = Field(default=True)
    barge_in_min_chars: int = Field(default=5)
    output_mode: Optional[str] = Field(default=None)      # JSON list, e.g. '["audio","text"]'
    silence_timeout_s: float = Field(default=2.0)
    max_silence_turns: int = Field(default=3)
    push_enabled: bool = Field(default=True)
    tool_calls_enabled: bool = Field(default=False)
