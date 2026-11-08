"""pairing_routes.py
~~~~~~~~~~~~~~~~~~~
Descubrimiento y emparejamiento de clientes (#317).

Público, sin autenticación:
  GET    /.well-known/jota-gateway  — «soy un Jota»: servicio, versión, nombre y si hay códigos abiertos
  POST   /pair                      — el cliente canjea un código y recibe su client_key (una sola vez)

Administración (X-Admin-Token):
  POST   /admin/pairing             — abre un código con la plantilla del cliente
  GET    /admin/pairing             — códigos abiertos (sin el código)
  GET    /admin/pairing/{id}        — estado de uno (open, used, expired, cancelled)
  DELETE /admin/pairing/{id}        — lo anula

/pair va fuera de /v1 a propósito: /v1 es la API compatible con OpenAI y tiene su propia
autenticación. Sin aprobación automática por loopback: un proxy en el mismo host haría pasar
internet por 127.0.0.1. Ni el código ni la clave van a los logs.
"""

import logging
import secrets
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session

from jota_gateway._version import __version__
from jota_gateway.api.admin_routes import _check_agent_consistency, create_client_record
from jota_gateway.api.deps import get_admin_auth
from jota_gateway.core.config import settings
from jota_gateway.core.logging import fingerprint_key
from jota_gateway.core.network import resolve_client_ip
from jota_gateway.db.database import get_db_session
from jota_gateway.db.models import ClientRecord
from jota_gateway.models.admin_schemas import ClientCreate, PairingCreate, PairRequest
from jota_gateway.services.pairing import InvalidCode, Pairing, PairingFull, PairingStore

logger = logging.getLogger(__name__)

PAIR_RATE_LIMIT = 10  # peticiones a /pair por IP y ventana
PAIR_RATE_WINDOW_S = 60.0

public_router = APIRouter()
admin_router = APIRouter(prefix="/admin/pairing", dependencies=[Depends(get_admin_auth)])


class RateLimiter:
    """Ventana deslizante por clave, en memoria (un solo proceso por BD)."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit, self._window, self._clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            # Olvida las claves sin actividad reciente: el dict no crece con cada IP que pasa.
            for k in [k for k, q in self._hits.items() if now - q[-1] >= self._window]:
                del self._hits[k]
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True


def gateway_name() -> str:
    return settings.GATEWAY_NAME or socket.gethostname()


def _store(request: Request) -> PairingStore:
    store: PairingStore = request.app.state.pairing
    return store


# ---------------------------------------------------------------------------
# Público
# ---------------------------------------------------------------------------


@public_router.get("/.well-known/jota-gateway")
def well_known(request: Request) -> dict[str, Any]:
    return {
        "service": "jota-gateway",
        "version": __version__,
        "name": gateway_name(),
        "pairing": _store(request).has_open(),
    }


@public_router.post("/pair", status_code=status.HTTP_201_CREATED)
def pair(
    body: PairRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    ip = resolve_client_ip(request)
    if not request.app.state.pair_limiter.allow(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many requests",
            headers={"Retry-After": str(int(PAIR_RATE_WINDOW_S))},
        )
    offered = body.model_dump(include={"name", "client_type", "output_mode"}, exclude_none=True)

    def create(template: dict[str, Any]) -> ClientRecord:
        fields = {**offered, **template}  # manda lo que fijó el administrador
        fields.setdefault("name", f"{fields.get('client_type') or 'cliente'}-{secrets.token_hex(2)}")
        return create_client_record(session, ClientCreate(**fields))

    try:
        record = _store(request).redeem(body.code, create)
    except InvalidCode as e:
        logger.warning(
            "Emparejamiento: código no válido desde %s%s",
            ip,
            "; anulados los códigos abiertos tras varios fallos" if e.burned else "",
        )
        raise HTTPException(status_code=400, detail="invalid or expired code") from None
    logger.info(
        "Emparejamiento: cliente %s creado (fp=%s)", record.id, fingerprint_key(record.client_key)
    )
    return {
        "client_id": record.id,
        "name": record.name,
        "client_key": record.client_key,
        "gateway": {"name": gateway_name(), "version": __version__},
    }


# ---------------------------------------------------------------------------
# Administración
# ---------------------------------------------------------------------------


def _summary(item: Pairing) -> dict[str, Any]:
    return {
        "id": item.id,
        "state": item.state,
        "expires_at": item.expires_at.isoformat(),
        "client_id": item.client_id,
        "client_name": item.client_name,
        "template": item.template,
    }


@admin_router.post("", status_code=status.HTTP_201_CREATED)
def open_pairing(body: PairingCreate, request: Request) -> dict[str, Any]:
    # Se valida aquí y no al canjear: un error tiene que salir en la CLI, no en la tele.
    _check_agent_consistency(body.default_agent, body.allowed_agents)
    try:
        item = _store(request).open(body.model_dump(exclude_unset=True, exclude={"client_key"}))
    except PairingFull:
        raise HTTPException(status_code=409, detail="too many open pairing codes") from None
    logger.info("Emparejamiento: código abierto (id=%s)", item.id)
    return {**_summary(item), "code": item.code}


@admin_router.get("")
def list_pairings(request: Request) -> list[dict[str, Any]]:
    return [_summary(p) for p in _store(request).list_open()]


@admin_router.get("/{pairing_id}")
def get_pairing(pairing_id: str, request: Request) -> dict[str, Any]:
    item = _store(request).get(pairing_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Pairing not found")
    return _summary(item)


@admin_router.delete("/{pairing_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_pairing(pairing_id: str, request: Request) -> Response:
    if not _store(request).cancel(pairing_id):
        raise HTTPException(status_code=404, detail="Pairing not found or not open")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
