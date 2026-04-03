"""
models_routes.py
~~~~~~~~~~~~~~~~
GET /api/models — lista de modelos disponibles (proxy a jota-db)
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import get_verified_client
from src.models.schemas import Client, ClientConfig
from src.services.db_client import db_client

router = APIRouter()


@router.get("/models")
async def get_models(
    auth: tuple[Client, ClientConfig] = Depends(get_verified_client),
) -> list:
    try:
        return await db_client.get_models()
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code)
    except Exception:
        raise HTTPException(status_code=502, detail="Unexpected error")
