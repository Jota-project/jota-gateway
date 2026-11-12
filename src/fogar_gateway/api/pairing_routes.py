"""pairing_routes.py
~~~~~~~~~~~~~~~~~~~
Descubrimiento y emparejamiento de clientes (#317).

Público, sin autenticación:
  GET    /.well-known/fogar-gateway  — «soy un Fogar»: servicio, versión y nombre
  POST   /pair                      — el cliente canjea un código y recibe su client_key (una sola vez)

Administración (X-Admin-Token):
  POST   /admin/pairing             — abre un código con la plantilla del cliente
  GET    /admin/pairing             — códigos abiertos (sin el código)
  GET    /admin/pairing/{id}        — estado de uno (open, used, expired, cancelled)
  DELETE /admin/pairing/{id}        — lo anula

/pair va fuera de /v1 a propósito: /v1 es la API compatible con OpenAI y tiene su propia
autenticación. Sin aprobación automática por loopback: un proxy en el mismo host haría pasar
internet por 127.0.0.1. Contra quien intente adivinar basta la quema a los MAX_FAILED fallos (un
límite por IP no frena nada antes que ella y, tras el túnel, todo internet comparte IP). El
`/.well-known` no dice si hay códigos abiertos: sería el aviso para quemarlos. Ni el código ni la
clave van a los logs.
"""

import logging
import secrets
import socket
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session

from fogar_gateway._version import __version__
from fogar_gateway.api.admin_routes import _check_agent_consistency, create_client_record
from fogar_gateway.api.deps import get_admin_auth
from fogar_gateway.core.config import settings
from fogar_gateway.core.logging import fingerprint_key
from fogar_gateway.core.network import resolve_client_ip
from fogar_gateway.db.database import get_db_session
from fogar_gateway.db.models import ClientRecord
from fogar_gateway.models.admin_schemas import ClientCreate, PairingCreate, PairRequest
from fogar_gateway.services.pairing import InvalidCode, Pairing, PairingFull, PairingStore

logger = logging.getLogger(__name__)

public_router = APIRouter()
admin_router = APIRouter(prefix="/admin/pairing", dependencies=[Depends(get_admin_auth)])


def gateway_name() -> str:
    return settings.GATEWAY_NAME or socket.gethostname()


def _store(request: Request) -> PairingStore:
    store: PairingStore = request.app.state.pairing
    return store


# ---------------------------------------------------------------------------
# Público
# ---------------------------------------------------------------------------


@public_router.get("/.well-known/fogar-gateway")
def well_known() -> dict[str, Any]:
    return {
        "service": "fogar-gateway",
        "version": __version__,
        "name": gateway_name(),
    }


@public_router.post("/pair", status_code=status.HTTP_201_CREATED)
def pair(
    body: PairRequest,
    request: Request,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
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
            resolve_client_ip(request),
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
        # Sin los null: no deben pisar lo que proponga el cliente (ni dejarlo sin nombre).
        template = body.model_dump(exclude_unset=True, exclude_none=True)
        item = _store(request).open(template)
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
