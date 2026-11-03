"""Cierre de la sesión por el gateway, visto desde el cliente (arnés e2e, #221 4/6).

Invariante (docs/client-protocol.md): todo `turn_start` tiene su `turn_end`, también cuando
es el gateway quien cierra la sesión con un turno abierto, mientras el socket siga abierto.
"""

import asyncio
import time

import websockets

from jota_gateway.core.config import settings
from tests.integration.harness.conftest import assert_turns_paired, of_type, turn_ended


async def _until_closed(client) -> websockets.ConnectionClosed:
    """Descarta lo que llegue hasta que el gateway cierre el socket; devuelve el cierre."""
    while True:
        try:
            await asyncio.wait_for(client.ws.recv(), timeout=10)
        except websockets.ConnectionClosed as closed:
            return closed


async def test_idle_close_with_an_open_push_turn_ends_that_turn(harness, monkeypatch):
    """El orquestador abre un push y no lo cierra; el cliente calla. El cierre por
    inactividad entrega el `turn_end` del push antes de cerrar el socket."""
    # Antes de conectar: el watchdog de inactividad lee el plazo al arrancar.
    monkeypatch.setattr(settings, "IDLE_TIMEOUT_S", 0.5)
    monkeypatch.setattr(settings, "TURN_TIMEOUT_S", 0.5)  # tope de un push huérfano
    client = await harness.connect()
    await harness.push("aviso a medias", end=False)

    msgs = await client.collect(turn_ended, timeout=10)
    assert_turns_paired(msgs)
    closed = await _until_closed(client)
    assert closed.rcvd is None or closed.rcvd.code == 1000


async def test_shutdown_with_a_turn_in_flight_closes_1012_without_hanging(harness):
    """Reinicio con un turno en vuelo: uvicorn cierra con 1012, el gateway cancela el
    turno y termina de apagarse enseguida (sin esperar al orquestador)."""
    client = await harness.connect()
    await client.send({"type": "send", "text": "wait for me"})
    await harness.openclaw.wait_held(1)
    await client.collect(lambda m: bool(of_type(m, "turn_start")))

    start = time.monotonic()
    harness.server.should_exit = True
    closed, _ = await asyncio.gather(
        _until_closed(client), asyncio.to_thread(harness.thread.join, 15)
    )

    assert not harness.thread.is_alive(), "el gateway no se apagó"
    assert time.monotonic() - start < settings.SESSION_TEARDOWN_S
    assert closed.rcvd is not None and closed.rcvd.code == 1012
