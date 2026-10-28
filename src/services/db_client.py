"""
db_client.py
~~~~~~~~~~~~
Cliente local SQLite. Reemplaza el cliente HTTP a jota-db.

Interfaz pública invariante:
  db_client.get_session(client_key) → (Client, ClientConfig)
"""

import asyncio
import json
import logging

from sqlmodel import Session, select

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
    Wrapper sobre la BD SQLite local. Sin caché (#265): cada llamada consulta la BD
    (un SELECT por índice), así que un cambio hecho desde la CLI u otro proceso se ve
    en la siguiente revalidación.

    `engine` opcional para facilitar tests en memoria;
    en producción usa el engine global de src.db.database.
    """

    def __init__(self, engine=None):
        self._engine = engine

    def _get_engine(self):
        return self._engine if self._engine is not None else get_engine()

    async def get_session(self, client_key: str) -> tuple[Client, ClientConfig]:
        """
        Resuelve client_key → (Client, ClientConfig). La E/S de SQLite corre en un
        hilo (#240): un `database is locked` no congela el event loop.

        Raises:
            ClientNotFound: la key no existe.
            ClientInactive: el cliente está desactivado.
        """
        return await asyncio.to_thread(self._lookup, client_key)

    def _lookup(self, client_key: str) -> tuple[Client, ClientConfig]:
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
        # `include` explícito: ClientConfig es extra="allow" y no debe recibir id/client_key.
        config = ClientConfig(
            **record.model_dump(include=_CONFIG_COLUMNS),
            allowed_agents=_parse_allowed_agents(record.allowed_agents),
        )
        return client, config


_CONFIG_COLUMNS = set(ClientConfig.model_fields) - {"allowed_agents"}

# Singleton — importar este objeto directamente
db_client = DbClient()
