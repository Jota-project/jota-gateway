"""
admin_schemas.py
~~~~~~~~~~~~~~~~
Schemas Pydantic para la API de administración /admin/clients/*.

Separados de schemas.py (modelos runtime del bridge) para no mezclar
contratos internos con la interfaz HTTP de administración.
"""

import json
from datetime import datetime

from pydantic import BaseModel

from src.db.models import ClientRecord


class ClientCreate(BaseModel):
    name: str
    client_key: str | None = None  # si se omite, se genera uno aleatorio
    client_type: str | None = None
    default_agent: str | None = None
    allowed_agents: list[str] | None = None
    # Pipeline config
    stt_language: str = "es"
    stt_vad_thold: float = 0.0
    tts_voice: str = "af_heart"
    tts_speed: float = 1.0
    barge_in_enabled: bool = True
    barge_in_min_chars: int = 5
    output_mode: list[str] | None = None
    silence_timeout_s: float = 2.0
    max_silence_turns: int = 3
    push_enabled: bool = True
    tool_calls_enabled: bool = False


class ClientUpdate(BaseModel):
    """Todos los campos opcionales — PATCH semántico (exclude_unset)."""

    name: str | None = None
    is_active: bool | None = None
    client_type: str | None = None
    default_agent: str | None = None
    allowed_agents: list[str] | None = None
    stt_language: str | None = None
    stt_vad_thold: float | None = None
    tts_voice: str | None = None
    tts_speed: float | None = None
    barge_in_enabled: bool | None = None
    barge_in_min_chars: int | None = None
    output_mode: list[str] | None = None
    silence_timeout_s: float | None = None
    max_silence_turns: int | None = None
    push_enabled: bool | None = None
    tool_calls_enabled: bool | None = None


class ClientResponse(BaseModel):
    id: str
    name: str
    client_key: str
    is_active: bool
    client_type: str | None
    default_agent: str | None
    allowed_agents: list[str] | None
    created_at: datetime
    # Pipeline config
    stt_language: str
    stt_vad_thold: float
    tts_voice: str
    tts_speed: float
    barge_in_enabled: bool
    barge_in_min_chars: int
    output_mode: list[str] | None
    silence_timeout_s: float
    max_silence_turns: int
    push_enabled: bool
    tool_calls_enabled: bool

    @classmethod
    def from_record(cls, r: ClientRecord) -> "ClientResponse":
        return cls(
            id=r.id,
            name=r.name,
            client_key=r.client_key,
            is_active=r.is_active,
            client_type=r.client_type,
            default_agent=r.default_agent,
            allowed_agents=json.loads(r.allowed_agents) if r.allowed_agents else None,
            created_at=r.created_at,
            stt_language=r.stt_language,
            stt_vad_thold=r.stt_vad_thold,
            tts_voice=r.tts_voice,
            tts_speed=r.tts_speed,
            barge_in_enabled=r.barge_in_enabled,
            barge_in_min_chars=r.barge_in_min_chars,
            output_mode=json.loads(r.output_mode) if r.output_mode else None,
            silence_timeout_s=r.silence_timeout_s,
            max_silence_turns=r.max_silence_turns,
            push_enabled=r.push_enabled,
            tool_calls_enabled=r.tool_calls_enabled,
        )
