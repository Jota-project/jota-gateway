"""
deps.py
~~~~~~~
FastAPI dependencies compartidas por todos los routers de la REST API.
"""
import httpx
from fastapi import Header, HTTPException

from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client


async def get_verified_client(
    x_api_key: str = Header(...),
) -> tuple[Client, ClientConfig]:
    """
    Resuelve X-API-Key → (Client, ClientConfig) llamando a jota-db.

    Raises:
        HTTPException 401: key inválida o cliente inactivo.
        HTTPException 503: jota-db no está disponible.
        HTTPException 502: error inesperado.
    """
    try:
        return await db_client.get_session(x_api_key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")
        raise HTTPException(status_code=502, detail="Unexpected error from jota-db")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    except Exception:
        raise HTTPException(status_code=502, detail="Unexpected error")
