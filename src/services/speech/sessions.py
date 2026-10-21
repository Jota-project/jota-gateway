"""Conteo de sesiones abiertas de un proveedor STT, para retirarlo sin romperlas."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


class SessionRefCount:
    """`opened()`/`closed()` por sesión; `retire(aclose_cb)` ejecuta `aclose_cb` una sola vez,
    al cerrarse la última sesión abierta (ya, si no hay ninguna)."""

    def __init__(self) -> None:
        self._open = 0
        self._retired = False
        self._cb: Callable[[], Awaitable[None]] | None = None
        self._fired = False

    def opened(self) -> None:
        self._open += 1

    async def closed(self) -> None:
        self._open -= 1
        if self._retired and self._open <= 0:
            await self._fire()

    async def retire(self, aclose_cb: Callable[[], Awaitable[None]]) -> None:
        self._retired = True
        self._cb = self._cb or aclose_cb
        if self._open <= 0:
            await self._fire()

    async def _fire(self) -> None:
        cb, self._cb = self._cb, None
        if cb is None or self._fired:
            return
        self._fired = True
        await cb()
