"""Códigos de cierre del WebSocket `/ws/stream` (docs/client-protocol.md §13, «Códigos de cierre»).

Incompatibilidad de protocolo — M19 (3.0.0): el código es el contrato; el `reason` es un texto
corto en inglés para logs y personas, y el cliente no debe compararlo. Todo cierre que manda el
gateway sale de esta tabla y pasa por `send_close` (`tests/unit/test_close_codes.py` lo vigila):
ningún sitio de cierre lleva un número ni un texto propio, ni llama a `close()` por su cuenta.

- Estándar (1000–1015) donde el significado es de transporte: fin normal, fallo interno, cliente
  lento. 1006 (sin close frame), 1009 (frame > 1 MiB, lo cierra uvicorn) y 1012 (reinicio, lo
  cierra uvicorn al apagarse) no los manda este código, pero el cliente los ve.
- 4000–4999, de aplicación. La regla del cliente va por rango: 4000–4899 no se reconecta solo
  (40xx handshake y credencial, 41xx conversación, 42xx sesión); 4900–4999 se reconecta con
  espera (49xx, fallos pasajeros de aplicación). Un código nuevo cae en su rango y un cliente que
  no lo conozca hace ya lo correcto.

`reconnect` dice qué debe hacer el cliente: `backoff` (reconectar solo, con espera creciente),
`on_input` (reabrir cuando haga falta, p. ej. al escribir), `never_auto` (solo si lo pide la
persona; reconectar solo echaría a otra sesión) o `fix` (no reintentar igual: hay que cambiar
algo, la clave o el handshake). La copia del panel web (`web/src/lib/closeCodes.ts`) la compara
un test con esta tabla.
"""

from typing import Any, Literal, NamedTuple

Reconnect = Literal["backoff", "on_input", "never_auto", "fix"]

# RFC 6455 §5.5: el cuerpo de un control frame cabe en 125 bytes, 2 del código.
MAX_REASON_BYTES = 123
# Códigos de aplicación que se reintentan con espera; el resto de 4xxx no se reconecta solo.
RETRYABLE_APPLICATION_CODES = range(4900, 5000)
# Dónde se anota, en `scope["state"]` del WebSocket, el cierre ya enviado.
_SENT_KEY = "fogar_close_sent"


class CloseFrame(NamedTuple):
    code: int
    reason: str
    reconnect: Reconnect


class Close:
    """La tabla. Se manda con `await send_close(websocket, Close.X)`."""

    # --- Estándar ---
    # Fin normal de la sesión: inactividad (IDLE_TIMEOUT_S), silencio del transcriptor.
    NORMAL = CloseFrame(1000, "session ended", "on_input")
    # Fallo interno al montar la sesión (servicios internos, conversación).
    SETUP_FAILED = CloseFrame(1011, "session setup failed", "backoff")
    # El orquestador (OpenClaw) no responde al health check: el único servicio fatal.
    ORCHESTRATOR_UNAVAILABLE = CloseFrame(1011, "orchestrator unavailable", "backoff")
    # La sesión termina por un fallo interno (antes o después del `ready`).
    INTERNAL_ERROR = CloseFrame(1011, "internal error", "backoff")
    # El cliente no lee al ritmo al que el gateway escribe (CLIENT_SEND_TIMEOUT_S).
    CLIENT_TOO_SLOW = CloseFrame(1013, "client too slow", "backoff")

    # --- 40xx: handshake y credencial ---
    HANDSHAKE_INVALID = CloseFrame(4000, "invalid handshake", "fix")
    HANDSHAKE_NOT_TEXT = CloseFrame(4001, "handshake must be a text frame", "fix")
    HANDSHAKE_TOO_LARGE = CloseFrame(4002, "handshake too large", "fix")
    INVALID_CREDENTIAL = CloseFrame(4010, "invalid or inactive credential", "fix")
    AGENT_NOT_PERMITTED = CloseFrame(4020, "agent not permitted", "fix")
    AGENT_NOT_AVAILABLE = CloseFrame(4021, "agent not available", "fix")

    # --- 41xx: conversación ---
    CONVERSATION_NOT_FOUND = CloseFrame(4100, "conversation not found", "fix")
    CONVERSATION_AGENT_MISMATCH = CloseFrame(4101, "conversation agent mismatch", "fix")
    CONVERSATION_OPENED_ELSEWHERE = CloseFrame(4102, "conversation opened elsewhere", "never_auto")

    # --- 42xx: sesión ---
    # El cliente se borró, se desactivó o se rotó su clave con la sesión abierta.
    REVOKED = CloseFrame(4200, "credential revoked", "fix")

    # --- 49xx: pasajeros, se reintentan con espera ---
    HANDSHAKE_TIMEOUT = CloseFrame(4900, "handshake timeout", "backoff")


ALL_CLOSES: dict[str, CloseFrame] = {
    name: value for name, value in vars(Close).items() if isinstance(value, CloseFrame)
}


def _state(websocket: Any) -> dict | None:
    scope = getattr(websocket, "scope", None)
    return scope.setdefault("state", {}) if isinstance(scope, dict) else None


def close_sent(websocket: Any) -> CloseFrame | None:
    """El cierre que ya se mandó (o se está mandando) a este WebSocket, si hay uno."""
    state = _state(websocket)
    return state.get(_SENT_KEY) if state is not None else None


async def send_close(websocket: Any, frame: CloseFrame) -> bool:
    """El único sitio que cierra un WebSocket de cliente: código y texto de la tabla.

    Nunca dos cierres por WebSocket: el primero se anota antes de enviarlo (otra tarea que llegue
    mientras tanto ya lo ve) y los siguientes no hacen nada. `False` si ya había uno.
    """
    state = _state(websocket)
    if state is not None:
        if state.get(_SENT_KEY) is not None:
            return False
        state[_SENT_KEY] = frame
    await websocket.close(code=frame.code, reason=frame.reason)
    return True
