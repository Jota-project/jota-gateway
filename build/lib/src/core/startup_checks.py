"""Comprobaciones de arranque (M6f, #218): tokens por defecto y guard de un solo proceso.

Ninguna función de aquí escribe el VALOR de un token: solo el nombre del setting.
"""

import errno
import logging
from pathlib import Path
from typing import IO

from sqlalchemy.engine import make_url

try:  # fcntl no existe en Windows; el guard se omite ahí (el despliegue de referencia es Linux).
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Valores de ejemplo/por defecto habituales (comparados sin mayúsculas ni espacios).
_PLACEHOLDER_TOKENS = frozenset(
    {
        "changeme",
        "change-me",
        "change_me",
        "changeit",
        "replace-me",
        "replace_me",
        "your-token-here",
        "your_token_here",
        "secret",
        "password",
        "admin",
        "token",
        "test",
        "example",
        "default",
    }
)

LOCK_FILENAME = "gateway.lock"


class SingleProcessLockError(RuntimeError):
    """Otro proceso del gateway ya usa esta base de datos."""


class SpeechConfigError(ValueError):
    """Configuración de proveedores de voz inválida. El mensaje nombra el setting, nunca el valor."""

    def __init__(self, setting: str) -> None:
        super().__init__(f"{setting} inválido o ausente para el proveedor de voz elegido")
        self.setting = setting


def check_speech_settings(settings, *, stt_provider: str, tts_provider: str) -> None:
    """Valida los settings que exigen los proveedores elegidos (lanza SpeechConfigError)."""
    if "elevenlabs" in (stt_provider, tts_provider):
        key = settings.ELEVENLABS_API_KEY
        if key is None or not key.get_secret_value().strip():
            raise SpeechConfigError("ELEVENLABS_API_KEY")
    if tts_provider == "elevenlabs":
        voice = settings.ELEVENLABS_TTS_DEFAULT_VOICE
        if voice is None or not voice.strip():
            raise SpeechConfigError("ELEVENLABS_TTS_DEFAULT_VOICE")
        # v3/v4 no están soportados en el WebSocket stream-input.
        if settings.ELEVENLABS_TTS_MODEL.strip().lower().startswith(("eleven_v3", "eleven_v4")):
            raise SpeechConfigError("ELEVENLABS_TTS_MODEL")
        sched = settings.ELEVENLABS_TTS_CHUNK_SCHEDULE
        if not 1 <= len(sched) <= 4 or not all(50 <= n <= 500 for n in sched):
            raise SpeechConfigError("ELEVENLABS_TTS_CHUNK_SCHEDULE")


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in _PLACEHOLDER_TOKENS


def warn_insecure_tokens(settings) -> list[str]:
    """WARNING por cada token vacío o por defecto. Devuelve los nombres avisados."""
    flagged: list[str] = []
    if not settings.ADMIN_TOKEN or _is_placeholder(settings.ADMIN_TOKEN):
        flagged.append("ADMIN_TOKEN")
        logger.warning(
            "ADMIN_TOKEN está vacío o es un valor de ejemplo: /admin/* queda inutilizable "
            "(vacío) o protegido por un secreto adivinable. Define uno propio."
        )
    if not settings.OPENCLAW_TOKEN or _is_placeholder(settings.OPENCLAW_TOKEN):
        flagged.append("OPENCLAW_TOKEN")
        logger.warning(
            "OPENCLAW_TOKEN está vacío o es un valor de ejemplo: la conexión con OpenClaw "
            "probablemente fallará o usa un secreto adivinable."
        )
    if type(settings).model_fields["TTS_TOKEN"].default == settings.TTS_TOKEN:
        flagged.append("TTS_TOKEN")
        logger.warning(
            "TTS_TOKEN tiene el valor por defecto del repositorio: cámbialo y configura "
            "el mismo token en el servicio TTS."
        )
    return flagged


def _lock_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database).resolve().parent / LOCK_FILENAME


def acquire_single_process_lock(database_url: str) -> IO | None:
    """Toma `flock(LOCK_EX|LOCK_NB)` sobre `<dir de la BD>/gateway.lock`.

    Devuelve el fichero abierto (guardarlo mientras dure el proceso) o `None` si el guard no
    aplica (BD en memoria, URL no SQLite, sin fcntl). Lanza `SingleProcessLockError` si otro
    proceso —o esta misma instancia, vía otro open()— ya lo tiene.
    """
    path = _lock_path(database_url)
    if path is None or fcntl is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")  # noqa: SIM115 — vive hasta release_single_process_lock
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        handle.close()
        if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
            # ENOLCK, ENOTSUP... (p. ej. NFS/FUSE sin flock): no es «otro proceso».
            raise SingleProcessLockError(
                f"El sistema de ficheros de {path.parent} no soporta flock "
                f"({errno.errorcode.get(e.errno or 0, 'OSError')}): usa un volumen local "
                "o desactiva el guard con SINGLE_PROCESS_LOCK=false."
            ) from None
        raise SingleProcessLockError(
            f"Otro proceso del gateway usa esta base de datos (lock {path}); "
            "multi-worker no está soportado. Arranca un solo proceso, o desactiva el guard "
            "con SINGLE_PROCESS_LOCK=false bajo tu responsabilidad."
        ) from None
    return handle


def release_single_process_lock(handle: IO | None) -> None:
    if handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        handle.close()
