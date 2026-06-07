from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    # jota-db (fuente de verdad de identidad y configuración)
    # Solo host[:puerto] — el código inyecta http:// en DbClient
    JOTA_DB_BASE_URL: str = "localhost:8001"
    JOTA_DB_API_KEY: str = ""

    # URL base del JotaOrchestrator (legacy — kept for reference)
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

    # Orchestrator selection
    DEFAULT_ORCHESTRATOR: str = "openclaw"

    # OpenClaw orchestrator
    OPENCLAW_PORT: int = 18789
    OPENCLAW_TOKEN: str = ""

    # Orchestrator reconnect policy
    ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF: float = 1.0   # seconds
    ORCHESTRATOR_RECONNECT_MAX_BACKOFF: float = 60.0      # seconds
    ORCHESTRATOR_RECONNECT_MAX_DURATION: float = 300.0    # seconds before entering DEGRADED

    BARGE_IN_MIN_CHARS: int = 5
    TRANSCRIBER_SILENCE_TIMEOUT_S: int = 25

settings = Settings()
