"""Identidad de dispositivo ante OpenClaw (M19): payload v3, firma, deviceId y el fichero 0600."""

import json
import os
import stat

import pytest
from cryptography.hazmat.primitives.serialization import load_der_public_key

from fogar_gateway import paths
from fogar_gateway.services.openclaw import device as device_mod
from fogar_gateway.services.openclaw.device import (
    DeviceFileError,
    DeviceIdentity,
    DeviceStore,
    b64url,
    b64url_decode,
    build_auth_payload_v3,
    clean_scopes,
    derive_device_id,
    normalize_metadata,
)

# RFC 8032 §7.1, TEST 1: semilla, clave pública y firma del mensaje vacío.
RFC_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
RFC_PUBLIC = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
RFC_SIG_EMPTY = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)
# sha256(RFC_PUBLIC) en hex: lo que da `deriveDeviceIdFromPublicKey` de OpenClaw.
RFC_DEVICE_ID = "21fe31dfa154a261626bf854046fd2271b7bed4b6abe45aa58877ef47f9721b9"
# Prefijo SPKI con el que OpenClaw convierte la clave en bruto (`createEd25519PublicKey`).
SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def rfc_identity() -> DeviceIdentity:
    return DeviceIdentity.from_record({
        "version": 1,
        "device_id": RFC_DEVICE_ID,
        "public_key": b64url(RFC_PUBLIC),
        "private_key": b64url(RFC_SEED),
    })


# ── payload v3, firma y deviceId ─────────────────────────────────────────


def test_payload_v3_matches_reference_layout():
    payload = build_auth_payload_v3(
        device_id="dev",
        client_id="gateway-client",
        client_mode="backend",
        role="operator",
        scopes=["operator.read", "operator.write", "operator.admin"],
        signed_at_ms=1731456000123,
        token="tok",
        nonce="n-1",
        platform="  LiNuX ",
        device_family=None,
    )
    assert payload == (
        "v3|dev|gateway-client|backend|operator|operator.read,operator.write,operator.admin|"
        "1731456000123|tok|n-1|linux|"
    )


def test_payload_v3_without_token_or_scopes_keeps_empty_fields():
    payload = build_auth_payload_v3(
        device_id="d", client_id="c", client_mode="m", role="r", scopes=[], signed_at_ms=0,
        token=None, nonce="n", platform=None, device_family="Phone",
    )
    assert payload == "v3|d|c|m|r||0||n||phone"


def test_metadata_lowercases_only_ascii_like_openclaw():
    assert normalize_metadata(" ÀLinux ") == "Àlinux"  # `[A-Z]` del real: lo demás, intacto
    assert normalize_metadata(None) == ""


def test_rfc8032_vector_seed_public_key_and_signature():
    identity = rfc_identity()
    assert b64url_decode(identity.public_key) == RFC_PUBLIC
    assert b64url_decode(identity.sign("")) == RFC_SIG_EMPTY


def test_device_id_is_sha256_of_raw_public_key():
    assert derive_device_id(RFC_PUBLIC) == RFC_DEVICE_ID
    assert rfc_identity().device_id == RFC_DEVICE_ID


def test_signature_verifies_like_the_server():
    identity = DeviceIdentity.generate()
    payload = "v3|x|gateway-client|backend|operator|operator.read|1|t|n|linux|"
    signature = identity.sign(payload)
    assert "=" not in signature and "=" not in identity.public_key  # base64url sin relleno
    raw = b64url_decode(identity.public_key)
    assert len(raw) == 32 and derive_device_id(raw) == identity.device_id
    load_der_public_key(SPKI_PREFIX + raw).verify(b64url_decode(signature), payload.encode())


def test_identity_repr_has_no_private_key():
    identity = rfc_identity()
    assert b64url(RFC_SEED) not in repr(identity) and RFC_SEED.hex() not in repr(identity)


def test_clean_scopes_drops_junk():
    assert clean_scopes(["operator.read", "operator.read", 3, "X Y", "a" * 80, "operator.admin"]) == [
        "operator.read", "operator.admin"
    ]
    assert clean_scopes("operator.admin") == []


# ── fichero de identidad ───────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "base" / "openclaw-device.json")


def test_default_path_hangs_from_the_base(monkeypatch, tmp_path):
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    expected = tmp_path / "data" / "openclaw-device.json"
    assert DeviceStore().path == expected == paths.openclaw_device_file()


def test_created_once_with_0600_and_reused(store):
    first = store.load_or_create()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert store.load_or_create() == first
    data = json.loads(store.path.read_text())
    assert data["device_id"] == first.device_id and data["public_key"] == first.public_key
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]  # sin temporales


def test_concurrent_creation_keeps_the_first_identity(store, monkeypatch):
    other = DeviceStore(store.path).load_or_create()
    store.path.unlink()
    real_generate = DeviceIdentity.generate

    def generate_while_another_process_writes():
        DeviceStore(store.path)._write(other.record(), create=True)  # el otro proceso gana
        return real_generate()

    monkeypatch.setattr(DeviceIdentity, "generate", staticmethod(generate_while_another_process_writes))
    assert store.load_or_create() == other


def test_failed_write_leaves_the_file_untouched(store, monkeypatch):
    store.load_or_create()
    before = store.path.read_bytes()

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(device_mod.os, "replace", boom)
    with pytest.raises(OSError):
        store.save_token("127.0.0.1:18789", "devtok-1", ["operator.read"])
    assert store.path.read_bytes() == before
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]


@pytest.mark.parametrize("content", [b"{not json", b"[]", b'{"version": 9}'])
def test_unusable_file_is_an_error_and_never_overwritten(store, content):
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(content)
    with pytest.raises(DeviceFileError):
        store.load_or_create()
    assert store.path.read_bytes() == content


def test_public_key_not_matching_the_private_key_is_an_error(store):
    record = DeviceIdentity.generate().record()
    record["public_key"] = b64url(RFC_PUBLIC)
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps(record))
    with pytest.raises(DeviceFileError):
        store.load_or_create()


def test_device_token_is_kept_per_openclaw_address(store):
    identity = store.load_or_create()
    store.save_token("10.0.0.5:18789", "devtok-SECRET", ["operator.read", "operator.admin"])
    assert store.token_for("127.0.0.1:18789") is None
    stored = store.token_for("10.0.0.5:18789")
    assert stored is not None and stored.token == "devtok-SECRET"
    assert stored.scopes == ("operator.read", "operator.admin")
    assert "devtok-SECRET" not in repr(stored)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load_or_create() == identity  # guardar el token no toca la clave
    assert store.clear_token("127.0.0.1:18789") is False
    assert store.clear_token("10.0.0.5:18789") is True
    assert store.token_for("10.0.0.5:18789") is None


def test_summary_never_carries_secrets(store):
    identity = store.load_or_create()
    store.save_token("h:1", "devtok-SECRET", ["operator.read"])
    summary = store.summary()
    assert summary == {
        "exists": True,
        "device_id": identity.device_id,
        "token_gateway": "h:1",
        "token_scopes": ["operator.read"],
    }
    text = json.dumps(summary)
    assert "devtok-SECRET" not in text and b64url(identity.private_seed) not in text


def test_summary_without_file(store):
    assert store.summary() == {"exists": False}


def test_save_token_without_identity_is_an_error(store):
    with pytest.raises(DeviceFileError):
        store.save_token("h:1", "t", [])


def test_umask_does_not_widen_the_file(store):
    old = os.umask(0)
    try:
        store.load_or_create()
    finally:
        os.umask(old)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_device_module_top_level_is_stdlib_only():
    import subprocess
    import sys

    code = ("import sys, fogar_gateway.services.openclaw.device; "
            "print('cryptography' in sys.modules)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "False"
