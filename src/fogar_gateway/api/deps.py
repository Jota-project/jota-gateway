"""
deps.py
~~~~~~~
FastAPI dependencies compartidas.
"""

import secrets

from fastapi import Header, HTTPException

from fogar_gateway.core.config import settings


def admin_token_matches(given: str) -> bool:
    """`given` es el ADMIN_TOKEN: la única comprobación, para `/admin` (X-Admin-Token) y para el
    `admin_token` del handshake WS (M19). Sin ADMIN_TOKEN configurado, nunca."""
    if not settings.ADMIN_TOKEN:
        return False
    # G-30 (#216): tiempo constante. Bytes porque compare_digest(str, str) lanza
    # TypeError con caracteres no ASCII (eso sería un 500 en vez de un 401).
    return secrets.compare_digest(given.encode("utf-8"), settings.ADMIN_TOKEN.encode("utf-8"))


async def get_admin_auth(x_admin_token: str = Header(...)) -> None:
    """
    Valida X-Admin-Token contra ADMIN_TOKEN env var.
    503 si ADMIN_TOKEN no está configurado (previene exposición accidental).
    401 si el token no coincide.
    """
    if not settings.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin API not configured")
    if not admin_token_matches(x_admin_token):
        raise HTTPException(status_code=401, detail="Invalid admin token")
