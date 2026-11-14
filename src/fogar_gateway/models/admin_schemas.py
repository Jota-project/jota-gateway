"""
admin_schemas.py
~~~~~~~~~~~~~~~~
Schemas Pydantic para la API de administración /admin/clients/*.

Separados de schemas.py (modelos runtime del bridge) para no mezclar
contratos internos con la interfaz HTTP de administración.
"""

import json
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import Path, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from fogar_gateway.core.logging import fingerprint_key
from fogar_gateway.db.models import ClientRecord
from fogar_gateway.models.schemas import OutputMode

# Etiqueta de cliente. Solo se valida la ENTRADA (create/update/CLI); la respuesta
# sigue siendo `str` para no romper la lectura de filas antiguas con valores libres (#122).
ClientType = Literal["ha", "esp32", "web", "app", "screen"]


# Tipos acotados solo para la ENTRADA (#217 / G-28). Los topes superiores evitan
# desbordes de entero en SQLite (OverflowError -> 500) y valores absurdos.
_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]
# Solo para importar una clave existente (#293): "" no equivale a omitirla.
_ImportedKey = Annotated[str, StringConstraints(min_length=32)]
AgentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
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
    client_key: _ImportedKey | None = None  # si se omite, se genera una aleatoria
    client_type: ClientType | None = None
    default_agent: AgentName | None = None
    allowed_agents: list[AgentName] | None = None
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
    default_agent: AgentName | None = None
    allowed_agents: list[AgentName] | None = None
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
    """Lectura de un cliente: nunca lleva la clave, solo su huella `key_fp` (#293)."""

    id: str
    name: str
    key_fp: str
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
            key_fp=fingerprint_key(r.client_key),
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


class ClientWithKey(ClientResponse):
    """Respuesta de crear y rotar: la única vez que se ve la clave."""

    client_key: str

    @classmethod
    def from_record(cls, r: ClientRecord) -> "ClientWithKey":
        return cls(**ClientResponse.from_record(r).model_dump(), client_key=r.client_key)


# ---------------------------------------------------------------------------
# Emparejamiento (#317)
# ---------------------------------------------------------------------------


class PairingCreate(ClientCreate):
    """Plantilla del cliente que creará el código: los mismos campos que `ClientCreate`, con el
    nombre opcional (lo puede proponer el cliente) y sin clave (la genera el gateway)."""

    name: _Name | None = None  # type: ignore[assignment]
    client_key: None = None  # type: ignore[assignment]


class PairRequest(BaseModel):
    """Lo que envía el cliente a `POST /pair`. La persona solo teclea `code`; el resto lo pone el
    cliente y solo se usa si la plantilla del administrador no lo fija."""

    code: Annotated[str, StringConstraints(max_length=32)]
    name: _Name | None = None
    client_type: ClientType | None = None
    output_mode: list[OutputMode] | None = None


# ---------------------------------------------------------------------------
# Conversaciones (M19)
# ---------------------------------------------------------------------------

# Parámetros de las rutas (FastAPI valida Path/Query, no un Field suelto).
ConversationIdPath = Annotated[str, Path(min_length=1, max_length=200)]
ClientIdFilter = Annotated[str | None, Query(min_length=1, max_length=200)]
# Mensajes de `GET /admin/conversations/{id}/messages` (el tope de `chat.history` es 1000).
HistoryLimit = Annotated[int, Query(ge=1, le=1000)]


class ConversationCreate(BaseModel):
    """`POST /admin/conversations`: una conversación nueva del panel web. Sin `agent`, el de la
    cascada (`default_agent` del panel, `DEFAULT_AGENT`, el de OpenClaw)."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentName | None = None


class ConversationResponse(BaseModel):
    """Una conversación: sin texto (el título lo pone OpenClaw; null si no se sabe)."""

    id: str
    client_id: str
    client_name: str
    agent: str
    kind: str
    day: date | None
    created_at: datetime
    last_turn_at: datetime | None
    turn_count: int
    title: str | None = None


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    timestamp: datetime | None


class ConversationMessages(BaseModel):
    conversation_id: str
    messages: list[ConversationMessage]
    has_more: bool
