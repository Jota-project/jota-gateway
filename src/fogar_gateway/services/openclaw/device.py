"""
device.py
~~~~~~~~~
Identidad de dispositivo del gateway ante OpenClaw (docs/openclaw-protocol.md, «2026-11-13»).

Una clave Ed25519 por instalación, en `paths.openclaw_device_file()` (JSON, 0600, escrito de forma
atómica), con el token de dispositivo cuando OpenClaw empareja el gateway: el token, sus permisos
y el `host:puerto` del OpenClaw que lo emitió. Cada `connect` firma el reto con el payload v3.

La clave privada y el token son credenciales: nunca van a un log, ni en parte (el token se
correlaciona con `fingerprint_key`). El deviceId es público: es el que enseña
`openclaw devices list`. Arriba solo stdlib: `cryptography` se importa al generar o firmar, y
`fogar-gw doctor` lee el resumen sin cargarla.
"""

from __future__ import annotations

import base64
import binascii
import errno
import hashlib
import json
import logging
import os
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fogar_gateway import paths

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
_TOKEN_MAX_LEN = 4096
_RAW_KEY_LEN = 32
_SCOPE_RE = re.compile(r"[a-z][a-z0-9._:-]{0,63}")
_MAX_SCOPES = 32
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class DeviceFileError(Exception):
    """El fichero de identidad existe pero no se puede leer o no es una identidad válida. No se
    sobrescribe nunca: podría guardar una identidad ya emparejada. El mensaje es fijo."""

    stable_code = "openclaw_device_file_invalid"

    def __init__(self) -> None:
        super().__init__(self.stable_code)


def clean_scopes(value: object) -> list[str]:
    """Permisos que manda OpenClaw (dato no fiable): solo identificadores cortos, sin repetir,
    como mucho _MAX_SCOPES. Cualquier otra cosa se descarta."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for scope in value:
        if isinstance(scope, str) and _SCOPE_RE.fullmatch(scope) and scope not in out:
            out.append(scope)
        if len(out) >= _MAX_SCOPES:
            break
    return out


def b64url(data: bytes) -> str:
    """base64url sin relleno, como `Buffer.toString("base64url")` de Node."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def derive_device_id(public_key_raw: bytes) -> str:
    """deviceId de OpenClaw: SHA-256 en hex de la clave pública Ed25519 en bruto (32 bytes),
    como `deriveDeviceIdFromPublicKey` (dist/device-identity-*.mjs)."""
    return hashlib.sha256(public_key_raw).hexdigest()


def normalize_metadata(value: object) -> str:
    """`normalizeDeviceMetadataForAuth`: recorta y pasa a minúsculas solo A-Z (no Unicode)."""
    if not isinstance(value, str):
        return ""
    return value.strip().translate(_ASCII_LOWER)


def build_auth_payload_v3(
    *,
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: Iterable[str],
    signed_at_ms: int,
    token: str | None,
    nonce: str,
    platform: str | None,
    device_family: str | None = None,
) -> str:
    """Texto que se firma en `connect.params.device.signature`, idéntico a
    `buildDeviceAuthPayloadV3` (`@openclaw/gateway-client` y el servidor 2026.10.5)."""
    return "|".join(
        [
            "v3",
            device_id,
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at_ms),
            token or "",
            nonce,
            normalize_metadata(platform),
            normalize_metadata(device_family),
        ]
    )


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    public_key: str  # clave pública en bruto, base64url (lo que va en `device.publicKey`)
    private_seed: bytes = field(repr=False)  # semilla Ed25519 de 32 bytes: credencial

    @classmethod
    def generate(cls) -> DeviceIdentity:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        seed = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        public = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return cls(derive_device_id(public), b64url(public), seed)

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> DeviceIdentity:
        """Valida la identidad guardada: la clave pública y el deviceId tienen que salir de la
        privada. Si no, DeviceFileError (nunca se regenera encima)."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        try:
            seed = b64url_decode(data["private_key"])
            if len(seed) != _RAW_KEY_LEN:
                raise ValueError("bad seed length")
            public = (
                Ed25519PrivateKey.from_private_bytes(seed)
                .public_key()
                .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            )
        except (KeyError, TypeError, ValueError, binascii.Error):
            raise DeviceFileError() from None
        if data.get("public_key") != b64url(public) or data.get("device_id") != derive_device_id(public):
            raise DeviceFileError()
        return cls(derive_device_id(public), b64url(public), seed)

    def record(self) -> dict[str, Any]:
        return {
            "version": FORMAT_VERSION,
            "device_id": self.device_id,
            "public_key": self.public_key,
            "private_key": b64url(self.private_seed),
        }

    def sign(self, payload: str) -> str:
        """Firma Ed25519 del texto UTF-8, en base64url sin relleno (`signEd25519Payload`)."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        return b64url(Ed25519PrivateKey.from_private_bytes(self.private_seed).sign(payload.encode()))


@dataclass(frozen=True)
class StoredDeviceToken:
    token: str = field(repr=False)  # credencial
    scopes: tuple[str, ...]
    gateway: str  # host:puerto del OpenClaw que lo emitió
    role: str = "operator"


def _token_from_record(value: object) -> StoredDeviceToken | None:
    if not isinstance(value, dict):
        return None
    token, gateway, role = value.get("token"), value.get("gateway"), value.get("role", "operator")
    if not (isinstance(token, str) and 0 < len(token) <= _TOKEN_MAX_LEN):
        return None
    if not isinstance(gateway, str) or not isinstance(role, str):
        return None
    return StoredDeviceToken(token, tuple(clean_scopes(value.get("scopes"))), gateway, role)


class DeviceStore:
    """Lee y escribe `openclaw-device.json`. Cada operación relee el fichero (es pequeño): otro
    proceso del mismo usuario (`fogar-gw onboard` con el gateway en marcha) puede haberlo
    cambiado. Escrituras atómicas (fichero temporal 0600 + rename); la creación no pisa a otra
    simultánea (link exclusivo)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else paths.openclaw_device_file()

    # ── lectura ──────────────────────────────────────────────────────────

    def _read(self) -> dict[str, Any] | None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise DeviceFileError() from None
        try:
            data = json.loads(raw)
        except ValueError:
            raise DeviceFileError() from None
        if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
            raise DeviceFileError()
        return data

    def load_or_create(self) -> DeviceIdentity:
        """La identidad guardada, o una nueva si no hay fichero. DeviceFileError si el fichero
        no vale; OSError si no se puede crear."""
        data = self._read()
        if data is not None:
            return DeviceIdentity.from_record(data)
        identity = DeviceIdentity.generate()
        if self._write(identity.record(), create=True):
            logger.info(
                "OpenClaw: nueva identidad de dispositivo del gateway (device=%s) en %s",
                identity.device_id[:12],
                self.path,
            )
            return identity
        data = self._read()  # otro proceso la creó a la vez: vale la suya
        if data is None:
            raise DeviceFileError()
        return DeviceIdentity.from_record(data)

    def token_for(self, gateway: str) -> StoredDeviceToken | None:
        data = self._read()
        if data is None:
            return None
        stored = _token_from_record(data.get("device_token"))
        return stored if stored is not None and stored.gateway == gateway else None

    def summary(self) -> dict[str, Any]:
        """Lo que se puede enseñar (doctor, sin `cryptography`): nunca la clave ni el token.
        DeviceFileError si el fichero no vale."""
        data = self._read()
        if data is None:
            return {"exists": False}
        device_id = data.get("device_id")
        stored = _token_from_record(data.get("device_token"))
        return {
            "exists": True,
            "device_id": device_id if isinstance(device_id, str) else None,
            "token_gateway": stored.gateway if stored else None,
            "token_scopes": list(stored.scopes) if stored else [],
        }

    # ── escritura ────────────────────────────────────────────────────────

    def save_token(self, gateway: str, token: str, scopes: Iterable[str], role: str = "operator") -> None:
        data = self._read()
        if data is None:
            raise DeviceFileError()  # sin identidad no hay token que guardar
        DeviceIdentity.from_record(data)  # no se reescribe un fichero que no vale
        data["device_token"] = {
            "token": token,
            "scopes": clean_scopes(list(scopes)),
            "role": role,
            "gateway": gateway,
        }
        self._write(data, create=False)

    def clear_token(self, gateway: str) -> bool:
        """Borra el token de `gateway` (si es el guardado). True si había uno."""
        data = self._read()
        if data is None:
            return False
        stored = _token_from_record(data.get("device_token"))
        if stored is None or stored.gateway != gateway:
            return False
        DeviceIdentity.from_record(data)
        del data["device_token"]
        self._write(data, create=False)
        return True

    def _write(self, data: dict[str, Any], *, create: bool) -> bool:
        """Escribe `data` en un temporal 0600 del mismo directorio y lo pone en su sitio:
        `create` con link exclusivo (False si ya existe), si no con rename atómico."""
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = directory / f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        body = (json.dumps(data, indent=2) + "\n").encode()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            if create:
                try:
                    os.link(tmp, self.path)
                except FileExistsError:
                    return False
                except OSError as e:
                    # Sistemas de ficheros sin enlaces duros: sin la garantía de exclusividad.
                    if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV):
                        raise
                    if self.path.exists():
                        return False
                    os.replace(tmp, self.path)
            else:
                os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        _fsync_dir(directory)
        return True


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as e:
        logger.debug("fsync del directorio de la identidad omitido (%s)", type(e).__name__)
        return
    try:
        os.fsync(fd)
    except OSError as e:
        logger.debug("fsync del directorio de la identidad omitido (%s)", type(e).__name__)
    finally:
        os.close(fd)
