# src/services/orchestrators/openclaw_client.py
import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Callable, Optional

import websockets
from websockets.legacy.client import WebSocketClientProtocol

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
        session_key: str = "jota-gateway-default",
    ):
        self._uri = f"ws://{host}:{port}"
        self._token = token
        self._session_key = session_key
        self._ws: Optional[WebSocketClientProtocol] = None
        self._listener_task: Optional[asyncio.Task] = None
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
                    "id": "gateway-client",
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

        # 4. Start background listener
        self._listener_task = asyncio.create_task(self._listen())
        logger.info(f"OpenClawClient connected → {self._uri}")

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("OpenClawClient closed.")

    async def ping(self) -> bool:
        if not self._ws:
            return False
        req_id = str(uuid.uuid4())
        try:
            loop = asyncio.get_event_loop()
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
    # Listener (background task)
    # ------------------------------------------------------------------

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
    ) -> AsyncIterator[OrchestratorEvent]:
        if not self._ws:
            yield OrchestratorEvent(type="error", content="OpenClawClient not connected")
            return

        req_id = str(uuid.uuid4())
        self._active_req_id = req_id
        self._turn_queue = asyncio.Queue()

        try:
            try:
                await self._ws.send(json.dumps({
                    "type": "req",
                    "id": req_id,
                    "method": "chat.send",
                    "params": {
                        "sessionKey": self._session_key,
                        "message": text,
                        "idempotencyKey": str(uuid.uuid4()),
                    },
                }))
            except Exception as e:
                yield OrchestratorEvent(type="error", content=f"orchestrator send failed: {e}")
                return

            while True:
                kind, data = await self._turn_queue.get()

                if kind == "chat":
                    delta = data.get("deltaText", "")
                    if delta:
                        yield OrchestratorEvent(type="token", content=delta)
                    # state == "final" means turn is complete (no more events, no final res)
                    if data.get("state") == "final":
                        yield OrchestratorEvent(type="status", content="done")
                        break

                elif kind == "done":
                    # Initial res with status=started means turn is running (events coming)
                    # Final res without status means turn is complete
                    payload = data.get("payload", {})
                    if payload.get("status") == "started":
                        continue  # Keep waiting for events
                    if not data.get("ok"):
                        yield OrchestratorEvent(type="error", content=str(data.get("error", {})))
                    else:
                        yield OrchestratorEvent(type="status", content="done")
                    break

                elif kind == "error":
                    yield OrchestratorEvent(type="error", content=str(data))
                    break

        finally:
            self._active_req_id = None
            self._turn_queue = None
