"""
db_client.py
~~~~~~~~~~~~
Cliente HTTP hacia jota-db. Singleton compartido por todas las sesiones.

Responsabilidades:
  · Handshake WS: get_session(client_key) → (Client, ClientConfig)
  · REST API (Fase 2): config, conversaciones, mensajes
"""
import logging
from typing import Optional
import httpx

from src.models.schemas import Client, ClientConfig, SessionResponse

logger = logging.getLogger(__name__)


class DbClient:
    """
    Wraps llamadas HTTP a jota-db.

    Se instancia como singleton en este módulo y se reutiliza entre sesiones.
    Llamar a connect() en el startup de la app y close() en el shutdown.
    """

    def __init__(self, base_url: str, api_key: str):
        self.base_url = f"http://{base_url.rstrip('/')}"
        self._api_key = api_key
        self._http: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(10.0),
        )
        logger.info(f"DbClient listo → {self.base_url}")

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Identidad (usado en el handshake WS)
    # ------------------------------------------------------------------

    async def get_session(self, client_key: str) -> tuple[Client, ClientConfig]:
        """
        Llama a GET /auth/session en jota-db para resolver client_key → Client + ClientConfig.

        Raises:
            httpx.HTTPStatusError: 401/403 si la key no es válida o el cliente está inactivo.
            httpx.RequestError:    Si jota-db no está disponible.
        """
        assert self._http, "DbClient no está conectado. Llama a connect() primero."
        response = await self._http.get(
            f"{self.base_url}/auth/session",
            headers={"X-API-Key": client_key},
        )
        response.raise_for_status()
        data = SessionResponse.model_validate(response.json())
        return data.client, data.config

    # ------------------------------------------------------------------
    # Configuración (usado en la REST API — Fase 2)
    # ------------------------------------------------------------------

    async def get_config(self, client_id: str) -> ClientConfig:
        assert self._http
        r = await self._http.get(f"{self.base_url}/config/me", headers={"X-Client-Id": client_id})
        r.raise_for_status()
        return ClientConfig.model_validate(r.json())

    async def update_config(self, client_id: str, patch: dict) -> ClientConfig:
        assert self._http
        r = await self._http.put(
            f"{self.base_url}/config/me",
            json=patch,
            headers={"X-Client-Id": client_id},
        )
        r.raise_for_status()
        return ClientConfig.model_validate(r.json())

    async def reset_config(self, client_id: str) -> ClientConfig:
        assert self._http
        r = await self._http.post(
            f"{self.base_url}/config/me/reset",
            headers={"X-Client-Id": client_id},
        )
        r.raise_for_status()
        return ClientConfig.model_validate(r.json())

    # ------------------------------------------------------------------
    # Historial (usado en la REST API — Fase 2)
    # ------------------------------------------------------------------

    async def get_conversations(self, client_id: str) -> list:
        assert self._http
        r = await self._http.get(
            f"{self.base_url}/conversations",
            headers={"X-Client-Id": client_id},
        )
        r.raise_for_status()
        return r.json()

    async def get_messages(self, client_id: str, conversation_id: str) -> list:
        assert self._http
        r = await self._http.get(
            f"{self.base_url}/conversations/{conversation_id}/messages",
            headers={"X-Client-Id": client_id},
        )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Modelos (usado en la REST API — Fase 2)
    # ------------------------------------------------------------------

    async def get_models(self) -> list:
        assert self._http
        r = await self._http.get(f"{self.base_url}/models")
        r.raise_for_status()
        return r.json()


# Singleton — importar este objeto directamente
from src.core.config import settings

db_client = DbClient(
    base_url=settings.JOTA_DB_BASE_URL,
    api_key=settings.JOTA_DB_API_KEY,
)
