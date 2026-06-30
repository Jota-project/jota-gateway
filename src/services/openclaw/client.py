import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Callable, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.models import GatewayInfo
from src.services.openclaw.registry import TurnRegistry
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
        self._ws: Optional[ClientConnection] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._health_futures: dict[str, asyncio.Future] = {}
        self.gateway_info: Optional[GatewayInfo] = None
        # Called only on unexpected disconnect (not on clean close()).
        # Set by ReconnectingOrchestrator after wrapping this client.
        self.on_disconnect: Optional[Callable[[], None]] = None

    async def connect(self) -> GatewayInfo:
        self._ws = await websockets.connect(self._uri)

        raw = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
        frame = json.loads(raw)
        if frame.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected connect.challenge, got: {frame}")

        req_id = str(uuid.uuid4())
        await self._ws.send(json.dumps({
            "type": "req", "id": req_id, "method": "connect",
            "params": {
                "minProtocol": 3, "maxProtocol": 4,
                "client": {
                    "id": "gateway-client", "version": "1.0.0",
                    "platform": "linux", "mode": "backend",
                },
                "role": "operator",
                "scopes": ["operator.read", "operator.write"],
                "auth": {"token": self._token},
            },
        }))

        raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
        hello = json.loads(raw)
        if not hello.get("ok"):
            raise RuntimeError(f"OpenClaw handshake failed: {hello.get('error')}")
        self.gateway_info = GatewayInfo.from_hello_ok(hello.get("payload", {}))

        sub_id = str(uuid.uuid4())
        await self._ws.send(json.dumps({
            "type": "req", "id": sub_id, "method": "sessions.subscribe", "params": {},
        }))

        # Consume the subscribe ack synchronously before starting _listen.
        # If we skipped this, the ack would flow into _listen → dispatcher, which
        # silently ignores unknown res frames — fragile. Awaiting it here is cleaner.
        sub_ack = await asyncio.wait_for(self._ws.recv(), timeout=10.0)  # noqa: F841

        self._listener_task = asyncio.create_task(self._listen())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.info(
            f"OpenClawClient connected → {self._uri} "
            f"(tick {self.gateway_info.tick_interval_ms}ms, "
            f"default_agent={self.gateway_info.default_agent_id})"
        )
        return self.gateway_info

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

    async def ping(self) -> bool:
        if not self._ws:
            return False
        req_id = str(uuid.uuid4())
        try:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._health_futures[req_id] = fut
            await self._ws.send(json.dumps({
                "type": "req", "id": req_id, "method": "health", "params": {},
            }))
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
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]:
        if not self._ws:
            yield OrchestratorEvent(type="error", content="not connected")
            return
        if not session_key:
            raise ValueError("session_key is required — callers must provide it via make_session_key()")

        req_id = str(uuid.uuid4())
        queue = self._turn_registry.register(req_id, session_key)
        _sent = False
        _finished = False

        try:
            try:
                await self._ws.send(json.dumps({
                    "type": "req", "id": req_id, "method": "chat.send",
                    "params": {
                        "sessionKey": session_key,
                        "message": text,
                        "idempotencyKey": str(uuid.uuid4()),
                    },
                }))
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
                elif kind == "done":
                    if not data.get("ok"):
                        yield OrchestratorEvent(type="error", content=str(data.get("error", {})))
                    else:
                        yield OrchestratorEvent(type="status", content="done")
                    _finished = True
                    break
                elif kind == "error":
                    yield OrchestratorEvent(type="error", content=str(data))
                    _finished = True
                    break
        finally:
            self._turn_registry.unregister(session_key, req_id)
            if _sent and not _finished and self._ws:
                try:
                    await asyncio.shield(self._ws.send(json.dumps({
                        "type": "req", "id": str(uuid.uuid4()),
                        "method": "chat.abort",
                        "params": {"sessionKey": session_key},
                    })))
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
            # Clean shutdown via close() — do NOT call on_disconnect.
            # Cancellation means we intentionally stopped listening; it is not a drop.
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
