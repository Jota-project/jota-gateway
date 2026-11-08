"""Códigos de emparejamiento (#317): el administrador abre uno con `jota-gw pair` y el cliente lo
canjea en `POST /pair` para darse de alta.

En memoria a propósito: duran 10 minutos y hay un solo proceso por BD (`SINGLE_PROCESS_LOCK`);
si el gateway se reinicia, se pide otro. El código nunca se registra en los logs.
"""

import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from jota_gateway.db.models import ClientRecord

CODE_TTL_S = 600
MAX_OPEN = 5
# Fallos seguidos (entre todos los clientes; un canje correcto pone la cuenta a 0) que anulan los códigos abiertos: con 6 cifras,
# 10 minutos y 5 intentos, adivinar uno no es viable aunque /pair esté expuesto a internet.
MAX_FAILED = 5

State = Literal["open", "used", "expired", "cancelled"]


class PairingFull(Exception):
    """Ya hay MAX_OPEN códigos abiertos."""


class InvalidCode(Exception):
    """Código desconocido, caducado, usado o mal escrito (el cliente no sabe cuál)."""

    def __init__(self, burned: bool = False) -> None:
        super().__init__("invalid or expired code")
        self.burned = burned  # este fallo anuló todos los códigos abiertos


@dataclass
class Pairing:
    id: str
    code: str
    template: dict[str, Any]
    expires_at: datetime
    deadline: float  # reloj monótono
    state: State = "open"
    client_id: str | None = None
    client_name: str | None = None
    closed_at: float | None = field(default=None, repr=False)


class PairingStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()  # /pair y /admin/pairing corren en el threadpool
        self._items: dict[str, Pairing] = {}
        self._failed = 0

    def open(self, template: dict[str, Any]) -> Pairing:
        with self._lock:
            self._sweep()
            open_codes = {p.code for p in self._items.values() if p.state == "open"}
            if len(open_codes) >= MAX_OPEN:
                raise PairingFull
            code = f"{secrets.randbelow(10**6):06d}"
            while code in open_codes:
                code = f"{secrets.randbelow(10**6):06d}"
            item = Pairing(
                id=str(uuid.uuid4()),
                code=code,
                template=dict(template),
                expires_at=datetime.now(UTC) + timedelta(seconds=CODE_TTL_S),
                deadline=self._clock() + CODE_TTL_S,
            )
            self._items[item.id] = item
            return item

    def get(self, pairing_id: str) -> Pairing | None:
        with self._lock:
            self._sweep()
            return self._items.get(pairing_id)

    def list_open(self) -> list[Pairing]:
        with self._lock:
            self._sweep()
            return [p for p in self._items.values() if p.state == "open"]

    def cancel(self, pairing_id: str) -> bool:
        with self._lock:
            self._sweep()
            item = self._items.get(pairing_id)
            if item is None or item.state != "open":
                return False
            self._close(item, "cancelled")
            return True

    def redeem(self, code: str, create: Callable[[dict[str, Any]], ClientRecord]) -> ClientRecord:
        """Canjea `code` llamando a `create(plantilla)` con el candado tomado, así que dos
        canjes del mismo código crean un solo cliente. Si `create` falla, el código sigue
        abierto y no cuenta como fallo: no es culpa de quien lo escribió."""
        code = "".join(code.split())  # «482 913» vale igual que «482913»
        with self._lock:
            self._sweep()
            match = None
            for item in self._items.values():
                # Contra todos los abiertos y en tiempo constante: no revela cuál se parece.
                if item.state == "open" and secrets.compare_digest(
                    item.code.encode(), code.encode("utf-8", "replace")
                ):
                    match = item
            if match is None:
                if not any(p.state == "open" for p in self._items.values()):
                    raise InvalidCode  # sin códigos abiertos no hay nada que adivinar
                self._failed += 1
                if self._failed < MAX_FAILED:
                    raise InvalidCode
                for item in self._items.values():
                    if item.state == "open":
                        self._close(item, "cancelled")
                self._failed = 0
                raise InvalidCode(burned=True)
            record = create(match.template)
            match.client_id, match.client_name = record.id, record.name
            self._close(match, "used")
            self._failed = 0  # «5 fallos seguidos»: un canje correcto vuelve a empezar
            return record

    def _close(self, item: Pairing, state: State) -> None:
        item.state = state
        item.closed_at = self._clock()

    def _sweep(self) -> None:
        """Caduca los vencidos y olvida los cerrados hace más de CODE_TTL_S (la CLI lee el
        estado final mientras espera). Sin códigos abiertos, los fallos vuelven a 0."""
        now = self._clock()
        for item in list(self._items.values()):
            if item.state == "open" and now >= item.deadline:
                self._close(item, "expired")
            if item.closed_at is not None and now - item.closed_at > CODE_TTL_S:
                del self._items[item.id]
        if not any(p.state == "open" for p in self._items.values()):
            self._failed = 0
