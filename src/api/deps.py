"""
deps.py
~~~~~~~
FastAPI dependencies compartidas.
"""

import secrets

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
    # G-30 (#216): tiempo constante. Bytes porque compare_digest(str, str) lanza
    # TypeError con caracteres no ASCII (eso sería un 500 en vez de un 401).
    if not secrets.compare_digest(
        x_admin_token.encode("utf-8"), settings.ADMIN_TOKEN.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid admin token")
