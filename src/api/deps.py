"""
deps.py
~~~~~~~
FastAPI dependencies compartidas.
"""
from fastapi import Header, HTTPException

from src.core.config import settings


async def get_admin_auth(x_admin_token: str = Header(...)) -> None:
    """
    Valida X-Admin-Token contra ADMIN_TOKEN env var.
    503 si ADMIN_TOKEN no está configurado (previene exposición accidental).
    401 si el token no coincide.
    """
    if not settings.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin API not configured")
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")
