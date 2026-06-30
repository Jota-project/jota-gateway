"""
db_client.py
~~~~~~~~~~~~
Cliente local SQLite. Reemplaza el cliente HTTP a jota-db.

Interfaz pública invariante:
  db_client.get_session(client_key) → (Client, ClientConfig)
  db_client.invalidate(client_key)  → None
"""
import logging
from typing import Optional

from sqlmodel import Session, select

from src.core.cache import make_cache
from src.core.exceptions import ClientInactive, ClientNotFound
from src.db.database import get_engine
from src.db.models import ClientRecord
from src.models.schemas import Client, ClientConfig

logger = logging.getLogger(__name__)


class DbClient:
    """
    Wrapper sobre la BD SQLite local.

    `engine` opcional para facilitar tests en memoria;
    en producción usa el engine global de src.db.database.
    """

    def __init__(self, engine=None):
        self._engine = engine
        self._session_cache, self._session_lock = make_cache(maxsize=500, ttl=60)

    def _get_engine(self):
        return self._engine if self._engine is not None else get_engine()

    async def get_session(self, client_key: str) -> tuple[Client, ClientConfig]:
        """
        Resuelve client_key → (Client, ClientConfig). Resultado cacheado 60 s.

        Raises:
            ClientNotFound: la key no existe.
            ClientInactive: el cliente está desactivado.
        """
        async with self._session_lock:
            if client_key in self._session_cache:
                return self._session_cache[client_key]

        with Session(self._get_engine()) as session:
            record: Optional[ClientRecord] = session.exec(
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
            system_prompt_extra=record.system_prompt_extra,
            preferred_model_id=record.preferred_model_id,
        )
        result = (client, config)
        async with self._session_lock:
            self._session_cache[client_key] = result
        return result

    def invalidate(self, client_key: str) -> None:
        """Elimina la entrada del caché. Llamar tras cualquier mutación en admin."""
        self._session_cache.pop(client_key, None)


# Singleton — importar este objeto directamente
db_client = DbClient()
