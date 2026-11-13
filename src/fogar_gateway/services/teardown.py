"""Contrato único de cierre y cancelación (#221).

Política (decisión «Política de errores y cancelación» del vault):
- Una `CancelledError` ajena nunca se traga: el cierre termina todos sus pasos y la
  relanza al final.
- La cancelación que provocamos nosotros (cancelar una tarea hija y esperarla) es la
  esperada y no se propaga.
- Un fallo al limpiar nunca es silencioso: va al log en DEBUG con su tipo, nunca con
  el texto de la excepción (podría llevar una `client_key` o texto de usuario).

`asyncio.current_task().cancelling()` distingue las dos cancelaciones. No se llama a
`uncancel()`: `asyncio.timeout` y `TaskGroup` dependen de ese contador.
"""

import asyncio
import logging
from collections.abc import Coroutine
from types import TracebackType
from typing import Any

from fogar_gateway.services.reconnection import describe_error, traceback_no_message

logger = logging.getLogger(__name__)


def cancel_requested() -> bool:
    """True si alguien ha pedido cancelar la tarea actual (cancelación ajena)."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def _retrieve(task: asyncio.Task[Any]) -> None:
    # Done-callback de una tarea abandonada por timeout: recoge su excepción para que
    # no salga «Task exception was never retrieved» con el texto completo.
    if not task.cancelled():
        task.exception()


class Teardown:
    """Ejecuta los pasos de un cierre hasta el final aunque cancelen a quien cierra.

    Uso::

        async with Teardown("[cliente] close_all") as td:
            await td.cancel(task, "turno activo", timeout=2.0)
            await td.step(client.close(), "cierre del transcriber", timeout=2.0)

    Cada paso corre en su propia tarea, así que una cancelación de quien cierra no lo
    corta a medias; se acota con `timeout` (con una cancelación pendiente, el tope es
    lo único que limita la espera). Al salir del bloque, si hubo una cancelación
    ajena, se relanza.
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self.cancelled = False
        # La CancelledError recibida, para relanzar la misma: anyio (Starlette) solo
        # absorbe la que lleva el mensaje de su cancel scope.
        self._cancel_exc: asyncio.CancelledError | None = None

    async def __aenter__(self) -> "Teardown":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # cancel_requested(): una cancelación que algún paso se tragó por su cuenta
        # sigue pendiente en el contador y tampoco se pierde.
        if exc_type is None and (self.cancelled or cancel_requested()):
            raise self._cancel_exc or asyncio.CancelledError()

    def raise_if_cancelled(self) -> None:
        """Relanza solo la cancelación recibida durante este `Teardown`, no una heredada.

        Para esperas fuera de un cierre (p. ej. el barge-in), donde una cancelación
        que otro se tragó antes no debe tumbar la tarea que espera.
        """
        if self.cancelled:
            raise self._cancel_exc or asyncio.CancelledError()

    def _note_cancel(self, what: str, exc: asyncio.CancelledError) -> None:
        if self._cancel_exc is None:
            self._cancel_exc = exc
        if not self.cancelled:
            logger.debug(
                "%s: cancelación externa durante %s; se termina el cierre y se relanza",
                self._label,
                what,
            )
        self.cancelled = True

    async def step(
        self, coro: Coroutine[Any, Any, object], what: str, timeout: float | None = None
    ) -> None:
        """Ejecuta `coro` como paso del cierre. Nunca lanza `Exception`."""
        task = asyncio.ensure_future(coro)
        await self.wait(task, what, timeout)
        if not task.done():
            # Abandonado por timeout: se cancela sin esperar (ya se esperó el tope).
            task.cancel()
            task.add_done_callback(_retrieve)

    async def cancel(
        self, task: asyncio.Task[Any] | None, what: str, timeout: float | None = None
    ) -> None:
        """Cancela `task` y espera a que termine. Nunca cancela a quien llama."""
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        await self.wait(task, what, timeout)

    async def wait(
        self, task: asyncio.Task[Any] | None, what: str, timeout: float | None = None
    ) -> None:
        """Espera a que `task` termine sin cancelarla, y registra cómo acabó."""
        if task is None or task is asyncio.current_task():
            return
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while not task.done():
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                break
            try:
                await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError as cancel:
                # asyncio.wait no propaga nada de la tarea: solo puede ser ajena.
                self._note_cancel(what, cancel)
        if not task.done():
            logger.warning("%s: %s no terminó en %ss", self._label, what, timeout)
            return
        if not task.cancelled() and (e := task.exception()) is not None:
            logger.debug("%s: %s falló: %s", self._label, what, describe_error(e))
            logger.debug("%s: %s: %s", self._label, what, traceback_no_message(e))
