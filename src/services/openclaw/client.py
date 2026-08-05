import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from src.services.openclaw import frames
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.models import GatewayInfo, ToolCallEvent
from src.services.openclaw.registry import TURN_IN_PROGRESS_ERROR, TurnInProgress, TurnRegistry
from src.services.protocol import OrchestratorEvent

logger = logging.getLogger(__name__)


class OpenClawClient:
    """Single persistent WebSocket to OpenClaw, multiplexed across all sessions.

    _listen receives every frame and calls dispatcher.dispatch() — no routing logic here.
    Health pings bypass the dispatcher via _health_futures (they have no session key).
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        turn_registry: TurnRegistry,
        dispatcher: FrameDispatcher,
    ) -> None:
        self._uri = f"ws://{host}:{port}"
        self._token = token
        self._turn_registry = turn_registry
        self._dispatcher = dispatcher
        self._ws: ClientConnection | None = None
        self._listener_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._health_futures: dict[str, asyncio.Future] = {}
        self.gateway_info: GatewayInfo | None = None
        # Called only on unexpected disconnect (not on clean close()).
        # Set by ReconnectingOrchestrator after wrapping this client.
        self.on_disconnect: Callable[[], None] | None = None

    async def connect(self) -> GatewayInfo:
        async with self._connect_lock:
            await self._cancel_and_close()
            return await self._do_connect()

    async def _do_connect(self) -> GatewayInfo:
        self._ws = await websockets.connect(self._uri)
        conn_err: Exception | None = None
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
            frame = json.loads(raw)
            if frame.get("event") != "connect.challenge":
                raise RuntimeError(f"Expected connect.challenge, got: {frame}")

            req_id = str(uuid.uuid4())
            await self._ws.send(json.dumps(frames.connect_backend(req_id, self._token)))

            raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
            hello = json.loads(raw)
            if not hello.get("ok"):
                raise RuntimeError(f"OpenClaw handshake failed: {hello.get('error')}")
            self.gateway_info = GatewayInfo.from_hello_ok(hello.get("payload", {}))

            agents_req_id = str(uuid.uuid4())
            await self._ws.send(json.dumps(frames.agents_list(agents_req_id)))
            agents_res = await self._recv_matching(agents_req_id, timeout=10.0)
            if agents_res.get("ok"):
                self.gateway_info.update_agents_from_list(agents_res.get("payload", {}))
            else:
                logger.warning(f"agents.list failed at connect: {agents_res.get('error')}")

            sub_id = str(uuid.uuid4())
            await self._ws.send(json.dumps(frames.sessions_subscribe(sub_id)))
            sub_ack = await asyncio.wait_for(self._ws.recv(), timeout=10.0)  # noqa: F841

            self._listener_task = asyncio.create_task(self._listen())
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())
            logger.info(
                f"OpenClawClient connected → {self._uri} "
                f"(tick {self.gateway_info.tick_interval_ms}ms, "
                f"default_agent={self.gateway_info.default_agent_id})"
            )
            return self.gateway_info
        except Exception as exc:
            conn_err = exc
            raise
        finally:
            if conn_err is not None and self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None

    async def _recv_matching(self, req_id: str, timeout: float) -> dict:
        """Read frames until the res matching req_id arrives, ignoring any
        unrelated event frames that may interleave before it (only used
        during connect(), before _listen() starts routing frames)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"No response for req_id={req_id} within {timeout}s")
            raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            frame = json.loads(raw)
            if frame.get("id") == req_id:
                return frame

    async def close(self) -> None:
        for task in (self._keepalive_task, self._listener_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._keepalive_task = None
        self._listener_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _cancel_and_close(self) -> None:
        """Best-effort teardown of a previous connection before connect()
        establishes a new one — cancelling the old listener before the new
        socket exists prevents it from ever racing the new handshake for
        frames (issue #103).

        Note: _listen()/_keepalive_loop() swallow their own CancelledError
        and return normally (see _listen()'s docstring) — so if the caller
        of connect() (e.g. the background _reconnect_loop task) is itself
        cancelled while suspended on `await task` here, asyncio can absorb
        that outer cancellation rather than re-raising it, since the awaited
        task completes without an exception. Bounded impact: the caller
        simply keeps running until this teardown finishes, it isn't lost.
        """
        for task in (self._keepalive_task, self._listener_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (Exception, asyncio.CancelledError):
                    pass
        self._keepalive_task = None
        self._listener_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def ping(self) -> bool:
        if not self._ws:
            return False
        req_id = str(uuid.uuid4())
        try:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._health_futures[req_id] = fut
            await self._ws.send(json.dumps(frames.health(req_id)))
            res = await asyncio.wait_for(fut, timeout=5.0)
            return res.get("ok", False)
        except Exception as e:
            logger.debug(f"ping failed: {e}")
            self._health_futures.pop(req_id, None)
            return False

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: str | None = None,
        session_key: str | None = None,
    ) -> AsyncIterator[OrchestratorEvent]:
        if not self._ws:
            yield OrchestratorEvent(type="error", content="not connected")
            return
        if not session_key:
            raise ValueError(
                "session_key is required — callers must provide it via make_session_key()"
            )

        req_id = str(uuid.uuid4())
        try:
            queue = self._turn_registry.register(req_id, session_key)
        except TurnInProgress:
            yield OrchestratorEvent(type="error", content=TURN_IN_PROGRESS_ERROR)
            return
        _sent = False
        _finished = False

        try:
            try:
                await self._ws.send(
                    json.dumps(frames.chat_send(req_id, session_key, text, str(uuid.uuid4())))
                )
                _sent = True
            except Exception as e:
                yield OrchestratorEvent(type="error", content=f"send failed: {e}")
                _finished = True
                return

            while True:
                kind, data = await queue.get()
                if kind == "chat":
                    delta = data.get("deltaText", "")
                    if delta:
                        yield OrchestratorEvent(type="token", content=delta)
                    if data.get("state") == "final":
                        # Set *before* yielding: closing the generator right at
                        # this suspended yield (contextlib.aclosing in
                        # call_orchestrator) throws GeneratorExit here, skipping
                        # any code written after the yield — _finished must
                        # already be true by then or the `finally` below wrongly
                        # sends a chat.abort for a turn that already ended.
                        _finished = True
                        yield OrchestratorEvent(type="status", content="done")
                        break
                elif kind == "tool":
                    tool_call = ToolCallEvent.from_session_tool_payload(data)
                    if tool_call is not None:
                        yield OrchestratorEvent(type="tool_call", tool_call=tool_call)
                elif kind == "done":
                    _finished = True
                    if not data.get("ok"):
                        yield OrchestratorEvent(type="error", content=str(data.get("error", {})))
                    else:
                        yield OrchestratorEvent(type="status", content="done")
                    break
                elif kind == "error":
                    _finished = True
                    yield OrchestratorEvent(type="error", content=str(data))
                    break
        finally:
            self._turn_registry.unregister(session_key, req_id)
            if _sent and not _finished and self._ws:
                try:
                    await asyncio.shield(
                        self._ws.send(json.dumps(frames.chat_abort(str(uuid.uuid4()), session_key)))
                    )
                except Exception:
                    pass

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                frame = json.loads(raw)
                fid = frame.get("id", "")
                if fid in self._health_futures:
                    fut = self._health_futures.pop(fid)
                    if not fut.done():
                        fut.set_result(frame)
                    continue
                await self._dispatcher.dispatch(frame)
        except asyncio.CancelledError:
            # Clean shutdown via close()/reconnect — do NOT call on_disconnect,
            # that would wrongly trigger ReconnectingOpenClawClient's reconnect
            # loop for an intentional teardown. But any turn still in flight on
            # THIS connection has no one left to deliver its response — without
            # error_all() it hangs forever at `await queue.get()` (no timeout
            # anywhere in the chain) and permanently occupies its session_key
            # in TurnRegistry (issue #103 follow-up).
            self._turn_registry.error_all("connection closed")
            return
        except Exception as e:
            logger.error(f"_listen error: {e}")
            self._turn_registry.error_all(str(e))
        if self.on_disconnect:
            self.on_disconnect()

    async def _keepalive_loop(self) -> None:
        interval = self.gateway_info.tick_interval_ms * 0.8 / 1000 if self.gateway_info else 12.0
        try:
            while True:
                await asyncio.sleep(interval)
                if self._ws:
                    await self.ping()
        except asyncio.CancelledError:
            return
