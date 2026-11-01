"""Contrato de cierre (#221): el cierre termina aunque cancelen a quien cierra, y la
cancelación ajena se relanza al final; la propia (tarea hija) no."""

import asyncio
import logging

import pytest

from src.services.teardown import Teardown, cancel_requested

SECRET = "texto-que-no-debe-salir"


async def _sleep_forever() -> None:
    await asyncio.Event().wait()


async def test_cancel_of_own_child_is_not_propagated():
    child = asyncio.create_task(_sleep_forever())
    async with Teardown("t") as td:
        await td.cancel(child, "hija", timeout=1)
    assert child.cancelled()
    assert not td.cancelled


async def test_foreign_cancel_finishes_every_step_then_reraises():
    ran: list[str] = []
    started = asyncio.Event()

    async def closer() -> None:
        async with Teardown("t") as td:
            async def slow() -> None:
                started.set()
                await asyncio.sleep(0.05)
                ran.append("slow")

            async def after() -> None:
                ran.append("after")

            await td.step(slow(), "lento", timeout=1)
            await td.step(after(), "después", timeout=1)

    task = asyncio.create_task(closer())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The step in progress is not cut short, and every later step still runs.
    assert ran == ["slow", "after"]


async def test_double_foreign_cancel_still_finishes_the_teardown():
    ran: list[str] = []
    in_wait = asyncio.Event()

    async def child_body() -> None:
        try:
            await _sleep_forever()
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)
            raise

    child = asyncio.create_task(child_body())

    async def closer() -> None:
        async with Teardown("t") as td:
            in_wait.set()
            await td.cancel(child, "hija", timeout=1)

            async def after() -> None:
                ran.append("after")

            await td.step(after(), "después", timeout=1)

    task = asyncio.create_task(closer())
    await in_wait.wait()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.cancelled()
    assert ran == ["after"]


async def test_reraised_cancellation_keeps_its_message():
    """anyio's cancel scopes (Starlette's TestClient, #221) only swallow a
    CancelledError that carries their own message: re-raise the original."""
    started = asyncio.Event()

    async def closer() -> None:
        async with Teardown("t") as td:
            started.set()
            await td.step(_sleep_forever(), "paso", timeout=0.05)

    task = asyncio.create_task(closer())
    await started.wait()
    task.cancel("Cancelled via cancel scope 1")
    with pytest.raises(asyncio.CancelledError) as info:
        await task
    assert info.value.args == ("Cancelled via cancel scope 1",)


async def test_cancel_swallowed_by_a_step_is_still_reraised():
    started = asyncio.Event()

    async def closer() -> None:
        async with Teardown("t"):
            started.set()
            try:
                await _sleep_forever()
            except asyncio.CancelledError:
                pass  # a careless callee swallows it; the counter still says pending

    task = asyncio.create_task(closer())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_outer_wait_for_timeout_surfaces_as_timeout_and_steps_finish():
    ran: list[str] = []

    async def closer() -> None:
        async with Teardown("t") as td:
            async def slow() -> None:
                await asyncio.sleep(0.05)
                ran.append("slow")

            await td.step(slow(), "lento", timeout=1)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(closer(), 0.01)
    assert ran == ["slow"]


async def test_foreign_cancel_while_waiting_a_child_keeps_waiting_then_reraises():
    child_done = asyncio.Event()

    async def child_body() -> None:
        try:
            await _sleep_forever()
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)  # slow, cooperative shutdown
            child_done.set()
            raise

    child = asyncio.create_task(child_body())
    in_wait = asyncio.Event()

    async def closer() -> None:
        async with Teardown("t") as td:
            in_wait.set()
            await td.cancel(child, "hija", timeout=1)

    task = asyncio.create_task(closer())
    await in_wait.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child_done.is_set() and child.cancelled()


async def test_step_failure_is_logged_by_type_without_message(caplog):
    async def boom() -> None:
        raise RuntimeError(SECRET)

    caplog.set_level(logging.DEBUG, logger="src.services.teardown")
    async with Teardown("t") as td:
        await td.step(boom(), "paso")
    assert "RuntimeError" in caplog.text
    assert SECRET not in caplog.text
    assert not td.cancelled


async def test_child_failure_is_logged_by_type_without_message(caplog):
    async def boom() -> None:
        raise ValueError(SECRET)

    child = asyncio.create_task(boom())
    await asyncio.sleep(0)
    caplog.set_level(logging.DEBUG, logger="src.services.teardown")
    async with Teardown("t") as td:
        await td.wait(child, "hija")
    assert "ValueError" in caplog.text
    assert SECRET not in caplog.text


async def test_step_timeout_warns_and_continues(caplog):
    ran: list[str] = []

    async def after() -> None:
        ran.append("after")

    caplog.set_level(logging.WARNING, logger="src.services.teardown")
    async with Teardown("t") as td:
        await td.step(_sleep_forever(), "colgado", timeout=0.01)
        await td.step(after(), "después")
    assert ran == ["after"]
    assert "colgado no terminó" in caplog.text


async def test_never_cancels_the_calling_task():
    async def closer() -> bool:
        me = asyncio.current_task()
        async with Teardown("t") as td:
            await td.cancel(me, "yo mismo")  # type: ignore[arg-type]
        return True

    assert await asyncio.create_task(closer())


async def test_exception_inside_the_block_propagates():
    with pytest.raises(KeyError):
        async with Teardown("t"):
            raise KeyError("x")


async def test_cancel_requested_reflects_pending_cancellation():
    seen: list[bool] = []

    async def body() -> None:
        seen.append(cancel_requested())
        try:
            await _sleep_forever()
        except asyncio.CancelledError:
            seen.append(cancel_requested())
            raise

    task = asyncio.create_task(body())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == [False, True]
