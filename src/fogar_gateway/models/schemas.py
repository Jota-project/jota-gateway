from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

# Modos de salida conocidos. Compartido por el handshake WS y la API admin (#122).
OutputMode = Literal["audio", "text", "status"]

# =====================================================================
# Identidad y configuración del cliente (BD local, src/fogar_gateway/db)
# =====================================================================


class Client(BaseModel):
    """Registro de cliente resuelto desde la BD local."""

    id: str
    client_key: str
    is_active: bool
    name: str = ""

    model_config = ConfigDict(extra="allow")


class ClientConfig(BaseModel):
    """Configuración por cliente leída desde la BD local."""

    stt_language: str = "es"
    stt_vad_thold: float = 0.0
    tts_voice: str = "af_heart"
    tts_speed: float = 1.0
    barge_in_enabled: bool = True
    barge_in_min_chars: int = 5
    silence_timeout_s: float = 2.0
    max_silence_turns: int = 3
    push_enabled: bool = True
    tool_calls_enabled: bool = False
    default_agent: str | None = None
    allowed_agents: list[str] | None = None

    model_config = ConfigDict(extra="allow")


# =====================================================================
# Gateway (Client <-> Gateway)
# =====================================================================


class Handshake(BaseModel):
    """
    Configuración inicial que el cliente envía al Gateway al conectar por WebSocket. Define
    qué patas internas del ecosistema se deben encender.

    Credencial: `client_key` o, para el panel web, `admin_token` (M19); una de las dos.
    """

    client_key: str | None = None
    admin_token: str | None = None
    input_mode: Literal["audio", "text"]
    output_mode: list[OutputMode]
    agent: str | None = None  # OpenClaw agent name; None → gateway default
    # Id de una conversación del cliente o "new"; None → la conversación por defecto (M19).
    conversation: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _one_credential(self) -> "Handshake":
        if (self.client_key is None) == (self.admin_token is None):
            raise ValueError("client_key or admin_token, exactly one")
        return self


# =====================================================================
# Transcriber (Gateway <-> FogarTranscriber)
# =====================================================================


class TranscriberConfig(BaseModel):
    """
    Mensaje de handshake inicial requerido por el servidor de transcripción (C++)
    antes de aceptar recibir ráfagas de audio PCM float32.
    """

    type: Literal["config"] = "config"
    language: str = "es"
    token: str = ""
    vad_thold: float = 0.0


class TranscriberMessage(BaseModel):
    """
    Formato de salida proveniente del Transcriptor C++.
    Tipos posibles: "transcription", "ready", "warning", "error", "status".
    """

    type: str
    text: str | None = None
    is_final: bool | None = None
    message: str | None = None  # descripción legible en warning/error
    code: str | None = None  # código de error/warning: AUTH_FAILED, buffer_full, etc.
    session_id: str | None = None  # presente en el mensaje "ready"
    state: str | None = None  # "busy"/"ok" en mensajes type="status" (capacidad, #143)
    reason: str | None = None  # motivo en status busy / final con complete=false
    complete: bool | None = None  # False en un final truncado por saturación de GPU
