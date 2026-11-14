"""Progreso de un turno para el cliente: el mensaje `turn_progress` (#254).

Mientras OpenClaw prepara la respuesta o usa herramientas, el cliente no recibe nada (con
2026.10.5 el texto llega entero en `final`, #241). Este módulo traduce lo que OpenClaw ya cuenta
a un vocabulario corto y estable que es del gateway, no de OpenClaw:

- `preparing`: OpenClaw prepara la ejecución (las fases de arranque de `chat` `state:"status"`).
- `thinking`: el modelo trabaja (`starting_model`, o vuelve a él tras una herramienta).
- `tool`: el agente usa una herramienta; solo su **nombre** (nunca argumentos ni resultado, que
  pueden llevar datos del usuario), y solo si es un identificador (`safe_tool_name`).

Por turno (`TurnProgress`): no se repite un estado igual al último enviado y sale como mucho
uno cada `PROGRESS_MIN_INTERVAL_S`; lo que llega antes se aplaza y sale solo el más reciente.
El envío, siempre entre `turn_start` y `turn_end`, es de `services/turns.py`.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fogar_gateway.services.openclaw.models import ToolCallEvent

logger = logging.getLogger(__name__)

ProgressPhase = Literal["preparing", "thinking", "tool"]

# Como mucho un `turn_progress` por turno cada tanto (OpenClaw puede encadenar herramientas).
PROGRESS_MIN_INTERVAL_S = 0.25

# Fases de `chat` `state:"status"` de OpenClaw 2026.10.5 (`ChatRunStartupPhaseSchema`;
# `retrying` llega como `starting_model` con `retry`) → las del gateway. Una fase que no está
# aquí es una fase de arranque nueva (el esquema solo admite fases de arranque): `preparing`.
_OPENCLAW_STATUS: dict[str, ProgressPhase] = {
    "waiting_for_state": "preparing",
    "preparing_workspace": "preparing",
    "naming_worktree": "preparing",
    "creating_worktree": "preparing",
    "running_setup": "preparing",
    "provisioning_environment": "preparing",
    "preparing_context": "preparing",
    "memory_flushing": "preparing",
    "starting_model": "thinking",
}
_UNKNOWN_STATUS: ProgressPhase = "preparing"

# Un nombre de herramienta es un identificador: los de los proveedores de modelos caben en
# `[A-Za-z0-9_-]{1,128}`; se admiten además `.` y `:`. Cualquier otra cosa no sale.
_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_PHASE_RE = re.compile(r"[a-z0-9_]{1,64}")


def safe_tool_name(name: object) -> str | None:
    """El nombre tal cual si es un identificador acotado; si no, None (sin nombre)."""
    return name if isinstance(name, str) and _TOOL_NAME_RE.fullmatch(name) else None


def chat_status_phase(payload: dict) -> ProgressPhase | None:
    """La fase del gateway de un `chat` `state:"status"` de OpenClaw; None si no es uno."""
    if payload.get("state") != "status":
        return None
    phase = payload.get("phase")
    mapped = _OPENCLAW_STATUS.get(phase) if isinstance(phase, str) else None
    if mapped is None:
        shown = phase if isinstance(phase, str) and _PHASE_RE.fullmatch(phase) else "<invalid>"
        logger.debug("OpenClaw chat status phase %s unknown: sent as %s", shown, _UNKNOWN_STATUS)
        return _UNKNOWN_STATUS
    return mapped


def _call_id(value: object) -> str | int | None:
    """El `toolCallId` como clave: solo texto o entero (un objeto o una lista de OpenClaw no
    sirven de clave y romperían el turno); cualquier otra cosa es «sin id»."""
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    return None


@dataclass(frozen=True)
class Progress:
    phase: ProgressPhase
    tool: str | None = None  # solo con `tool`; None si el nombre no es seguro

    def message(self, turn_id: str) -> dict:
        return {"type": "turn_progress", "turn_id": turn_id, "phase": self.phase, "tool": self.tool}


class TurnProgress:
    """El progreso de un turno: qué ve el cliente ahora, sin repetir y sin inundar. Sin E/S.

    `offer` devuelve el `Progress` que hay que enviar ya, los segundos que faltan para poder
    enviar (el que llame espera y llama a `flush`) o None (nada que enviar: igual al último).
    """

    def __init__(
        self,
        min_interval: float | None = None,  # None: PROGRESS_MIN_INTERVAL_S al crearlo
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min = PROGRESS_MIN_INTERVAL_S if min_interval is None else min_interval
        self._clock = clock
        self._open_tools: dict[str | int, str | None] = {}  # toolCallId → nombre, por inicio
        self._latest: Progress | None = None
        self._sent: Progress | None = None
        self._sent_at: float | None = None

    def tool(self, tool_call: ToolCallEvent) -> Progress:
        """El estado tras un evento de herramienta: la que empieza o, al terminar una, la última
        que sigue abierta (herramientas en paralelo); si no queda ninguna, `thinking`."""
        call_id = _call_id(tool_call.tool_call_id)
        if tool_call.phase == "start":
            name = safe_tool_name(tool_call.name)
            if call_id is not None:  # sin id no se puede emparejar con su result
                self._open_tools.pop(call_id, None)
                self._open_tools[call_id] = name
            return Progress("tool", name)
        if call_id is not None:
            self._open_tools.pop(call_id, None)
        if self._open_tools:
            return Progress("tool", next(reversed(self._open_tools.values())))
        return Progress("thinking")

    def text_arrived(self) -> None:
        """Salió texto del turno: para el cliente el progreso ya es viejo. Lo aplazado no sale
        (nunca un progreso viejo detrás de un `token`) y el siguiente estado sale aunque repita
        el último enviado (el cliente lo borró con el `token`)."""
        self._latest = None
        self._sent = None

    def offer(self, progress: Progress) -> Progress | float | None:
        self._latest = progress
        return self.flush()

    def flush(self) -> Progress | float | None:
        """Lo pendiente (el estado más reciente), con las mismas reglas que `offer`."""
        latest = self._latest
        if latest is None or latest == self._sent:
            return None
        now = self._clock()
        if self._sent_at is not None:
            wait = self._sent_at + self._min - now
            if wait > 0:
                return wait
        self._sent, self._sent_at = latest, now
        return latest
