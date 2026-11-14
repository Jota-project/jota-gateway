import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from fogar_gateway.core.config import settings
from fogar_gateway.core.logging import fingerprint_key
from fogar_gateway.services.openclaw import frames
from fogar_gateway.services.openclaw.chat_text import ChatTextTracker
from fogar_gateway.services.openclaw.device import (
    DeviceFileError,
    DeviceIdentity,
    DeviceStore,
    StoredDeviceToken,
    build_auth_payload_v3,
)
from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
from fogar_gateway.services.openclaw.models import (
    AuthMethod,
    GatewayInfo,
    IdentityError,
    OrchestratorAccess,
    ToolCallEvent,
    approve_command,
)
from fogar_gateway.services.openclaw.registry import (
    TURN_IN_PROGRESS_ERROR,
    TURN_OVERFLOW_ERROR,
    TurnInProgress,
    TurnRegistry,
)
from fogar_gateway.services.protocol import OrchestratorEvent
from fogar_gateway.services.reconnection import describe_error, traceback_no_message
from fogar_gateway.services.teardown import Teardown

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
# OpenClaw accepted the token but waits for the operator to approve this device (`last_error`).
ORCHESTRATOR_PAIRING_REQUIRED_ERROR = "orchestrator_pairing_required"
# OpenClaw asks for device identity but the gateway could not send it (identity file unusable,
# or a challenge without nonce): approving on OpenClaw would not help.
ORCHESTRATOR_DEVICE_IDENTITY_REQUIRED_ERROR = "orchestrator_device_identity_required"

_TERMINAL = ("aborted", "final", "error")  # terminal `chat` states


class OrchestratorHandshakeError(RuntimeError):
    """The OpenClaw v4 handshake failed or got an unexpected/malformed frame. The message
    is the fixed code: it never carries the offending frame or OpenClaw's error text."""

    stable_code = ORCHESTRATOR_HANDSHAKE_FAILED_ERROR

    def __init__(self) -> None:
        super().__init__(ORCHESTRATOR_HANDSHAKE_FAILED_ERROR)


class OrchestratorRejectedError(OrchestratorHandshakeError):
    """OpenClaw answered `connect` with `ok:false` (wrong or missing token, rate limit…): the
    same stable code; `fogar-gw onboard` tells it apart from a malformed or unexpected frame."""


class OrchestratorStartingError(OrchestratorHandshakeError):
    """OpenClaw was still starting (startup-sidecars) when the retry budget ran out: the same
    stable code, but not a rejected token (`fogar-gw onboard` tells them apart)."""


class OrchestratorPairingRequired(OrchestratorRejectedError):
    """OpenClaw answered `connect` with `NOT_PAIRED` / `details.code: PAIRING_REQUIRED` to a
    connect that carried the device proof: the operator has to approve this device once with
    `openclaw devices approve <request_id>`. OpenClaw checks the credential before pairing, so
    the credential in `auth_method` was accepted (`fogar-gw onboard` claims the shared token
    only when that is what was sent). Its own stable code, so `last_error` says it;
    `request_id` is the pending request (sanitized, or None)."""

    stable_code = ORCHESTRATOR_PAIRING_REQUIRED_ERROR

    def __init__(self, request_id: str | None, auth_method: AuthMethod = "shared_token") -> None:
        RuntimeError.__init__(self, ORCHESTRATOR_PAIRING_REQUIRED_ERROR)
        self.request_id = request_id
        self.auth_method = auth_method


class OrchestratorDeviceIdentityRequired(OrchestratorRejectedError):
    """OpenClaw asks for device identity (`PAIRING_REQUIRED` or `DEVICE_IDENTITY_REQUIRED`) but
    this connect carried no device proof: `cause` says why (`identity_file_invalid`: the
    identity file is unusable or cannot be created; `challenge_without_nonce`: nothing to
    sign). Approving anything on OpenClaw would not help, so it is not a pairing request."""

    stable_code = ORCHESTRATOR_DEVICE_IDENTITY_REQUIRED_ERROR

    def __init__(self, cause: IdentityError) -> None:
        RuntimeError.__init__(self, ORCHESTRATOR_DEVICE_IDENTITY_REQUIRED_ERROR)
        self.cause = cause


class _DeviceTokenRejected(Exception):
    """Internal: OpenClaw refused the stored device token (already dropped); never escapes
    _do_connect, which retries once with the shared token."""


# `details.code` that mean the stored device token no longer works (revoked, rotated, device
# removed → device_token_mismatch; approved scopes narrowed → scope_mismatch). Server 2026.10.5,
# `resolveConnectAuthDecisionCore` + `connect-error-details`.
_DEVICE_TOKEN_REJECTED = frozenset({"AUTH_DEVICE_TOKEN_MISMATCH", "AUTH_SCOPE_MISMATCH"})
_PAIRING_REQUIRED = "PAIRING_REQUIRED"
_IDENTITY_REQUIRED = frozenset({_PAIRING_REQUIRED, "DEVICE_IDENTITY_REQUIRED"})
# OpenClaw's own PAIRING_CONNECT_REQUEST_ID_PATTERN: anything else is not echoed.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _pairing_request_id(details: object) -> str | None:
    rid = details.get("requestId") if isinstance(details, dict) else None
    return rid if isinstance(rid, str) and _REQUEST_ID_RE.fullmatch(rid) else None


@dataclass
class _AuthPlan:
    """What one `connect` sends: credential, scopes and (if the challenge has a nonce and the
    identity is usable) the signed device block; `identity_error` says why there is none."""

    method: AuthMethod
    token: str = field(repr=False)
    scopes: list[str] = field(default_factory=list)
    identity: DeviceIdentity | None = None
    stored: StoredDeviceToken | None = None
    device: dict | None = None
    identity_error: IdentityError | None = None


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


# shortcut: alias kept only because src/fogar_gateway/api/openai_routes.py imports it; drop it when that
# import moves to fogar_gateway.services.reconnection.
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
        device_store: DeviceStore | None = None,
        access: OrchestratorAccess | None = None,
        use_device_token: bool = True,
    ) -> None:
        # Key of the stored device token: the configured OpenClaw `host:port`.
        self._gateway = f"{host}:{port}"
        self._device_store = device_store if device_store is not None else DeviceStore()
        # False (`fogar-gw onboard`): always the shared token, so it is what gets tested; the
        # identity still signs, so pairing is detected (and a token issued is still stored).
        self._use_device_token = use_device_token
        # Updated in place (main.py shares it with /admin/state); never replaced.
        self.access = access if access is not None else OrchestratorAccess()
        self._pairing_logged: str | None = None  # last pending request logged at WARNING
        self._identity_warned = False
        # An IPv6 literal (`::1`) goes in brackets inside the URI, unless it already has them.
        bare_ipv6 = ":" in host and not host.startswith("[")
        self._uri = f"ws://[{host}]:{port}" if bare_ipv6 else f"ws://{host}:{port}"
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
            try:
                return await self._do_connect()
            except (OrchestratorPairingRequired, OrchestratorDeviceIdentityRequired):
                raise  # access already says pending / identity_required
            except BaseException:
                self.access.reset()  # no connection now: no stale admin/paired/pending
                raise

    async def _do_connect(self) -> GatewayInfo:
        """One handshake; while OpenClaw is still starting its sidecars, another on a new
        socket after each retryAfterMs, each wait capped at what is left of
        _STARTUP_RETRY_BUDGET_S and no attempt once it is spent."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _STARTUP_RETRY_BUDGET_S
        retries = 0
        skip_device_token = False
        while True:
            try:
                return await self._connect_once(skip_device_token)
            except _DeviceTokenRejected:
                if skip_device_token:  # cannot happen (no device token sent): never loop
                    raise OrchestratorRejectedError() from None
                skip_device_token = True  # once, on a new socket, with the shared token
                continue
            except _StartupPending as e:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    logger.warning(
                        "OpenClaw rejected the handshake (code=UNAVAILABLE): still starting, %.0fs retry budget spent",
                        _STARTUP_RETRY_BUDGET_S,
                    )
                    raise OrchestratorStartingError() from None
                wait_s = min(e.wait_s, remaining)
                logger.log(
                    logging.DEBUG if retries else logging.INFO,  # one INFO per connect(), not per retry
                    "OpenClaw is starting (startup-sidecars): new handshake in %.0f ms",
                    wait_s * 1000,
                )
                retries += 1
                await asyncio.sleep(wait_s)  # the failed attempt already closed its socket

    async def _connect_once(self, skip_device_token: bool = False) -> GatewayInfo:
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

            plan = await self._plan_auth(frame.get("payload"), skip_device_token)
            req_id = str(uuid.uuid4())
            connect = frames.connect_backend(
                req_id,
                plan.token,
                scopes=plan.scopes,
                device=plan.device,
                device_token=plan.method == "device_token",
            )
            await self._ws.send(json.dumps(connect))

            raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
            hello = self._parse_handshake_frame(raw)
            if not hello.get("ok"):
                error = hello.get("error")
                wait_s = _startup_retry_after_s(error)
                if wait_s is not None:
                    raise _StartupPending(wait_s)
                rejected = hello.get("type") == "res" and hello.get("ok") is False
                details = error.get("details") if isinstance(error, dict) else None
                detail_code = _safe_error_code(details)
                if rejected and plan.method == "device_token" and detail_code in _DEVICE_TOKEN_REJECTED:
                    await self._forget_device_token(detail_code)
                    raise _DeviceTokenRejected()
                if rejected and detail_code in _IDENTITY_REQUIRED and plan.device is None:
                    cause = plan.identity_error or "challenge_without_nonce"
                    self._mark_identity_required(cause, detail_code)
                    raise OrchestratorDeviceIdentityRequired(cause)
                if rejected and detail_code == _PAIRING_REQUIRED:
                    request_id = _pairing_request_id(details)
                    self._mark_pairing_pending(request_id, plan, details)
                    raise OrchestratorPairingRequired(request_id, plan.method)
                logger.warning(
                    "OpenClaw rejected the handshake (code=%s, detail=%s)",
                    _safe_error_code(error),
                    detail_code,
                )
                if rejected:
                    raise OrchestratorRejectedError()  # a real `ok:false` answer to `connect`
                raise OrchestratorHandshakeError()  # an event, or a `res` without ok:false
            hello_payload = hello.get("payload") or {}
            if not isinstance(hello_payload, dict):
                raise OrchestratorHandshakeError()
            self.gateway_info = GatewayInfo.from_hello_ok(hello_payload)
            self._log_hello(hello_payload)
            await self._accept_hello(hello_payload, plan)
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

    # ── Device identity and auth (docs/openclaw-protocol.md, «2026-11-13») ──

    async def _plan_auth(self, challenge: object, skip_device_token: bool) -> _AuthPlan:
        """Credential for this `connect`: the stored device token of this OpenClaw `host:port`
        (with its stored scopes, like the reference client), else the shared token. With a
        challenge nonce (every real OpenClaw), the device block signs the v3 payload; without
        one (an old or fake server) the connect goes without device identity. One file read
        per connect, off the event loop."""
        payload = challenge if isinstance(challenge, dict) else {}
        nonce = payload.get("nonce")
        nonce = nonce.strip() if isinstance(nonce, str) else ""
        identity: DeviceIdentity | None = None
        stored: StoredDeviceToken | None = None
        identity_error: IdentityError | None = None
        if nonce:
            loaded = await self._load_identity()
            if loaded is None:
                identity_error = "identity_file_invalid"
            else:
                identity, stored = loaded
        else:
            identity_error = "challenge_without_nonce"
        if skip_device_token or not self._use_device_token:
            stored = None
        if stored is not None:
            scopes = list(stored.scopes or frames.OPERATOR_SCOPES)
            plan = _AuthPlan("device_token", stored.token, scopes, identity, stored)
        else:
            plan = _AuthPlan("shared_token", self._token, list(frames.OPERATOR_SCOPES), identity)
        plan.identity_error = identity_error
        if identity is None:
            return plan
        ts = payload.get("ts")
        # Like the reference client: the challenge's `ts` (a safe integer), else the clock.
        signed_at = int(time.time() * 1000)
        if isinstance(ts, int) and not isinstance(ts, bool) and 0 <= ts < 2**53:
            signed_at = ts
        signature = identity.sign(
            build_auth_payload_v3(
                device_id=identity.device_id,
                client_id=frames.CLIENT_ID,
                client_mode=frames.CLIENT_MODE,
                role=frames.ROLE,
                scopes=plan.scopes,
                signed_at_ms=signed_at,
                token=plan.token,
                nonce=nonce,
                platform=frames.CLIENT_PLATFORM,
            )
        )
        plan.device = {
            "id": identity.device_id,
            "publicKey": identity.public_key,
            "signature": signature,
            "signedAt": signed_at,
            "nonce": nonce,
        }
        return plan

    async def _load_identity(self) -> tuple[DeviceIdentity, StoredDeviceToken | None] | None:
        """The install's identity (created on first use) and the device token stored for this
        OpenClaw, in one read on a worker thread. If the file is unusable the connect goes
        without device identity (enough on the same host) and says so once."""
        try:
            identity, stored = await asyncio.to_thread(self._device_store.load_for, self._gateway)
        except (DeviceFileError, OSError) as e:
            if not self._identity_warned:
                self._identity_warned = True
                logger.warning(
                    "OpenClaw: sin identidad de dispositivo (%s en %s); solo vale con OpenClaw en "
                    "esta misma máquina. Revísalo con `fogar-gw doctor`.",
                    describe_error(e),
                    self._device_store.path,
                )
            return None
        self._identity_warned = False
        self.access.device_id = identity.device_id
        return identity, stored

    async def _forget_device_token(self, detail_code: str) -> None:
        logger.warning(
            "OpenClaw rechazó el token de dispositivo (detail=%s): se borra y se reintenta con "
            "el token compartido",
            detail_code,
        )
        try:
            await asyncio.to_thread(self._device_store.clear_token, self._gateway)
        except (DeviceFileError, OSError) as e:
            logger.warning(
                "OpenClaw: no se pudo borrar el token de dispositivo (%s)", describe_error(e)
            )

    def _mark_pairing_pending(
        self, request_id: str | None, plan: _AuthPlan, details: object
    ) -> None:
        """Once per pending request at WARNING (the approval command); every retry at DEBUG."""
        self.access.reset()
        self.access.pairing = "pending"
        self.access.pairing_request_id = request_id
        device = plan.identity.device_id[:12] if plan.identity else "?"
        next_step = details.get("recommendedNextStep") if isinstance(details, dict) else None
        if self._pairing_logged != (request_id or "?"):
            self._pairing_logged = request_id or "?"
            logger.warning(
                "OpenClaw espera que apruebes este gateway como dispositivo (petición %s, "
                "device=%s). En la máquina de OpenClaw: `%s`. El gateway reintenta solo.",
                request_id or "?",
                device,
                approve_command(request_id),
            )
        logger.debug(
            "OpenClaw: emparejamiento pendiente (next=%s)",
            next_step if isinstance(next_step, str) and _REQUEST_ID_RE.fullmatch(next_step) else None,
        )

    def _mark_identity_required(self, cause: IdentityError, detail_code: str) -> None:
        """OpenClaw wants a device proof this connect could not carry: the real cause, once per
        cause at WARNING, never an approval command (there is nothing to approve)."""
        self.access.reset()
        self.access.pairing = "identity_required"
        self.access.identity_error = cause
        if self._pairing_logged != cause:
            self._pairing_logged = cause
            why = (
                f"el fichero de identidad {self._device_store.path} no vale o no se puede crear"
                if cause == "identity_file_invalid"
                else "el reto de OpenClaw no trae nonce que firmar"
            )
            logger.warning(
                "OpenClaw exige identidad de dispositivo (detail=%s) y el gateway no puede "
                "mandarla: %s. Revísalo con `fogar-gw doctor`.",
                detail_code,
                why,
            )

    async def _accept_hello(self, payload: dict, plan: _AuthPlan) -> None:
        """Persist `hello-ok.auth.deviceToken` (`DeviceStore.accept_token`: new or different →
        saved; the same one keeps its stored scopes unless hello-ok announces broader ones)
        and publish the access this connection got. A failed write is logged: the connection
        still works."""
        auth = payload.get("auth")
        token = auth.get("deviceToken") if isinstance(auth, dict) else None
        token = token.strip() if isinstance(token, str) else ""
        scopes = self.gateway_info.scopes if self.gateway_info else None
        paired = plan.method == "device_token"
        if plan.identity is not None and token and len(token) <= 4096:
            paired = True
            try:
                update = await asyncio.to_thread(
                    self._device_store.accept_token, self._gateway, token, scopes
                )
                if update != "unchanged":
                    logger.info(
                        "OpenClaw: token de dispositivo %s (device=%s, fp=%s, scopes=%s)",
                        "guardado" if update == "saved" else "con permisos ampliados",
                        plan.identity.device_id[:12],
                        fingerprint_key(token),
                        ",".join(scopes) if scopes is not None else "desconocidos",
                    )
            except (DeviceFileError, OSError) as e:
                logger.warning(
                    "OpenClaw: no se pudo guardar el token de dispositivo (%s)", describe_error(e)
                )
        self._pairing_logged = None
        self.access.reset()
        self.access.scopes = list(scopes) if scopes is not None else None
        self.access.auth_method = plan.method
        self.access.pairing = "paired" if paired else "not_required"
        logger.info(
            "OpenClaw: acceso %s (auth=%s, pairing=%s)",
            "permisos desconocidos" if scopes is None else ",".join(scopes) or "sin permisos",
            plan.method,
            self.access.pairing,
        )

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
        try:
            await self._cancel_and_close()
        finally:
            self.access.reset()

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

        The run is not known here (a push): holding the key until the terminal `chat`
        arrives keeps it from closing the next turn empty, and the answer's `runIds` are
        tombstoned so later frames of that run are dropped (#285). No-op if a normal turn
        owns the key. Never raises an Exception.
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
        payload: object = None
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
            # After swallowing its `aborted` (a tombstone first would drop it): the runs it
            # names are tombstoned for whatever of them is still in flight.
            self._turn_registry.note_abort_result(payload)

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
                    run_id, own_run = data.get("runId"), self._turn_registry.run_of(req_id)
                    if (
                        own_run is not None
                        and run_id is not None
                        and run_id != own_run
                        and data.get("state") in _TERMINAL
                    ):
                        # #285: the end of another run on this session never ends this turn.
                        logger.debug("terminal chat of another run ignored")
                        continue
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
            if _sent and not _finished:
                # #285: whatever of this run is still in flight never reaches the next turn
                # nor comes back as a push (also if its ack has not arrived yet).
                self._turn_registry.abandon(req_id)
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
            self.access.reset()  # the connection is gone: its access no longer applies
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
