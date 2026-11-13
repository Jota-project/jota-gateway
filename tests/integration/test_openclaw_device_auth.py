"""El gateway como dispositivo de OpenClaw (M19) contra el OpenClaw falso real: firma v3 en cada
`connect`, camino local sin emparejar, `PAIRING_REQUIRED`, token de dispositivo y su rechazo, y
un acceso que refleja la conexión de ahora (no la última que hubo)."""

import asyncio
import json
import logging
import stat

import pytest

from fogar_gateway.services.openclaw import client as client_mod
from fogar_gateway.services.openclaw.client import (
    ORCHESTRATOR_DEVICE_IDENTITY_REQUIRED_ERROR,
    ORCHESTRATOR_PAIRING_REQUIRED_ERROR,
    OpenClawClient,
    OrchestratorDeviceIdentityRequired,
    OrchestratorPairingRequired,
    OrchestratorRejectedError,
    _AuthPlan,
)
from fogar_gateway.services.openclaw.device import DeviceStore, StoredDeviceToken
from fogar_gateway.services.openclaw.dispatcher import FrameDispatcher
from fogar_gateway.services.openclaw.models import GatewayInfo
from fogar_gateway.services.openclaw.reconnecting import ReconnectingOpenClawClient
from fogar_gateway.services.openclaw.registry import ClientRegistry, TurnRegistry
from tests.fakes.openclaw import FakeOpenClawServer

SHARED = "shared-SECRET-token"
ALL_SCOPES = ["operator.read", "operator.write", "operator.admin"]


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "openclaw-device.json")


def make_client(port: int, store: DeviceStore, token: str = SHARED, **kw) -> OpenClawClient:
    turns = TurnRegistry()
    return OpenClawClient(
        "127.0.0.1", port, token, turns, FrameDispatcher(turns, ClientRegistry()),
        device_store=store, **kw,
    )


async def connect_once(client: OpenClawClient):
    """connect() y close(); devuelve (GatewayInfo, acceso mientras estaba conectado)."""
    try:
        info = await client.connect()
        return info, client.access.as_dict()
    finally:
        await client.close()


def stored_token(store: DeviceStore, port: int) -> str | None:
    stored = store.token_for(f"127.0.0.1:{port}")
    return stored.token if stored else None


async def test_local_connect_signs_and_gets_admin_without_pairing(store):
    async with FakeOpenClawServer(token=SHARED) as server:
        client = make_client(server.port, store)
        info, access = await connect_once(client)
        params = server.connects[0]
        assert params["scopes"] == ALL_SCOPES
        assert params["auth"] == {"token": SHARED}
        device = params["device"]  # el fake ya validó la firma: si no, el connect fallaría
        identity = store.load_or_create()
        assert device["id"] == identity.device_id and device["publicKey"] == identity.public_key
        assert info.scopes == ALL_SCOPES and info.admin
        assert access == {
            "scopes": ALL_SCOPES,
            "admin": True,
            "auth_method": "shared_token",
            "pairing": "not_required",
            "pairing_request_id": None,
            "identity_error": None,
            "device_id": identity.device_id,
        }
        assert stored_token(store, server.port) is None  # en local no hay emparejamiento
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


async def test_challenge_without_nonce_connects_without_device(store):
    async with FakeOpenClawServer(token=SHARED, challenge_nonce=False) as server:
        _, access = await connect_once(make_client(server.port, store))
        assert "device" not in server.connects[0]
        assert not store.path.exists()
        assert access["pairing"] == "not_required" and access["admin"]


async def test_hello_without_scopes_is_unknown_not_none(store):
    hello = {"type": "hello-ok", "protocol": 4, "policy": {"tickIntervalMs": 30000},
             "snapshot": {}, "auth": {"role": "operator"}}
    async with FakeOpenClawServer(token=SHARED, hello_payload=hello) as server:
        info, access = await connect_once(make_client(server.port, store))
        assert info.scopes is None and not info.admin
        assert access["scopes"] is None and access["pairing"] == "not_required"


async def test_bad_signature_is_rejected_by_the_fake(store, monkeypatch):
    """El fake valida la firma de verdad: un payload distinto del que reconstruye OpenClaw falla."""
    real = client_mod.build_auth_payload_v3
    monkeypatch.setattr(client_mod, "build_auth_payload_v3", lambda **kw: real(**kw) + "x")
    async with FakeOpenClawServer(token=SHARED) as server:
        with pytest.raises(OrchestratorRejectedError) as excinfo:
            await connect_once(make_client(server.port, store))
        assert excinfo.type is OrchestratorRejectedError


async def test_wrong_shared_token_is_a_rejection_not_pairing(store):
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        with pytest.raises(OrchestratorRejectedError) as excinfo:
            await connect_once(make_client(server.port, store, token="otro"))
        assert excinfo.type is OrchestratorRejectedError
        assert not server.pending


async def test_remote_pairing_then_device_token(store, caplog):
    caplog.set_level(logging.DEBUG, logger="fogar_gateway")
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        client = make_client(server.port, store)
        for _ in range(2):  # dos intentos con la petición pendiente: un solo WARNING
            with pytest.raises(OrchestratorPairingRequired) as excinfo:
                await client.connect()
            assert excinfo.value.request_id == "req-1"
            assert excinfo.value.auth_method == "shared_token"
            assert excinfo.value.stable_code == ORCHESTRATOR_PAIRING_REQUIRED_ERROR
            assert client.access.pairing == "pending"
            assert client.access.pairing_request_id == "req-1"
            assert client.access.scopes is None and not client.access.admin
        warnings = [r for r in caplog.records
                    if r.levelno == logging.WARNING and "openclaw devices approve req-1" in r.getMessage()]
        assert len(warnings) == 1

        server.approve("req-1")
        info, access = await connect_once(client)
        token = stored_token(store, server.port)
        assert token and token.startswith("devtok-")
        assert info.admin and access["pairing"] == "paired"
        assert access["auth_method"] == "shared_token" and access["pairing_request_id"] is None
        assert store.token_for(f"127.0.0.1:{server.port}").scopes == tuple(ALL_SCOPES)

        _, access = await connect_once(client)  # a partir de aquí, con el token de dispositivo
        params = server.connects[-1]
        assert params["auth"] == {"token": token, "deviceToken": token}
        assert params["scopes"] == ALL_SCOPES  # los guardados con el token
        assert access["auth_method"] == "device_token" and access["pairing"] == "paired"
        assert SHARED not in caplog.text and token not in caplog.text


async def test_verification_client_never_uses_the_stored_device_token(store):
    """`use_device_token=False` (onboard): se prueba el token compartido aunque haya uno de
    dispositivo guardado para este OpenClaw; un token compartido malo se rechaza."""
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        with pytest.raises(OrchestratorPairingRequired):
            await make_client(server.port, store).connect()
        server.approve()
        await connect_once(make_client(server.port, store))
        assert stored_token(store, server.port)

        await connect_once(make_client(server.port, store, use_device_token=False))
        assert server.connects[-1]["auth"] == {"token": SHARED}
        assert "device" in server.connects[-1]  # firma igual
        with pytest.raises(OrchestratorRejectedError):
            await connect_once(make_client(server.port, store, token="malo", use_device_token=False))


async def test_device_token_works_after_the_shared_token_changes(store):
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        client = make_client(server.port, store)
        with pytest.raises(OrchestratorPairingRequired):
            await client.connect()
        server.approve()
        await connect_once(client)
        server.token = "rotated-shared-token"
        info, _ = await connect_once(make_client(server.port, store, token=SHARED))
        assert info.admin


async def test_revoked_device_token_falls_back_to_shared_token_once(store, caplog):
    caplog.set_level(logging.WARNING, logger="fogar_gateway")
    async with FakeOpenClawServer(token=SHARED) as server:
        client = make_client(server.port, store)
        identity = store.load_or_create()
        server.paired[identity.device_id] = ALL_SCOPES  # emparejado antes: también en local da token
        await connect_once(client)
        assert stored_token(store, server.port)

        server.revoke(identity.device_id)
        before = server.connections
        info, access = await connect_once(client)
        assert server.connections == before + 2  # rechazado y, en otro socket, con el compartido
        assert server.connects[-1]["auth"] == {"token": SHARED}
        assert stored_token(store, server.port) is None
        assert info.admin and access["auth_method"] == "shared_token"
        assert access["pairing"] == "not_required"
        assert "AUTH_DEVICE_TOKEN_MISMATCH" in caplog.text


async def test_revoked_device_token_remote_goes_back_to_pairing(store):
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        client = make_client(server.port, store)
        with pytest.raises(OrchestratorPairingRequired):
            await client.connect()
        server.approve()
        await connect_once(client)
        server.revoke(store.load_or_create().device_id)
        with pytest.raises(OrchestratorPairingRequired) as excinfo:
            await client.connect()
        assert excinfo.value.request_id == "req-2"
        assert stored_token(store, server.port) is None


async def test_token_for_another_openclaw_is_not_sent(store):
    async with FakeOpenClawServer(token=SHARED) as server:
        store.load_or_create()
        store.save_token("10.0.0.5:18789", "devtok-other", ["operator.read"])
        await connect_once(make_client(server.port, store))
        assert server.connects[0]["auth"] == {"token": SHARED}
        assert server.connects[0]["scopes"] == ALL_SCOPES


async def test_unusable_identity_file_connects_without_device(store, caplog):
    caplog.set_level(logging.WARNING, logger="fogar_gateway")
    store.path.write_text("{roto")
    async with FakeOpenClawServer(token=SHARED) as server:
        client = make_client(server.port, store)
        await connect_once(client)
        await connect_once(client)
        assert "device" not in server.connects[0]
        assert store.path.read_text() == "{roto"  # nunca se pisa
        assert caplog.text.count("sin identidad de dispositivo") == 1


# ── identidad exigida sin prueba de dispositivo: la causa real, nunca «approve» ──


@pytest.mark.parametrize("code", ["DEVICE_IDENTITY_REQUIRED", "PAIRING_REQUIRED"])
@pytest.mark.parametrize(
    ("setup", "cause"),
    [("bad_file", "identity_file_invalid"), ("no_nonce", "challenge_without_nonce")],
)
async def test_identity_required_without_device_reports_the_real_cause(store, caplog, code, setup, cause):
    caplog.set_level(logging.DEBUG, logger="fogar_gateway")
    if setup == "bad_file":
        store.path.write_text("{roto")
    async with FakeOpenClawServer(token=SHARED, remote=True,
                                  challenge_nonce=setup != "no_nonce") as server:
        server.no_device_code = code
        client = make_client(server.port, store)
        for _ in range(2):
            with pytest.raises(OrchestratorDeviceIdentityRequired) as excinfo:
                await client.connect()
            assert excinfo.value.cause == cause
            assert excinfo.value.stable_code == ORCHESTRATOR_DEVICE_IDENTITY_REQUIRED_ERROR
        access = client.access.as_dict()
        assert access["pairing"] == "identity_required" and access["identity_error"] == cause
        assert access["pairing_request_id"] is None
        assert "devices approve" not in caplog.text
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                    and "exige identidad de dispositivo" in r.getMessage()]
        assert len(warnings) == 1
        await client.close()


# ── el acceso es el de ahora: fallos, caídas y cierre lo dejan en `unknown` ──


async def test_access_resets_after_a_failed_connect_keeping_device_id(store):
    async with FakeOpenClawServer(token=SHARED) as server:
        client = make_client(server.port, store)
        await client.connect()
        assert client.access.admin
        device_id = client.access.device_id
        server.token = "otro"  # el siguiente connect se rechaza
        with pytest.raises(OrchestratorRejectedError):
            await client.connect()
        assert client.access.as_dict() == {
            "scopes": None, "admin": False, "auth_method": None, "pairing": "unknown",
            "pairing_request_id": None, "identity_error": None, "device_id": device_id,
        }
        await client.close()


async def test_access_resets_when_pending_turns_into_unreachable(store):
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        client = make_client(server.port, store)
        with pytest.raises(OrchestratorPairingRequired):
            await client.connect()
        assert client.access.pairing == "pending"
    with pytest.raises(OSError):
        await client.connect()  # el servidor ya no está
    assert client.access.pairing == "unknown" and client.access.pairing_request_id is None


async def test_access_resets_on_drop_and_on_close(store):
    async with FakeOpenClawServer(token=SHARED) as server:
        client = make_client(server.port, store)
        dropped = asyncio.Event()
        client.on_disconnect = dropped.set
        await client.connect()
        assert client.access.pairing == "not_required"
        for ws in list(server._conns):
            await ws.close(1011)
        await asyncio.wait_for(dropped.wait(), 5)
        assert client.access.pairing == "unknown" and client.access.scopes is None
        assert client.access.device_id is not None

        await client.connect()
        assert client.access.admin
        await client.close()
        assert client.access.pairing == "unknown" and not client.access.admin


async def test_reconnecting_wrapper_reports_pairing_in_last_error(store):
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        wrapper = ReconnectingOpenClawClient(make_client(server.port, store), "openclaw",
                                             initial_backoff=60)
        try:
            await wrapper.connect()
            assert wrapper.status().last_error == ORCHESTRATOR_PAIRING_REQUIRED_ERROR
        finally:
            await wrapper.close()


# ── acceptHello: token nuevo o distinto se guarda; el mismo conserva sus permisos o los amplía ──


def _hello(token: str, scopes: list[str] | None) -> dict:
    auth: dict = {"deviceToken": token}
    if scopes is not None:
        auth["scopes"] = scopes
    return {"auth": auth}


@pytest.mark.parametrize(
    ("hello_token", "hello_scopes", "expected_scopes"),
    [
        ("devtok-same", ["operator.read", "operator.write"], ("operator.read", "operator.write")),
        ("devtok-same", ["operator.write"], ("operator.read",)),  # no se estrecha
        ("devtok-same", None, ("operator.read",)),
        ("devtok-new", ["operator.write"], ("operator.write",)),
        ("devtok-new", None, None),  # desconocidos: null, no []
    ],
)
async def test_accept_hello(store, hello_token, hello_scopes, expected_scopes):
    identity = store.load_or_create()
    store.save_token("127.0.0.1:1", "devtok-same", ["operator.read"])
    client = make_client(1, store)
    client.gateway_info = GatewayInfo("main", 15000, 1, scopes=hello_scopes)
    stored = StoredDeviceToken("devtok-same", ("operator.read",), "127.0.0.1:1")
    plan = _AuthPlan("device_token", "devtok-same", ["operator.read"], identity, stored)
    await client._accept_hello(_hello(hello_token, hello_scopes), plan)
    after = store.token_for("127.0.0.1:1")
    assert after.token == hello_token and after.scopes == expected_scopes
    assert json.loads(store.path.read_text())["device_token"]["gateway"] == "127.0.0.1:1"
    assert client.access.scopes == hello_scopes and client.access.pairing == "paired"


async def test_broadened_scopes_are_requested_on_the_next_connect(store):
    """Ampliación aprobada que conserva el token: el siguiente connect pide los permisos nuevos."""
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        client = make_client(server.port, store)
        identity = store.load_or_create()
        server.paired[identity.device_id] = ALL_SCOPES
        server.device_tokens[identity.device_id] = ("devtok-kept", ALL_SCOPES)
        gateway = f"127.0.0.1:{server.port}"
        store.save_token(gateway, "devtok-kept", ["operator.read"])
        await connect_once(client)  # con el token guardado pide sus permisos: solo read
        assert server.connects[-1]["scopes"] == ["operator.read"]
        # Un connect con el compartido pide los tres; OpenClaw reutiliza el mismo token y
        # hello-ok anuncia los tres: se guardan sin cambiar el token.
        await connect_once(make_client(server.port, store, use_device_token=False))
        assert store.token_for(gateway) == StoredDeviceToken("devtok-kept", tuple(ALL_SCOPES), gateway)
        await connect_once(client)
        assert server.connects[-1]["auth"]["deviceToken"] == "devtok-kept"
        assert server.connects[-1]["scopes"] == ALL_SCOPES


async def test_file_io_runs_off_the_event_loop(store, monkeypatch):
    calls = []
    real = asyncio.to_thread

    async def spy(fn, *a, **kw):
        calls.append(getattr(fn, "__name__", "?"))
        return await real(fn, *a, **kw)

    monkeypatch.setattr(client_mod.asyncio, "to_thread", spy)
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        client = make_client(server.port, store)
        with pytest.raises(OrchestratorPairingRequired):
            await client.connect()
        server.approve()
        await connect_once(client)
    assert calls.count("load_for") == 2 and "accept_token" in calls
