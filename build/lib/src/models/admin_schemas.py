"""
admin_schemas.py
~~~~~~~~~~~~~~~~
Schemas Pydantic para la API de administración /admin/clients/*.

Separados de schemas.py (modelos runtime del bridge) para no mezclar
contratos internos con la interfaz HTTP de administración.
"""

import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.db.models import ClientRecord
from src.models.schemas import OutputMode

# Etiqueta de cliente. Solo se valida la ENTRADA (create/update/CLI); la respuesta
# sigue siendo `str` para no romper la lectura de filas antiguas con valores libres (#122).
ClientType = Literal["ha", "esp32", "web", "app"]


# Tipos acotados solo para la ENTRADA (#217 / G-28). Los topes superiores evitan
# desbordes de entero en SQLite (OverflowError -> 500) y valores absurdos.
_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]
_Agent = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_VadThold = Annotated[float, Field(ge=0.0, le=1.0)]
_Speed = Annotated[float, Field(gt=0.0, le=10.0)]
_Seconds = Annotated[float, Field(gt=0.0, le=86_400.0)]
_MinChars = Annotated[int, Field(ge=1, le=10_000)]
_Turns = Annotated[int, Field(ge=1, le=10_000)]

# Campos de PATCH que mapean a columnas NOT NULL: `null` explícito -> 422.
_NOT_NULL_UPDATE_FIELDS = (
    "name",
    "is_active",
    "stt_language",
    "stt_vad_thold",
    "tts_voice",
    "tts_speed",
    "barge_in_enabled",
    "barge_in_min_chars",
    "silence_timeout_s",
    "max_silence_turns",
    "push_enabled",
    "tool_calls_enabled",
)


class ClientCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    name: _Name
    client_key: str | None = None  # si se omite (o es ""), se genera uno aleatorio
    client_type: ClientType | None = None
    default_agent: _Agent | None = None
    allowed_agents: list[_Agent] | None = None
    # Pipeline config
    stt_language: _Text = "es"
    stt_vad_thold: _VadThold = 0.0
    tts_voice: _Text = "af_heart"
    tts_speed: _Speed = 1.0
    barge_in_enabled: bool = True
    barge_in_min_chars: _MinChars = 5
    output_mode: list[OutputMode] | None = None
    silence_timeout_s: _Seconds = 2.0
    max_silence_turns: _Turns = 3
    push_enabled: bool = True
    tool_calls_enabled: bool = False


class ClientUpdate(BaseModel):
    """Todos los campos opcionales — PATCH semántico (exclude_unset).

    Un `null` explícito solo vale para los campos que admiten NULL en la BD
    (`client_type`, `default_agent`, `allowed_agents`, `output_mode`); en el resto es 422.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    name: _Name | None = None
    is_active: bool | None = None
    client_type: ClientType | None = None
    default_agent: _Agent | None = None
    allowed_agents: list[_Agent] | None = None
    stt_language: _Text | None = None
    stt_vad_thold: _VadThold | None = None
    tts_voice: _Text | None = None
    tts_speed: _Speed | None = None
    barge_in_enabled: bool | None = None
    barge_in_min_chars: _MinChars | None = None
    output_mode: list[OutputMode] | None = None
    silence_timeout_s: _Seconds | None = None
    max_silence_turns: _Turns | None = None
    push_enabled: bool | None = None
    tool_calls_enabled: bool | None = None

    @model_validator(mode="after")
    def _reject_null_for_not_null_columns(self) -> "ClientUpdate":
        for field in _NOT_NULL_UPDATE_FIELDS:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} no admite null")
        return self


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
