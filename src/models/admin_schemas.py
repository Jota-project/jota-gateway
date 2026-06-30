"""
admin_schemas.py
~~~~~~~~~~~~~~~~
Schemas Pydantic para la API de administración /admin/clients/*.

Separados de schemas.py (modelos runtime del bridge) para no mezclar
contratos internos con la interfaz HTTP de administración.
"""
import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from src.db.models import ClientRecord


class ClientCreate(BaseModel):
    name: str
    client_type: Optional[str] = None
    default_agent: Optional[str] = None
    allowed_agents: Optional[list[str]] = None
    # Pipeline config
    stt_language: str = "es"
    stt_vad_thold: float = 0.0
    tts_voice: str = "af_heart"
    tts_speed: float = 1.0
    barge_in_enabled: bool = True
    barge_in_min_chars: int = 5
    output_mode: Optional[list[str]] = None
    silence_timeout_s: float = 2.0
    # Personalización
    system_prompt_extra: Optional[str] = None
    preferred_model_id: Optional[str] = None
    # Metadata
    room: Optional[str] = None
    tags: Optional[list[str]] = None


class ClientUpdate(BaseModel):
    """Todos los campos opcionales — PATCH semántico (exclude_unset)."""
    name: Optional[str] = None
    is_active: Optional[bool] = None
    client_type: Optional[str] = None
    default_agent: Optional[str] = None
    allowed_agents: Optional[list[str]] = None
    stt_language: Optional[str] = None
    stt_vad_thold: Optional[float] = None
    tts_voice: Optional[str] = None
    tts_speed: Optional[float] = None
    barge_in_enabled: Optional[bool] = None
    barge_in_min_chars: Optional[int] = None
    output_mode: Optional[list[str]] = None
    silence_timeout_s: Optional[float] = None
    system_prompt_extra: Optional[str] = None
    preferred_model_id: Optional[str] = None
    room: Optional[str] = None
    tags: Optional[list[str]] = None


class ClientResponse(BaseModel):
    id: str
    name: str
    client_key: str
    is_active: bool
    client_type: Optional[str]
    default_agent: Optional[str]
    allowed_agents: Optional[list[str]]
    created_at: datetime
    # Pipeline config
    stt_language: str
    stt_vad_thold: float
    tts_voice: str
    tts_speed: float
    barge_in_enabled: bool
    barge_in_min_chars: int
    output_mode: Optional[list[str]]
    silence_timeout_s: float
    # Personalización
    system_prompt_extra: Optional[str]
    preferred_model_id: Optional[str]
    # Metadata
    room: Optional[str]
    tags: Optional[list[str]]

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
            system_prompt_extra=r.system_prompt_extra,
            preferred_model_id=r.preferred_model_id,
            room=r.room,
            tags=json.loads(r.tags) if r.tags else None,
        )
