from pydantic import BaseModel, ConfigDict
from typing import List, Literal, Optional, Any

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
    token: str = "pene"
    publish_mqtt: bool = False
    vad_thold: float = 0.0
    
class TranscriberMessage(BaseModel):
    """
    Formato de salida proveniente del Transcriptor C++.
    """
    type: str # "transcription", "error", "warning", o "ready"
    text: Optional[str] = None
    is_final: Optional[bool] = None
    message: Optional[str] = None # Para errors

# =====================================================================
# Orchestrator (Gateway <-> JotaOrchestrator)
# =====================================================================

class OrchestratorControlMessage(BaseModel):
    """
    Mensaje de control mid-session hacia el JotaOrchestrator.
    (Para enviar texto llano, se manda el String suelto sin este envelope).
    """
    type: str
    model_id: Optional[str] = None

class OrchestratorResponse(BaseModel):
    """
    Formato general de los paquetes devueltos por Orchestrator.
    Puede ser "token" o eventos estructurales (estado, error, tool_usage).
    """
    type: str
    content: Optional[str] = None
    message: Optional[str] = None 
    
    model_config = ConfigDict(extra="allow")
