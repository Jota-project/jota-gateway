import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator, Callable

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from src.core.config import settings
from src.services.openclaw import frames
from src.services.openclaw.chat_text import ChatTextTracker
from src.services.openclaw.dispatcher import FrameDispatcher
from src.services.openclaw.models import GatewayInfo, ToolCallEvent
from src.services.openclaw.registry import (
    TURN_IN_PROGRESS_ERROR,
    TURN_OVERFLOW_ERROR,
    TurnInProgress,
    TurnRegistry,
)
from src.services.protocol import OrchestratorEvent
from src.services.reconnection import traceback_no_message

logger = logging.getLogger(__name__)

_DEFAULT_KEEPALIVE_S = 12.0
_MIN_KEEPALIVE_S = 1.0

# Stable error content delivered to every in-flight turn when the OpenClaw connection
# ends (clean close, error or teardown). Never a raw exception string (G-03/G-04).
ORCHESTRATOR_DISCONNECTED_ERROR = "orchestrator_disconnected"

# Other stable error contents (#228): fixed text, never `str(e)` nor OpenClaw's own error
# text. They reach WS clients as `TURN_ERROR.message` and /v1 callers as the error body.
ORCHESTRATOR_SEND_FAILED_ERROR = "orchestrator_send_failed"  # chat.send could not be written
ORCHESTRATOR_UNAVAILABLE_ERROR = "orchestrator_unavailable"  # not connected / reconnecting
ORCHESTRATOR_ERROR = "orchestrator_error"  # OpenClaw rejected the turn, or an internal failure
ORCHESTRATOR_HANDSHAKE_FAILED_ERROR = "orchestrator_handshake_failed"  # connect() handshake


class OrchestratorHandshakeError(RuntimeError):
    """The OpenClaw v4 handshake failed or got an unexpected/malformed frame. The message
    is the fixed code: it never carries the offending frame or OpenClaw's error text."""

    stable_code = ORCHESTRATOR_HANDSHAKE_FAILED_ERROR

    def __init__(self) -> None:
        super().__init__(ORCHESTRATOR_HANDSHAKE_FAILED_ERROR)

# Inbound frame size limit (G-05). websockets defaults to 1 MiB while OpenClaw announces
# up to 25 MiB in hello-ok's policy.maxPayload; a larger frame closed the socket with
# 1009 and dropped every multiplexed session. The connect happens *before* hello-ok, so
# the limit starts at the documented server default (or the last announced value on a
# reconnect) and is re-aligned with the announced maxPayload right after hello-ok.
_DEFAULT_MAX_PAYLOAD = 26_214_400  # 25 MiB, OpenClaw's documented maxPayload
_MIN_FRAME_LIMIT = 1_048_576  # 1 MiB floor: never below websockets' own default
_MAX_FRAME_LIMIT = 67_108_864  # 64 MiB cap: bounds memory if the server announces more


def _frame_limit(announced: object) -> int:
    """Clamp an announced maxPayload to [_MIN_FRAME_LIMIT, _MAX_FRAME_LIMIT]; anything
    that is not a positive int falls back to _DEFAULT_MAX_PAYLOAD."""
    if isinstance(announced, bool) or not isinstance(announced, int) or announced <= 0:
        announced = _DEFAULT_MAX_PAYLOAD
    return max(_MIN_FRAME_LIMIT, min(announced, _MAX_FRAME_LIMIT))


_traceback_no_message = traceback_no_message

_SAFE_CODE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,48}")


def _safe_error_code(error: object) -> str:
    """The short `code` OpenClaw attaches to an `error`, for operator diagnostics (#228).
    Only returned when it is a plain identifier; anything else (no code, wrong type, long,
    spaces/newlines) is `unspecified`. The `message`/body is never read."""
    code = error.get("code") if isinstance(error, dict) else None
    if isinstance(code, str) and _SAFE_CODE_RE.fullmatch(code):
        return code
    return "unspecified"


def _log_safe(value: object) -> str:
    """Server-provided scalar rendered for a log line: short, single-line."""
    return str(value).replace("\n", " ").replace("\r", " ")[:40]


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
        # Last roster known from a previous connect, kept as fallback if agents.list
        # fails on this one (#151). Captured before gateway_info is rebuilt below.
        previous_agents = dict(self.gateway_info.agents) if self.gateway_info else {}
        limit = _frame_limit(self.gateway_info.max_payload if self.gateway_info else None)
        self._ws = await websockets.connect(self._uri, max_size=limit)
        conn_err: Exception | None = None
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
            frame = self._parse_handshake_frame(raw)
            if frame.get("event") != "connect.challenge":
                raise OrchestratorHandshakeError()

            req_id = str(uuid.uuid4())
            await self._ws.send(json.dumps(frames.connect_backend(req_id, self._token)))

            raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
            hello = self._parse_handshake_frame(raw)
            if not hello.get("ok"):
                logger.warning(
                    "OpenClaw rejected the handshake (code=%s)", _safe_error_code(hello.get("error"))
                )
                raise OrchestratorHandshakeError()
            hello_payload = hello.get("payload") or {}
            if not isinstance(hello_payload, dict):
                raise OrchestratorHandshakeError()
            self.gateway_info = GatewayInfo.from_hello_ok(hello_payload)
            self._log_hello(hello_payload)
            self._apply_frame_limit(self.gateway_info.max_payload, limit)

            agents_req_id = str(uuid.uuid4())
            await self._ws.send(json.dumps(frames.agents_list(agents_req_id)))
            agents_res = await self._recv_matching(agents_req_id, timeout=10.0)
            if agents_res.get("ok"):
                self.gateway_info.update_agents_from_list(agents_res.get("payload", {}))
            else:
                # Only the short code, never OpenClaw's error text (#228).
                logger.warning(
                    "agents.list failed at connect (code=%s)",
                    _safe_error_code(agents_res.get("error")),
                )
                if not self.gateway_info.agents and previous_agents:
                    # Wiping the roster would make has_agent() reject every named
                    # agent with 1008 until the next successful agents.list (#151).
                    self.gateway_info.agents = previous_agents
                    logger.warning(
                        f"keeping last known agent roster ({len(previous_agents)} agents)"
                    )

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

    @staticmethod
    def _parse_handshake_frame(raw: str | bytes) -> dict:
        """A handshake frame that is not a JSON object is a handshake failure with a
        stable reason, never a stray AttributeError/JSONDecodeError (#228)."""
        try:
            frame = json.loads(raw)
        except ValueError:
            raise OrchestratorHandshakeError() from None
        if not isinstance(frame, dict):
            raise OrchestratorHandshakeError()
        return frame

    @staticmethod
    def _log_hello(payload: dict) -> None:
        """Once per connection: server/protocol version announced by hello-ok (G-48),
        only the fields that actually came. Makes protocol drift visible."""
        server = payload.get("server")
        version = server.get("version") if isinstance(server, dict) else None
        protocol = payload.get("protocol")
        parts = []
        if version is not None:
            parts.append(f"server_version={_log_safe(version)}")
        if protocol is not None:
            parts.append(f"protocol={_log_safe(protocol)}")
        logger.info("OpenClaw hello-ok: %s", " ".join(parts) or "no version info")

    def _apply_frame_limit(self, announced: int, current: int) -> None:
        """Align the live socket's inbound limit with hello-ok's maxPayload (G-05)."""
        wanted = _frame_limit(announced)
        if wanted == current:
            return
        protocol = getattr(self._ws, "protocol", None)
        if protocol is not None and hasattr(protocol, "max_message_size"):
            protocol.max_message_size = wanted

    async def _recv_matching(self, req_id: str, timeout: float) -> dict:
        """Read frames until the res matching req_id arrives, ignoring any
        unrelated event frames that may interleave before it (only used
        during connect(), before _listen() starts routing frames)."""
        ws = self._ws
        if ws is None:
            raise ConnectionError("not connected")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"No response for req_id={req_id} within {timeout}s")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
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
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        if not self._ws:
            yield OrchestratorEvent(type="error", content=ORCHESTRATOR_UNAVAILABLE_ERROR)
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
        text_tracker = ChatTextTracker()

        try:
            try:
                await self._ws.send(
                    json.dumps(frames.chat_send(req_id, session_key, text, str(uuid.uuid4())))
                )
                _sent = True
            except Exception as e:
                logger.warning("chat.send failed (%s)", type(e).__name__)
                logger.debug("chat.send traceback (no message): %s", _traceback_no_message(e))
                yield OrchestratorEvent(type="error", content=ORCHESTRATOR_SEND_FAILED_ERROR)
                _finished = True
                return

            while True:
                try:
                    kind, data = await asyncio.wait_for(
                        queue.get(), timeout=settings.TURN_TIMEOUT_S
                    )
                except TimeoutError:
                    # _finished stays False on purpose: the turn did not
                    # complete, it's being aborted — the `finally` below must
                    # still send chat.abort, same as any other anomalous exit.
                    yield OrchestratorEvent(type="error", content="turn_timeout")
                    break
                if kind == "chat":
                    # #241: OpenClaw 2026.10.5 sends the full text only in `final`
                    # (no deltaText); emit just what the deltas did not cover.
                    delta = text_tracker.feed(data)
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
                        # OpenClaw's own error text stays out of the client and the logs;
                        # the operator gets only the short code.
                        logger.warning(
                            "OpenClaw rejected the turn (code=%s)",
                            _safe_error_code(data.get("error")),
                        )
                        yield OrchestratorEvent(type="error", content=ORCHESTRATOR_ERROR)
                    else:
                        yield OrchestratorEvent(type="status", content="done")
                    break
                elif kind == "overflow":
                    # Consumer fell behind the bounded queue (#130 S3b). _finished
                    # stays False on purpose, like turn_timeout: the `finally`
                    # must still send chat.abort.
                    yield OrchestratorEvent(type="error", content=TURN_OVERFLOW_ERROR)
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
            if self._ws is None:
                raise ConnectionError("not connected")
            async for raw in self._ws:
                # One bad frame must never take the multiplexed connection (and every
                # session on it) down: drop it, log only the reason, never the content.
                try:
                    await self._handle_raw_frame(raw)
                except Exception as e:
                    logger.warning("dropping malformed OpenClaw frame (%s)", type(e).__name__)
                    logger.debug("malformed frame traceback (no message): %s", _traceback_no_message(e))
        except asyncio.CancelledError:
            # Clean shutdown via close()/reconnect — do NOT call on_disconnect,
            # that would wrongly trigger ReconnectingOpenClawClient's reconnect
            # loop for an intentional teardown. The `finally` below still fails
            # every in-flight turn (issue #103 follow-up).
            return
        except ConnectionClosed as e:
            # Only the numeric close codes (never the reason text): the 1009 "too big"
            # case used to be diagnosable from the message, keep it diagnosable.
            logger.error(
                "_listen: OpenClaw connection closed (%s, rcvd=%s, sent=%s)",
                type(e).__name__,
                e.rcvd.code if e.rcvd else None,
                e.sent.code if e.sent else None,
            )
        except Exception as e:
            logger.error("_listen error (%s)", type(e).__name__)
            logger.debug("_listen error traceback (no message): %s", _traceback_no_message(e))
        finally:
            # Single teardown for every way out (clean close 1000, error, cancel): any
            # turn still in flight on THIS connection has no one left to deliver its
            # response — without error_all() it would wait TURN_TIMEOUT_S and keep its
            # session_key occupied (G-04). Stable message: no raw exception text.
            self._turn_registry.error_all(ORCHESTRATOR_DISCONNECTED_ERROR)
        if self.on_disconnect:
            self.on_disconnect()

    async def _handle_raw_frame(self, raw: str | bytes) -> None:
        frame = json.loads(raw)
        if not isinstance(frame, dict):
            raise TypeError("frame is not an object")
        fid = frame.get("id", "")
        if fid in self._health_futures:
            fut = self._health_futures.pop(fid)
            if not fut.done():
                fut.set_result(frame)
            return
        await self._dispatcher.dispatch(frame)

    def _keepalive_interval(self) -> float:
        """Seconds between keepalive pings: 80% of the server's tick, never below
        _MIN_KEEPALIVE_S (issue #135 — tickIntervalMs=0 used to spin the loop)."""
        if not self.gateway_info:
            return _DEFAULT_KEEPALIVE_S
        return max(self.gateway_info.tick_interval_ms * 0.8 / 1000, _MIN_KEEPALIVE_S)

    async def _keepalive_loop(self) -> None:
        interval = self._keepalive_interval()
        try:
            while True:
                await asyncio.sleep(interval)
                if self._ws:
                    await self.ping()
        except asyncio.CancelledError:
            return
