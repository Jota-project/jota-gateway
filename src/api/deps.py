"""
deps.py
~~~~~~~
FastAPI dependencies compartidas por todos los routers de la REST API.
"""
import httpx
from fastapi import Header, HTTPException

from src.core.config import settings


def handle_db_error(e: Exception) -> None:
    if isinstance(e, httpx.HTTPStatusError):
        raise HTTPException(status_code=e.response.status_code)
    if isinstance(e, httpx.RequestError):
        raise HTTPException(status_code=503, detail="jota-db unavailable")
    raise HTTPException(status_code=502, detail="Unexpected error")


async def get_admin_auth(x_admin_token: str = Header(...)) -> None:
    """Validates X-Admin-Token against ADMIN_TOKEN env var.

    Returns 503 if ADMIN_TOKEN is not configured (prevents accidental exposure).
    Returns 401 if token does not match.
    """
    if not settings.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin API not configured")
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")
