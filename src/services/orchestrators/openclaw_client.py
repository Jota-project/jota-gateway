# src/services/orchestrators/openclaw_client.py
import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Callable, Optional

import websockets
from websockets.asyncio.client import ClientConnection

from src.services.orchestrators.protocol import OrchestratorEvent

logger = logging.getLogger(__name__)


class OpenClawClient:
    """
    WebSocket client for OpenClaw gateway (protocol v4).

    Maintains a single persistent connection. Each call to stream_response()
    sends one chat.send turn and yields OrchestratorEvent tokens until the
    matching res frame arrives.
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        default_agent: str = "main",
    ):
        self._uri = f"ws://{host}:{port}"
        self._token = token
        self._default_agent = default_agent
        self._ws: Optional[ClientConnection] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._tick_interval: float = 15.0  # overwritten by hello-ok policy
        # Active turn state (one turn at a time)
        self._active_req_id: Optional[str] = None
        self._turn_queue: Optional[asyncio.Queue] = None
        # Health ping state
        self._health_futures: dict[str, asyncio.Future] = {}
        # Disconnect notification — set by ReconnectingOrchestrator at wrap time
        self.on_disconnect: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._uri)

        # 1. Wait for connect.challenge
        raw = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
        frame = json.loads(raw)
        if frame.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected connect.challenge, got: {frame}")

        # 2. Send connect (backend mode — no device signature needed from loopback)
        req_id = str(uuid.uuid4())
        await self._ws.send(json.dumps({
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {
                "minProtocol": 3,
                "maxProtocol": 4,
                "client": {
                    "id": "jota-gateway",
                    "version": "1.0.0",
                    "platform": "linux",
                    "mode": "backend",
                },
                "role": "operator",
                "scopes": ["operator.read", "operator.write"],
                "auth": {"token": self._token},
            },
        }))

        # 3. Wait for hello-ok
        raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
        hello = json.loads(raw)
        if not hello.get("ok"):
            raise RuntimeError(f"OpenClaw handshake failed: {hello.get('error')}")

        # Parse keepalive interval from policy (default 15 s if not provided)
        policy = hello.get("payload", {}).get("policy", {})
        self._tick_interval = policy.get("tickIntervalMs", 15000) / 1000.0

        # 4. Start background tasks
        self._listener_task = asyncio.create_task(self._listen())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())
        logger.info(f"OpenClawClient connected → {self._uri} (tick {self._tick_interval:.0f}s)")

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
        logger.info("OpenClawClient closed.")

    async def ping(self) -> bool:
        if not self._ws:
            return False
        req_id = str(uuid.uuid4())
        try:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            self._health_futures[req_id] = fut
            await self._ws.send(json.dumps({
                "type": "req",
                "id": req_id,
                "method": "health",
                "params": {},
            }))
            res = await asyncio.wait_for(fut, timeout=5.0)
            return res.get("ok", False)
        except Exception as e:
            logger.debug(f"OpenClawClient ping failed: {e}")
            self._health_futures.pop(req_id, None)
            return False

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _keepalive_loop(self) -> None:
        """Send periodic health pings to keep the connection alive.

        OpenClaw closes with code 4000 after tickIntervalMs × 2 silence.
        We ping at 80 % of the interval to stay well within the window.
        """
        interval = self._tick_interval * 0.8
        try:
            while True:
                await asyncio.sleep(interval)
                if self._ws:
                    await self.ping()
        except asyncio.CancelledError:
            return

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                frame = json.loads(raw)
                ftype = frame.get("type")

                if ftype == "res":
                    req_id = frame.get("id")

                    if req_id in self._health_futures:
                        fut = self._health_futures.pop(req_id)
                        if not fut.done():
                            fut.set_result(frame)

                    elif req_id == self._active_req_id and self._turn_queue is not None:
                        await self._turn_queue.put(("done", frame))

                elif ftype == "event":
                    event_name = frame.get("event")
                    payload = frame.get("payload", {})

                    if event_name == "chat" and self._turn_queue is not None:
                        await self._turn_queue.put(("chat", payload))

        except asyncio.CancelledError:
            return  # clean shutdown — do not notify wrapper
        except Exception as e:
            logger.error(f"OpenClawClient listener error: {e}")
            if self._turn_queue is not None:
                await self._turn_queue.put(("error", str(e)))

        # Connection dropped (not a clean shutdown via close())
        if self.on_disconnect:
            self.on_disconnect()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream_response(
        self,
        text: str,
        user_id: str,
        model_id: Optional[str] = None,
        system_prompt_extra: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> AsyncIterator[OrchestratorEvent]:
        if not self._ws:
            yield OrchestratorEvent(type="error", content="OpenClawClient not connected")
            return

        # session_key is required — callers must provide it via make_session_key()
        if not session_key:
            raise ValueError("session_key is required — callers must provide it via make_session_key()")
        key = session_key

        req_id = str(uuid.uuid4())
        self._active_req_id = req_id
        self._turn_queue = asyncio.Queue()
        _sent = False
        _finished = False

        try:
            try:
                await self._ws.send(json.dumps({
                    "type": "req",
                    "id": req_id,
                    "method": "chat.send",
                    "params": {
                        "session": {"key": key},
                        "message": text,
                        "idempotencyKey": str(uuid.uuid4()),
                    },
                }))
                _sent = True
            except Exception as e:
                yield OrchestratorEvent(type="error", content=f"orchestrator send failed: {e}")
                _finished = True
                return

            while True:
                kind, data = await self._turn_queue.get()

                if kind == "chat":
                    if data.get("replace"):
                        logger.warning(
                            "OpenClaw sent replace=true mid-stream — content may be inconsistent"
                        )
                    delta = data.get("deltaText", "")
                    if delta:
                        yield OrchestratorEvent(type="token", content=delta)

                elif kind == "done":
                    # res with status=started is an acknowledgement — events still coming
                    payload = data.get("payload", {})
                    if payload.get("status") == "started":
                        continue
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
            self._active_req_id = None
            self._turn_queue = None
            if _sent and not _finished and self._ws:
                # Turn was cancelled before completing — stop generation on OpenClaw side
                try:
                    await asyncio.shield(self._ws.send(json.dumps({
                        "type": "req",
                        "id": str(uuid.uuid4()),
                        "method": "chat.abort",
                        "params": {"session": {"key": key}},
                    })))
                except Exception:
                    pass
