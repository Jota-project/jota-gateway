import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator, Callable

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from jota_gateway.core.config import settings
from jota_gateway.services.openclaw import frames
from jota_gateway.services.openclaw.chat_text import ChatTextTracker
from jota_gateway.services.openclaw.dispatcher import FrameDispatcher
from jota_gateway.services.openclaw.models import GatewayInfo, ToolCallEvent
from jota_gateway.services.openclaw.registry import (
    TURN_IN_PROGRESS_ERROR,
    TURN_OVERFLOW_ERROR,
    TurnInProgress,
    TurnRegistry,
)
from jota_gateway.services.protocol import OrchestratorEvent
from jota_gateway.services.reconnection import describe_error, traceback_no_message
from jota_gateway.services.teardown import Teardown

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

_TERMINAL = ("aborted", "final", "error")  # terminal `chat` states


class OrchestratorHandshakeError(RuntimeError):
    """The OpenClaw v4 handshake failed or got an unexpected/malformed frame. The message
    is the fixed code: it never carries the offending frame or OpenClaw's error text."""

    stable_code = ORCHESTRATOR_HANDSHAKE_FAILED_ERROR

    def __init__(self) -> None:
        super().__init__(ORCHESTRATOR_HANDSHAKE_FAILED_ERROR)


# OpenClaw ≥2026.11 answers `connect` with a retryable UNAVAILABLE while its startup sidecars
# boot (docs/openclaw-protocol.md): connect() retries the handshake within this budget.
_STARTUP_RETRY_BUDGET_S = 30.0
_STARTUP_RETRY_MAX_WAIT_S = 5.0  # per wait, whatever retryAfterMs says


class _StartupPending(Exception):
    """Internal: OpenClaw is still starting; never escapes _do_connect."""

    def __init__(self, wait_s: float) -> None:
        super().__init__()
        self.wait_s = wait_s


def _startup_retry_after_s(error: object) -> float | None:
    """Wait before the next handshake if `error` is OpenClaw's startup-sidecars UNAVAILABLE
    (its own test: code, retryable and details.reason), else None. Like its reference
    client: retryAfterMs missing/invalid → 500 ms, never below 100 ms; capped here at
    _STARTUP_RETRY_MAX_WAIT_S."""
    if not isinstance(error, dict):
        return None
    details = error.get("details")
    if not (
        error.get("code") == "UNAVAILABLE"
        and error.get("retryable") is True
        and isinstance(details, dict)
        and details.get("reason") == "startup-sidecars"
    ):
        return None
    ms = error.get("retryAfterMs")
    if isinstance(ms, bool) or not isinstance(ms, int | float) or not ms >= 0:  # `not >=` catches NaN
        ms = 500
    # Clamp before dividing: a huge int / 1000 raises OverflowError.
    return max(100, min(ms, _STARTUP_RETRY_MAX_WAIT_S * 1000)) / 1000


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


# shortcut: alias kept only because src/jota_gateway/api/openai_routes.py imports it; drop it when that
# import moves to jota_gateway.services.reconnection.
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
        """One handshake; while OpenClaw is still starting its sidecars, another on a new
        socket after each retryAfterMs, each wait capped at what is left of
        _STARTUP_RETRY_BUDGET_S and no attempt once it is spent."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _STARTUP_RETRY_BUDGET_S
        retries = 0
        while True:
            try:
                return await self._connect_once()
            except _StartupPending as e:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    logger.warning(
                        "OpenClaw rejected the handshake (code=UNAVAILABLE): still starting, %.0fs retry budget spent",
                        _STARTUP_RETRY_BUDGET_S,
                    )
                    raise OrchestratorHandshakeError() from None
                wait_s = min(e.wait_s, remaining)
                logger.log(
                    logging.DEBUG if retries else logging.INFO,  # one INFO per connect(), not per retry
                    "OpenClaw is starting (startup-sidecars): new handshake in %.0f ms",
                    wait_s * 1000,
                )
                retries += 1
                await asyncio.sleep(wait_s)  # the failed attempt already closed its socket

    async def _connect_once(self) -> GatewayInfo:
        # Last roster known from a previous connect, kept as fallback if agents.list
        # fails on this one (#151). Captured before gateway_info is rebuilt below.
        previous_agents = list(self.gateway_info.agents) if self.gateway_info else []
        limit = _frame_limit(self.gateway_info.max_payload if self.gateway_info else None)
        self._ws = await websockets.connect(self._uri, max_size=limit)
        ok = False
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
                wait_s = _startup_retry_after_s(hello.get("error"))
                if wait_s is not None:
                    raise _StartupPending(wait_s)
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
                if previous_agents:
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
            ok = True
            return self.gateway_info
        finally:
            # También si cancelan connect() (#221): el socket abierto no se queda sin dueño.
            if not ok and self._ws is not None:
                try:
                    await self._ws.close()
                except Exception as e:  # noqa: BLE001 — limpieza tras un connect fallido
                    logger.debug("OpenClaw: cierre del socket tras connect fallido (%s)", describe_error(e))
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
        await self._cancel_and_close()

    async def _cancel_and_close(self) -> None:
        """Teardown used by close() and by connect() before it establishes a new
        connection — cancelling the old listener before the new socket exists prevents
        it from ever racing the new handshake for frames (issue #103).

        G-52 (#221): Teardown finishes every step even if the caller (e.g. the
        background reconnect task) is cancelled meanwhile, then re-raises that
        cancellation; failures of the old tasks go to DEBUG with their type. The 12 s
        socket step sits above websockets' close_timeout (10 s).
        """
        async with Teardown("OpenClaw close") as td:
            await td.cancel(self._keepalive_task, "keepalive", timeout=2.0)
            await td.cancel(self._listener_task, "listener", timeout=2.0)
            self._keepalive_task = None
            self._listener_task = None
            if self._ws:
                await td.step(self._ws.close(), "cierre del socket", timeout=12.0)
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
        except Exception as e:  # noqa: BLE001 — frontera: un ping fallido es `False`
            logger.debug("ping failed (%s)", describe_error(e))
            return False
        finally:
            self._health_futures.pop(req_id, None)  # también si cancelan el ping

    async def abort(self, session_key: str, timeout: float = 1.0) -> None:
        """Abort the run in flight on `session_key` and swallow its own `aborted`.

        Frames are routed by session_key, not runId (G-14): holding the key until the
        terminal `chat` arrives keeps it from closing the next turn empty. No-op if a
        normal turn owns the key. Never raises an Exception.
        """
        if not self._ws:
            return
        req_id = str(uuid.uuid4())
        try:
            queue = self._turn_registry.register(req_id, session_key)
        except TurnInProgress:
            return
        # The ok:true `res` never reaches the turn queue (the dispatcher only routes
        # ok:false): catch it by id, as ping() does.
        res: asyncio.Future = asyncio.get_running_loop().create_future()
        self._health_futures[req_id] = res
        try:
            await self._ws.send(json.dumps(frames.chat_abort(req_id, session_key)))
            async with asyncio.timeout(timeout):
                payload = (await res).get("payload")
                if not (isinstance(payload, dict) and payload.get("aborted")):
                    return  # nothing was running: no `aborted` will follow
                # The `aborted` usually comes before the `res`: it is already queued.
                while True:
                    kind, data = await queue.get()
                    if kind == "tool" or (kind == "chat" and data.get("state") not in _TERMINAL):
                        continue
                    return
        except Exception as e:  # noqa: BLE001 — frontera: el abort es best-effort, el turno siguiente sigue
            logger.debug("chat.abort failed (%s)", describe_error(e))
        finally:
            self._health_futures.pop(req_id, None)
            self._turn_registry.unregister(session_key, req_id)

    async def stream_response(
        self,
        text: str,
        session_key: str,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        if not self._ws:
            yield OrchestratorEvent(type="error", content=ORCHESTRATOR_UNAVAILABLE_ERROR)
            return

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
            except Exception as e:  # noqa: BLE001 — frontera: el turno cede orchestrator_send_failed
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
                    if data.get("state") == "error":
                        # #300: the agent failed after accepting the turn (no `final`
                        # follows). errorMessage stays out of the client and the logs.
                        _finished = True
                        logger.warning("OpenClaw agent failed mid-turn (chat state=error)")
                        yield OrchestratorEvent(type="error", content=ORCHESTRATOR_ERROR)
                        break
                elif kind == "tool":
                    tool_call = ToolCallEvent.from_session_tool_payload(data)
                    if tool_call is not None:
                        yield OrchestratorEvent(type="tool_call", tool_call=tool_call)
                elif kind == "done":
                    # Only a rejected `res` (ok:false) reaches the queue (see dispatcher).
                    # OpenClaw's own error text stays out of the client and the logs;
                    # the operator gets only the short code.
                    _finished = True
                    logger.warning(
                        "OpenClaw rejected the turn (code=%s)",
                        _safe_error_code(data.get("error")),
                    )
                    yield OrchestratorEvent(type="error", content=ORCHESTRATOR_ERROR)
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
                except Exception as e:  # noqa: BLE001 — limpieza: el turno ya terminó
                    logger.debug("chat.abort failed (%s)", describe_error(e))

    async def _listen(self) -> None:
        try:
            if self._ws is None:
                raise ConnectionError("not connected")
            async for raw in self._ws:
                # One bad frame must never take the multiplexed connection (and every
                # session on it) down: drop it, log only the reason, never the content.
                try:
                    await self._handle_raw_frame(raw)
                except Exception as e:  # noqa: BLE001 — frontera: un frame malo no tumba la conexión
                    logger.warning("dropping malformed OpenClaw frame (%s)", type(e).__name__)
                    logger.debug("malformed frame traceback (no message): %s", _traceback_no_message(e))
        except ConnectionClosed as e:
            # Only the numeric close codes (never the reason text): the 1009 "too big"
            # case used to be diagnosable from the message, keep it diagnosable.
            logger.error(
                "_listen: OpenClaw connection closed (%s, rcvd=%s, sent=%s)",
                type(e).__name__,
                e.rcvd.code if e.rcvd else None,
                e.sent.code if e.sent else None,
            )
        except Exception as e:  # noqa: BLE001 — frontera: la caída se avisa con on_disconnect
            logger.error("_listen error (%s)", type(e).__name__)
            logger.debug("_listen error traceback (no message): %s", _traceback_no_message(e))
        finally:
            # Single teardown for every way out (clean close 1000, error, cancel): any
            # turn still in flight on THIS connection has no one left to deliver its
            # response — without error_all() it would wait TURN_TIMEOUT_S and keep its
            # session_key occupied (G-04). Stable message: no raw exception text.
            self._turn_registry.error_all(ORCHESTRATOR_DISCONNECTED_ERROR)
        # A cancellation (clean close()/reconnect) propagates from the `finally` above and
        # never gets here: on_disconnect would wrongly start the reconnect loop (#103).
        if self.on_disconnect:
            try:
                self.on_disconnect()
            except Exception as e:  # noqa: BLE001 — frontera: hook del wrapper, la tarea no lo recoge
                logger.warning("on_disconnect hook failed (%s)", describe_error(e))
                logger.debug("on_disconnect traceback (no message): %s", traceback_no_message(e))

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
        while True:
            await asyncio.sleep(interval)
            if self._ws:
                await self.ping()
