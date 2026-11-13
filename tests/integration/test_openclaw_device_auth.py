"""El gateway como dispositivo de OpenClaw (M19) contra el OpenClaw falso real: firma v3 en cada
`connect`, camino local sin emparejar, `PAIRING_REQUIRED`, token de dispositivo y su rechazo."""

import json
import logging
import stat

import pytest

from fogar_gateway.services.openclaw import client as client_mod
from fogar_gateway.services.openclaw.client import (
    ORCHESTRATOR_PAIRING_REQUIRED_ERROR,
    OpenClawClient,
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


def make_client(port: int, store: DeviceStore, token: str = SHARED) -> OpenClawClient:
    turns = TurnRegistry()
    return OpenClawClient(
        "127.0.0.1", port, token, turns, FrameDispatcher(turns, ClientRegistry()), device_store=store
    )


async def connect_once(client: OpenClawClient):
    try:
        return await client.connect()
    finally:
        await client.close()


def stored_token(store: DeviceStore, port: int) -> str | None:
    stored = store.token_for(f"127.0.0.1:{port}")
    return stored.token if stored else None


async def test_local_connect_signs_and_gets_admin_without_pairing(store):
    async with FakeOpenClawServer(token=SHARED) as server:
        client = make_client(server.port, store)
        info = await connect_once(client)
        params = server.connects[0]
        assert params["scopes"] == ALL_SCOPES
        assert params["auth"] == {"token": SHARED}
        device = params["device"]  # el fake ya validó la firma: si no, el connect fallaría
        identity = store.load_or_create()
        assert device["id"] == identity.device_id and device["publicKey"] == identity.public_key
        assert info.scopes == ALL_SCOPES and info.admin
        assert client.access.as_dict() == {
            "scopes": ALL_SCOPES,
            "admin": True,
            "auth_method": "shared_token",
            "pairing": "not_required",
            "pairing_request_id": None,
            "device_id": identity.device_id,
        }
        assert stored_token(store, server.port) is None  # en local no hay emparejamiento
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


async def test_challenge_without_nonce_connects_without_device(store):
    async with FakeOpenClawServer(token=SHARED, challenge_nonce=False) as server:
        client = make_client(server.port, store)
        await connect_once(client)
        assert "device" not in server.connects[0]
        assert not store.path.exists()
        assert client.access.pairing == "not_required" and client.access.admin


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
                await connect_once(client)
            assert excinfo.value.request_id == "req-1"
            assert excinfo.value.stable_code == ORCHESTRATOR_PAIRING_REQUIRED_ERROR
        assert client.access.pairing == "pending"
        assert client.access.pairing_request_id == "req-1"
        assert client.access.scopes is None and not client.access.admin
        warnings = [r for r in caplog.records
                    if r.levelno == logging.WARNING and "openclaw devices approve req-1" in r.getMessage()]
        assert len(warnings) == 1

        server.approve("req-1")
        info = await connect_once(client)
        token = stored_token(store, server.port)
        assert token and token.startswith("devtok-")
        assert info.admin and client.access.pairing == "paired"
        assert client.access.auth_method == "shared_token" and client.access.pairing_request_id is None
        assert store.token_for(f"127.0.0.1:{server.port}").scopes == tuple(ALL_SCOPES)

        await connect_once(client)  # a partir de aquí, con el token de dispositivo
        params = server.connects[-1]
        assert params["auth"] == {"token": token, "deviceToken": token}
        assert params["scopes"] == ALL_SCOPES  # los guardados con el token
        assert client.access.auth_method == "device_token" and client.access.pairing == "paired"
        assert SHARED not in caplog.text and token not in caplog.text


async def test_device_token_works_after_the_shared_token_changes(store):
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        client = make_client(server.port, store)
        with pytest.raises(OrchestratorPairingRequired):
            await connect_once(client)
        server.approve()
        await connect_once(client)
        server.token = "rotated-shared-token"
        info = await connect_once(make_client(server.port, store, token=SHARED))
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
        info = await connect_once(client)
        assert server.connections == before + 2  # rechazado y, en otro socket, con el compartido
        assert server.connects[-1]["auth"] == {"token": SHARED}
        assert stored_token(store, server.port) is None
        assert info.admin and client.access.auth_method == "shared_token"
        assert client.access.pairing == "not_required"
        assert "AUTH_DEVICE_TOKEN_MISMATCH" in caplog.text


async def test_revoked_device_token_remote_goes_back_to_pairing(store):
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        client = make_client(server.port, store)
        with pytest.raises(OrchestratorPairingRequired):
            await connect_once(client)
        server.approve()
        await connect_once(client)
        server.revoke(store.load_or_create().device_id)
        with pytest.raises(OrchestratorPairingRequired) as excinfo:
            await connect_once(client)
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


async def test_reconnecting_wrapper_reports_pairing_in_last_error(store):
    async with FakeOpenClawServer(token=SHARED, remote=True) as server:
        wrapper = ReconnectingOpenClawClient(make_client(server.port, store), "openclaw",
                                             initial_backoff=60, max_duration=1)
        try:
            await wrapper.connect()
            assert wrapper.status().last_error == ORCHESTRATOR_PAIRING_REQUIRED_ERROR
        finally:
            await wrapper.close()


# ── acceptHello: token nuevo o distinto se guarda; el mismo conserva sus permisos ──


def _hello(token: str, scopes: list[str]) -> dict:
    return {"auth": {"deviceToken": token, "scopes": scopes}}


@pytest.mark.parametrize(
    ("hello_token", "expected_scopes"),
    [("devtok-same", ("operator.read",)), ("devtok-new", ("operator.read", "operator.write"))],
)
def test_accept_hello_keeps_scopes_of_an_identical_token(store, hello_token, expected_scopes):
    identity = store.load_or_create()
    store.save_token("127.0.0.1:1", "devtok-same", ["operator.read"])
    client = make_client(1, store)
    scopes = ["operator.read", "operator.write"]
    client.gateway_info = GatewayInfo("main", 15000, 1, scopes=scopes)
    stored = StoredDeviceToken("devtok-same", ("operator.read",), "127.0.0.1:1")
    plan = _AuthPlan("device_token", "devtok-same", ["operator.read"], identity, stored)
    client._accept_hello(_hello(hello_token, scopes), plan)
    after = store.token_for("127.0.0.1:1")
    assert after.token == hello_token and after.scopes == expected_scopes
    assert json.loads(store.path.read_text())["device_token"]["gateway"] == "127.0.0.1:1"
