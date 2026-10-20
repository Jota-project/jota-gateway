from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # Base de datos interna
    DATABASE_URL: str = "sqlite:///data/gateway.db"

    # Un solo proceso por base de datos (M6f, #218): flock sobre <dir de la BD>/gateway.lock.
    # Estado en memoria (sesiones, cachés, registro de bridges) no se comparte entre workers.
    SINGLE_PROCESS_LOCK: bool = True

    # Revocación de sesiones vivas (M6f, G-06): cada sesión se revalida contra la BD cada
    # SESSION_REVALIDATE_INTERVAL_S; la caché de db_client vive SESSION_CACHE_TTL_S (antes 60).
    SESSION_REVALIDATE_INTERVAL_S: float = Field(default=10.0, gt=0)
    SESSION_CACHE_TTL_S: float = Field(default=10.0, gt=0)

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

    # Proveedores de voz (src/services/speech/). El valor de arranque se puede cambiar en
    # caliente con PUT /admin/speech (no se persiste: al reiniciar rigen estos).
    STT_PROVIDER: Literal["jota", "elevenlabs"] = "jota"
    TTS_PROVIDER: Literal["jota", "elevenlabs"] = "jota"
    ELEVENLABS_API_KEY: SecretStr | None = None
    ELEVENLABS_BASE_URL: str = "wss://api.elevenlabs.io"
    ELEVENLABS_HEALTH_TTL_S: float = Field(default=60.0, gt=0)
    ELEVENLABS_STT_COMMIT: Literal["vad", "manual"] = "vad"
    ELEVENLABS_STT_VAD_SILENCE_S: float = Field(default=1.0, gt=0)
    ELEVENLABS_STT_IDLE_CLOSE_S: float = Field(default=5.0, gt=0)
    ELEVENLABS_STT_DRAIN_S: float = Field(default=1.5, ge=0)
    ELEVENLABS_STT_CONNECT_BUFFER_S: float = Field(default=2.0, ge=0)
    ELEVENLABS_STT_WARM_HOLD_S: float = Field(default=0.0, ge=0)
    ELEVENLABS_STT_MAX_SESSIONS: int = Field(default=15, gt=0)
    ELEVENLABS_STT_ENABLE_LOGGING: bool = False
    ELEVENLABS_TTS_MODEL: str = "eleven_flash_v2_5"
    ELEVENLABS_TTS_DEFAULT_VOICE: str | None = None
    ELEVENLABS_TTS_CHUNK_SCHEDULE: list[int] = [120, 160, 250, 290]
    ELEVENLABS_TTS_MAX_STREAMS: int = Field(default=10, gt=0)
    ELEVENLABS_TTS_MAX_BUFFER_BYTES: int = Field(default=10485760, gt=0)
    # voz configurada en el cliente (p. ej. "af_heart") -> id de voz del proveedor (JSON)
    TTS_VOICE_MAP: dict[str, str] = {}
    # Puerta de audio hacia el STT: "auto" = energía para proveedores de pago, off para jota.
    STT_AUDIO_GATE: Literal["auto", "off", "energy"] = "auto"
    STT_GATE_RMS_DBFS: float = -45.0
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
    TTS_AUTH_TIMEOUT_S: float = 10.0
    TRANSCRIBER_READY_TIMEOUT_S: float = 10.0

    # Deadlines acotados (issue #115)
    # 5 s (antes 10, #216): un handshake es un único mensaje pequeño; el margen largo solo
    # sirve a quien conecta y se queda callado, reteniendo la conexión sin autenticar.
    HANDSHAKE_TIMEOUT_S: float = 5.0
    # Tope (caracteres) del primer mensaje, comprobado antes de parsear/autenticar (#216).
    HANDSHAKE_MAX_BYTES: int = Field(default=4096, gt=0)
    TURN_TIMEOUT_S: float = 120.0
    IDLE_TIMEOUT_S: float = 300.0
    SHUTDOWN_DRAIN_S: float = 30.0

    # Drenado del audio TTS de un push al cerrar su turno (issue #130 S3a)
    PUSH_TTS_DRAIN_TIMEOUT_S: float = 30.0

    # Un envío al cliente bloqueado más de esto lo marca como lento y cierra la
    # conexión (issue #130 S2). Es por envío, no acumulado.
    CLIENT_SEND_TIMEOUT_S: float = Field(default=10.0, gt=0)

    # Eventos máximos en la cola de un turno de OpenClaw (issue #130 S3b). Si el
    # consumidor se atrasa y se llena, el turno se aborta (chat.abort + TURN_ERROR
    # "turn_overflow") en vez de bloquear _listen y a las demás sesiones.
    TURN_QUEUE_MAX: int = Field(default=1000, gt=0)


_PAID_STT_PROVIDERS = frozenset({"elevenlabs"})


def effective_audio_gate(settings: Settings, provider_name: str) -> Literal["off", "energy"]:
    """Resuelve `STT_AUDIO_GATE=auto`: energía para proveedores de pago, off para jota."""
    if settings.STT_AUDIO_GATE == "auto":
        return "energy" if provider_name in _PAID_STT_PROVIDERS else "off"
    return settings.STT_AUDIO_GATE


settings = Settings()
