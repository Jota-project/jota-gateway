from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    JOTA_DB_BASE_URL: str = "localhost:8001"
    JOTA_DB_API_KEY: str = ""
    ORCHESTRATOR_BASE_URL: str = "localhost:8000"
    GATEWAY_KEY: str = ""
    TRANSCRIBER_WS_URL: str = "localhost:9000"
    TTS_WS_URL: str = "localhost:8005"
    TTS_TOKEN: str = "gateway"
    DEFAULT_ORCHESTRATOR: str = "openclaw"
    OPENCLAW_PORT: int = 18789
    OPENCLAW_TOKEN: str = ""
    ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF: float = 1.0
    ORCHESTRATOR_RECONNECT_MAX_BACKOFF: float = 60.0
    ORCHESTRATOR_RECONNECT_MAX_DURATION: float = 300.0
    BARGE_IN_MIN_CHARS: int = 5
    TRANSCRIBER_SILENCE_TIMEOUT_S: int = 25
    # LLM bypass para /v1/chat/completions (evita OpenClaw)
    OPENAI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    LLM_MODEL: str = "google/gemini-2.5-flash-lite"

settings = Settings()
