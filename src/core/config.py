from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # URL base HTTP del JotaOrchestrator (sin trailing slash)
    # El cliente usará POST {ORCHESTRATOR_BASE_URL}/api/quick
    ORCHESTRATOR_BASE_URL: str = "http://localhost:8000"
    # Clave de cliente QUICK registrada en JotaDB para este gateway
    ORCHESTRATOR_API_KEY: str = "jota_internal_default_key"
    
    TRANSCRIBER_WS_URL: str = "ws://localhost:9000"
    TTS_WS_URL: str = "ws://localhost:8005/ws"
    TTS_TOKEN: str = "gateway"

    BARGE_IN_MIN_CHARS: int = 5
    TRANSCRIBER_SILENCE_TIMEOUT_S: int = 25

    class Config:
        env_file = ".env"

settings = Settings()
