from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # jota-db (fuente de verdad de identidad y configuración)
    # Solo host[:puerto] — el código inyecta http:// en DbClient
    JOTA_DB_BASE_URL: str = "localhost:8001"
    JOTA_DB_API_KEY: str = ""

    # URL base del JotaOrchestrator
    # Solo host[:puerto] — el código inyecta http:// en OrchestratorClient
    ORCHESTRATOR_BASE_URL: str = "localhost:8000"
    GATEWAY_KEY: str = ""

    # Transcriber (jota-transcriber)
    # Solo host[:puerto] — el código inyecta ws:// o http:// según el uso
    TRANSCRIBER_WS_URL: str = "localhost:9000"

    # TTS (jota-speaker)
    # Solo host[:puerto] — el código inyecta ws:// y path /ws en TTSClient
    TTS_WS_URL: str = "localhost:8005"
    TTS_TOKEN: str = "gateway"

    BARGE_IN_MIN_CHARS: int = 5
    TRANSCRIBER_SILENCE_TIMEOUT_S: int = 25

    class Config:
        env_file = ".env"

settings = Settings()
