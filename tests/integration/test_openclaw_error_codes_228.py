"""Diagnóstico para el operador (#228, revisión): se registra el `code` corto de OpenClaw
solo si es un identificador seguro; nunca `message` ni el cuerpo. Servidores `websockets`
reales en puerto 0."""

import asyncio
import logging

import pytest

from tests.integration.test_openclaw_stable_errors_228 import (
    MARK,
    Server,
    _failed_connect,
    _res,
    make_client,
    run_turn,
)

EVIL_CODE = "BAD\nSECRET-INTERNAL " + "x" * 200


def _src_log(caplog):
    return "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("src"))


def _no_leak(log):
    assert MARK not in log and "BAD" not in log and "xxxxx" not in log


async def test_rejected_hello_logs_safe_code_but_never_message(caplog):
    hello = _res("x", ok=False, error={"code": "INVALID_TOKEN", "message": MARK})
    await _failed_connect({"hello": hello}, caplog)
    log = _src_log(caplog)
    assert "INVALID_TOKEN" in log
    _no_leak(log)


@pytest.mark.parametrize("code", [EVIL_CODE, "a b", "", 42, None, "x" * 49])
async def test_rejected_hello_with_unsafe_code_logs_unspecified(code, caplog):
    hello = _res("x", ok=False, error={"code": code, "message": MARK})
    await _failed_connect({"hello": hello}, caplog)
    log = _src_log(caplog)
    assert "unspecified" in log
    _no_leak(log)


@pytest.mark.parametrize("error", ["just text SECRET-INTERNAL", ["SECRET-INTERNAL"], None])
async def test_rejected_hello_with_non_object_error_logs_unspecified(error, caplog):
    hello = _res("x", ok=False, error=error)
    await _failed_connect({"hello": hello}, caplog)
    log = _src_log(caplog)
    assert "unspecified" in log
    _no_leak(log)


async def test_agents_list_failure_logs_safe_code_but_never_message(caplog):
    caplog.set_level(logging.DEBUG, logger="src")
    async with Server(agents_error={"code": "AGENTS_DOWN", "message": MARK}) as srv:
        client = make_client(srv.port)
        await client.connect()
        await client.close()
    log = _src_log(caplog)
    assert "AGENTS_DOWN" in log
    _no_leak(log)


async def test_agents_list_failure_with_unsafe_code_logs_unspecified(caplog):
    caplog.set_level(logging.DEBUG, logger="src")
    async with Server(agents_error={"code": EVIL_CODE, "message": MARK}) as srv:
        client = make_client(srv.port)
        await client.connect()
        await client.close()
    log = _src_log(caplog)
    assert "unspecified" in log
    _no_leak(log)


async def _turn_log(error, caplog):
    caplog.set_level(logging.DEBUG, logger="src")

    async def on_chat(ws, rid):
        await ws.send(_res(rid, ok=False, error=error))

    async with Server(on_chat=on_chat) as srv:
        client = make_client(srv.port)
        await client.connect()
        try:
            await asyncio.wait_for(run_turn(client), 5)
        finally:
            await client.close()
    return _src_log(caplog)


async def test_res_ok_false_logs_safe_code_but_never_message(caplog):
    log = await _turn_log({"code": "RATE_LIMITED", "message": MARK}, caplog)
    assert "RATE_LIMITED" in log
    _no_leak(log)


async def test_res_ok_false_with_unsafe_code_logs_unspecified(caplog):
    log = await _turn_log({"code": EVIL_CODE, "message": MARK}, caplog)
    assert "unspecified" in log
    _no_leak(log)
