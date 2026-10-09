"""
db_client.py
~~~~~~~~~~~~
Cliente local SQLite. Reemplaza el cliente HTTP a jota-db.

Interfaz pública invariante:
  db_client.get_session(client_key) → (Client, ClientConfig)
  db_client.invalidate(client_key)  → None
"""

import json
import logging

from sqlmodel import Session, select

from src.core.cache import make_cache
from src.core.exceptions import ClientInactive, ClientNotFound
from src.db.database import get_engine
from src.db.models import ClientRecord
from src.models.schemas import Client, ClientConfig

logger = logging.getLogger(__name__)


def _parse_allowed_agents(raw: str | None) -> list[str] | None:
    """Parse the JSON-encoded `allowed_agents` field from ClientRecord.

    - None / "" / "null"  → None   (sin restricción)
    - "[]"                 → []     (deniega todo)
    - '["a","b"]'         → ["a", "b"]
    - malformed JSON       → raises ValueError (fail loud; never silently bypass)

    Returns None for the legacy "empty/null" inputs so that an admin who
    has not configured the field exercises the no-restriction path.
    """
    # The "null" string check below is load-bearing: json.loads("null")
    # returns Python None, which would slip past the isinstance check and
    # raise a confusing ValueError. Intercept it explicitly as "no
    # restriction" instead.
    if raw is None or raw == "" or raw == "null":
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError(f"allowed_agents must be a JSON list, got {type(parsed).__name__}")
    return [str(x) for x in parsed]


class DbClient:
    """
    Wrapper sobre la BD SQLite local.

    `engine` opcional para facilitar tests en memoria;
    en producción usa el engine global de src.db.database.
    """

    def __init__(self, engine=None):
        self._engine = engine
        self._session_cache, self._session_lock = make_cache(maxsize=500, ttl=60)
        # Global invalidation epoch (issue #163). One int instead of a per-key
        # dict: a per-key counter can't be purged safely (an in-flight read of a
        # deleted/rotated key could still repopulate the cache once its entry is
        # gone), and keeping every historical key grows without bound.
        self._epoch: int = 0

    def _get_engine(self):
        return self._engine if self._engine is not None else get_engine()

    async def get_session(self, client_key: str) -> tuple[Client, ClientConfig]:
        """
        Resuelve client_key → (Client, ClientConfig). Resultado cacheado 60 s.

        Usa una época global de invalidación para evitar repoblar el caché
        con un valor obsoleto: si invalidate() (de CUALQUIER key) corre en
        otro hilo mientras la consulta a BD está en vuelo, la época capturada
        antes de la consulta ya no coincide al terminar y el resultado NO se
        escribe en caché (el siguiente acceso volverá a consultar BD). Es más
        conservadora que un contador por key — a lo sumo cuesta una consulta
        extra — pero nunca deja un valor obsoleto y usa memoria O(1).

        Raises:
            ClientNotFound: la key no existe.
            ClientInactive: el cliente está desactivado.
        """
        with self._session_lock:
            if client_key in self._session_cache:
                return self._session_cache[client_key]
            epoch_before = self._epoch

        with Session(self._get_engine()) as session:
            record: ClientRecord | None = session.exec(
                select(ClientRecord).where(ClientRecord.client_key == client_key)
            ).first()

        if record is None:
            raise ClientNotFound(client_key)
        if not record.is_active:
            raise ClientInactive(client_key)

        client = Client(
            id=record.id,
            client_key=record.client_key,
            is_active=record.is_active,
            name=record.name,
        )
        config = ClientConfig(
            stt_language=record.stt_language,
            stt_vad_thold=record.stt_vad_thold,
            tts_voice=record.tts_voice,
            tts_speed=record.tts_speed,
            barge_in_enabled=record.barge_in_enabled,
            barge_in_min_chars=record.barge_in_min_chars,
            silence_timeout_s=record.silence_timeout_s,
            max_silence_turns=record.max_silence_turns,
            push_enabled=record.push_enabled,
            tool_calls_enabled=record.tool_calls_enabled,
            default_agent=record.default_agent,
            allowed_agents=_parse_allowed_agents(record.allowed_agents),
        )
        result = (client, config)
        with self._session_lock:
            if self._epoch == epoch_before:
                self._session_cache[client_key] = result
        return result

    def invalidate(self, client_key: str) -> None:
        """Elimina la entrada del caché y avanza la época global.

        Seguro de llamar desde cualquier hilo (p.ej. handlers `def`
        síncronos de admin_routes.py, ejecutados en el threadpool de
        Starlette, distinto del hilo del event loop). Llamar siempre
        DESPUÉS de session.commit() en el mismo call site.
        """
        with self._session_lock:
            self._session_cache.pop(client_key, None)
            self._epoch += 1


# Singleton — importar este objeto directamente
db_client = DbClient()
