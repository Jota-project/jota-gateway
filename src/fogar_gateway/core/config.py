import logging
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fogar_gateway import paths
from fogar_gateway.access import parse_public_urls

_logger = logging.getLogger(__name__)

# Keys that older installs still carry in config.env: accepted and ignored (with one WARNING)
# so an upgrade does not turn them into "Extra inputs are not permitted".
RETIRED_KEYS = frozenset({"ORCHESTRATOR_RECONNECT_MAX_DURATION"})  # #272: single endless loop


class Settings(BaseSettings):
    # Nunca el .env del directorio actual: <base>/config.env (o --home / FOGAR_GATEWAY_HOME).
    model_config = SettingsConfigDict(env_file=paths.config_file())

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        retired = sorted(k for k in data if isinstance(k, str) and k.upper() in RETIRED_KEYS)
        if retired:
            _logger.warning(
                "Config: %s is obsolete and ignored (remove it from config.env).", ", ".join(retired)
            )
            data = {k: v for k, v in data.items() if k not in retired}
        return data

    # Base de datos interna. Una ruta SQLite relativa cuelga del directorio base, también el
    # default: el engine, Alembic, el candado y `fogar-gw doctor` ven la misma ruta absoluta.
    DATABASE_URL: str = Field(default="sqlite:///data/gateway.db", validate_default=True)

    @field_validator("DATABASE_URL")
    @classmethod
    def _absolute_database_url(cls, v: str) -> str:
        return paths.absolute_database_url(v)

    # Host de escucha de `fogar-gw serve` (puerto 8004). Detrás de un proxy en el mismo host,
    # 127.0.0.1. Vacío = 0.0.0.0, como el `${GATEWAY_HOST:-0.0.0.0}` del CMD anterior.
    GATEWAY_HOST: str = "0.0.0.0"

    # Nombre con el que se anuncia en /.well-known/fogar-gateway (#317). Vacío = nombre del equipo;
    # en Docker conviene fijarlo, porque ahí el nombre del equipo suele ser el id del contenedor.
    GATEWAY_NAME: str = ""

    # URLs https propias del usuario (CSV, sin ruta) por las que se llega al gateway: excepción
    # a «host:port sin protocolo». Solo se valida; se guarda el texto original.
    PUBLIC_URLS: str = ""

    @field_validator("PUBLIC_URLS")
    @classmethod
    def _valid_public_urls(cls, v: str) -> str:
        parse_public_urls(v)
        return v

    # Un solo proceso por base de datos (M6f, #218): flock sobre <dir de la BD>/gateway.lock.
    # Estado en memoria (sesiones, cachés, registro de bridges) no se comparte entre workers.
    SINGLE_PROCESS_LOCK: bool = True

    # Revocación de sesiones vivas (M6f, G-06): cada sesión se revalida contra la BD cada
    # SESSION_REVALIDATE_INTERVAL_S (db_client no cachea: #265).
    SESSION_REVALIDATE_INTERVAL_S: float = Field(default=10.0, gt=0)

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
    # Token de servicio para el transcriber (su AUTH_TOKEN). Nunca la client_key (#218).
    TRANSCRIBER_TOKEN: str = ""
    TTS_WS_URL: str = "localhost:8005"
    # Vacío = el servicio TTS no usa clave (como TRANSCRIBER_TOKEN).
    TTS_TOKEN: str = ""

    # Proveedores de voz (src/fogar_gateway/services/speech/). Se eligen al arrancar; cambiarlos = reiniciar.
    # "off" = como con el servicio caído: STT off, solo entra texto; TTS off, respuestas en texto.
    STT_PROVIDER: Literal["fogar", "elevenlabs", "off"] = "fogar"
    TTS_PROVIDER: Literal["fogar", "elevenlabs", "off"] = "fogar"
    ELEVENLABS_API_KEY: SecretStr | None = None
    ELEVENLABS_BASE_URL: str = "wss://api.elevenlabs.io"
    # Resto de ajustes de ElevenLabs: constantes en src/fogar_gateway/services/speech/elevenlabs/ (#264).
    ELEVENLABS_TTS_MODEL: str = "eleven_flash_v2_5"
    ELEVENLABS_TTS_DEFAULT_VOICE: str | None = "gc61tTk93h3LBXPxew2V"  # voz en español (no es secreto)
    # voz configurada en el cliente (p. ej. "af_heart") -> id de voz del proveedor (JSON)
    TTS_VOICE_MAP: dict[str, str] = {}
    # Puerta de energía hacia el STT de ElevenLabs (solo se envía audio mientras se habla).
    # fogar nunca la usa.
    STT_AUDIO_GATE: Literal["off", "energy"] = "energy"

    # shortcut: "auto" (default until #264) == "energy"; drop once no .env in production carries it.
    @field_validator("STT_AUDIO_GATE", mode="before")
    @classmethod
    def _legacy_auto_gate(cls, v: object) -> object:
        return "energy" if v == "auto" else v

    STT_GATE_RMS_DBFS: float = -45.0
    OPENCLAW_HOST: str = "127.0.0.1"
    OPENCLAW_PORT: int = 18789
    # wss:// en vez de ws:// (OpenClaw de otra máquina tras TLS, p. ej. su `tailscale serve`).
    OPENCLAW_TLS: bool = False
    OPENCLAW_TOKEN: str = ""
    # Agente por defecto de la instalación (#245): entra en la cascada de resolve_agent() entre
    # el `default_agent` del cliente y el agente por defecto de OpenClaw. Vacío = el de OpenClaw.
    # Misma regla que el `default_agent` de un cliente (admin_schemas.AgentName): sin espacios
    # alrededor y hasta 200 caracteres; vacío = sin agente de instalación.
    DEFAULT_AGENT: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""

    # Hora local del host (0-23) a la que empieza el día de las conversaciones por defecto (M19):
    # una por cliente, agente y día. Un turno que empieza tras el corte pasa a la del día nuevo.
    CONVERSATION_DAY_START_HOUR: int = Field(default=5, ge=0, le=23)
    ORCHESTRATOR_RECONNECT_INITIAL_BACKOFF: float = 1.0
    ORCHESTRATOR_RECONNECT_MAX_BACKOFF: float = 60.0
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
    # Tope de cada paso del cierre de una sesión (cancelar el turno, el push, las tareas,
    # cerrar el transcriber y el tracker) y del drenado de su cola de salida (#221).
    SESSION_TEARDOWN_S: float = Field(default=5.0, gt=0)
    # Obsoleto e ignorado (#221): el cierre ya no espera al turno en curso. Se acepta para
    # que un .env antiguo no impida arrancar (avisa con un WARNING al arrancar).
    SHUTDOWN_DRAIN_S: float | None = None
    LOG_FORMAT: Literal["text", "json"] = "text"

    # Drenado del audio TTS de un push al cerrar su turno (issue #130 S3a): tope de tiempo
    # sin progreso del TTS (se reinicia con cada trozo), no duración total
    PUSH_TTS_DRAIN_TIMEOUT_S: float = 30.0

    # Pacing del audio TTS (7e): el gateway envía a ritmo de reproducción, como mucho
    # AUDIO_LEAD_S por delante
    AUDIO_PACING_ENABLED: bool = True
    AUDIO_LEAD_S: float = Field(default=2.0, gt=0)

    # Un envío al cliente bloqueado más de esto lo marca como lento y cierra la
    # conexión (issue #130 S2). Es por envío, no acumulado.
    CLIENT_SEND_TIMEOUT_S: float = Field(default=10.0, gt=0)

    # Eventos máximos en la cola de un turno de OpenClaw (issue #130 S3b). Si el
    # consumidor se atrasa y se llena, el turno se aborta (chat.abort + TURN_ERROR
    # "turn_overflow") en vez de bloquear _listen y a las demás sesiones.
    TURN_QUEUE_MAX: int = Field(default=1000, gt=0)


settings = Settings()
