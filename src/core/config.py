from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # Base de datos interna
    DATABASE_URL: str = "sqlite:///data/gateway.db"

    # Admin API
    ADMIN_TOKEN: str = ""

    # /v1/* trusted-origin auth (issue #52)
    TRUST_LOOPBACK: bool = True
    TRUSTED_NETWORKS: str = (
        ""  # CSV of CIDRs, e.g. "192.168.1.0/24". Empty = only loopback trusted.
    )
    TRUSTED_PROXIES: str = "127.0.0.1,::1"  # CSV of IPs/CIDRs allowed to set X-Real-IP

    # Servicios externos
    TRANSCRIBER_WS_URL: str = "localhost:9000"
    TTS_WS_URL: str = "localhost:8005"
    TTS_TOKEN: str = "gateway"
    OPENCLAW_HOST: str = "127.0.0.1"
    OPENCLAW_PORT: int = 18789
    OPENCLAW_TOKEN: str = ""
    ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF: float = 1.0
    ORCHESTRATOR_RECONNECT_MAX_BACKOFF: float = 60.0
    ORCHESTRATOR_RECONNECT_MAX_DURATION: float = 300.0
    TRANSCRIBER_RECONNECT_INITIAL_BACKOFF: float = 1.0
    TRANSCRIBER_RECONNECT_MAX_BACKOFF: float = 60.0
    TRANSCRIBER_RECONNECT_MAX_DURATION: float = 300.0
    TTS_RECONNECT_INITIAL_BACKOFF: float = 1.0
    TTS_RECONNECT_MAX_BACKOFF: float = 60.0


settings = Settings()
