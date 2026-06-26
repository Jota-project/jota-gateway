from pydantic import BaseModel, ConfigDict
from typing import List, Literal, Optional

# =====================================================================
# jota-db — modelos que el gateway recibe de GET /auth/session
# =====================================================================

class Client(BaseModel):
    """Registro de cliente tal como lo devuelve jota-db."""
    id: str
    client_key: str
    is_active: bool

    model_config = ConfigDict(extra="allow")


class ClientConfig(BaseModel):
    """Configuración por cliente tal como la devuelve jota-db."""
    stt_language: str = "es"
    stt_vad_thold: float = 0.0
    tts_voice: str = "af_heart"
    tts_speed: float = 1.0
    preferred_model_id: Optional[str] = None
    system_prompt_extra: Optional[str] = None
    barge_in_enabled: bool = True
    barge_in_min_chars: int = 5

    model_config = ConfigDict(extra="allow")


class SessionResponse(BaseModel):
    """Respuesta de GET /auth/session en jota-db."""
    client: Client
    config: ClientConfig

# =====================================================================
# Gateway (Client <-> Gateway)
# =====================================================================

class Handshake(BaseModel):
    """
    Configuración inicial que el ESP32 o la Web envían al Gateway al conectar
    por WebSocket. Define qué patas internas del ecosistema se deben encender.
    """
    client_key: str
    input_mode: Literal["audio", "text"]
    output_mode: List[Literal["audio", "text", "status"]]
    agent: Optional[str] = None  # OpenClaw agent name; None → gateway default

    model_config = ConfigDict(extra="allow")

# =====================================================================
# Transcriber (Gateway <-> JotaTranscriber)
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
    Tipos posibles: "transcription", "ready", "warning", "error".
    """
    type: str
    text: Optional[str] = None
    is_final: Optional[bool] = None
    message: Optional[str] = None   # descripción legible en warning/error
    code: Optional[str] = None      # código de error/warning: AUTH_FAILED, buffer_full, etc.
    session_id: Optional[str] = None  # presente en el mensaje "ready"

