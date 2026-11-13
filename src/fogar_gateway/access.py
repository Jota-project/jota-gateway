"""Vías de acceso al gateway. Solo stdlib arriba: lo importan `core/config.py` y el CLI."""

import http.client
import ipaddress
import json
import shutil
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Sin eco del valor: podría llevar usuario:contraseña y los errores acaban en logs.
_MSG = (
    "PUBLIC_URLS: una de las URLs no es válida; usa https://host[:puerto] sin ruta, "
    "parámetros ni usuario"
)


def parse_public_urls(raw: str) -> list[str]:
    """CSV de URLs propias del usuario -> `https://host[:port]/` normalizadas. `ValueError` si alguna no vale."""
    urls: list[str] = []
    for item in (p.strip() for p in raw.split(",")):
        if not item:
            continue
        try:
            u = urlsplit(item)
            host, port = u.hostname, u.port
        except ValueError:
            raise ValueError(_MSG) from None
        if (
            u.scheme != "https"
            or not host
            or u.username is not None
            or u.password is not None
            or u.path not in ("", "/")
            or u.query
            or u.fragment
        ):
            raise ValueError(_MSG)
        host = f"[{host}]" if ":" in host else host
        urls.append(f"https://{host}{f':{port}' if port else ''}/")
    return urls


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None  # el 3xx llega como HTTPError: lo clasificamos nosotros


def _http_error(e: urllib.error.HTTPError) -> tuple[str, str]:
    location = urlsplit(e.headers.get("Location") or "").hostname or ""
    cf_header = any(k.lower().startswith("cf-access") for k in e.headers)
    if (300 <= e.code < 400 and (location == "cloudflareaccess.com" or location.endswith(".cloudflareaccess.com"))) or (
        e.code in (401, 403) and cf_header
    ):
        return "protegida", "cloudflare_access"
    return "inalcanzable", f"http_{e.code}"


def _network_error(e: BaseException) -> str:
    reason = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(reason, ssl.SSLError):
        return "tls"
    if isinstance(reason, socket.gaierror):
        return "dns"
    if isinstance(reason, TimeoutError):
        return "timeout"
    return "red"


def verify(url: str, instance_id: str | None, timeout: float = 4.0) -> tuple[str, str]:
    """Comprueba que `url` (raíz con «/» final) llega a ESTE gateway. Nunca lanza por la red."""
    if instance_id is None:
        return "sin_verificar", "gateway_parado"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(f"{url}.well-known/fogar-gateway", timeout=timeout) as r:
            data = json.loads(r.read(65536))
    except urllib.error.HTTPError as e:
        return _http_error(e)
    except ValueError:  # JSON roto o no UTF-8: algo que no es Fogar
        return "inalcanzable", "no_es_fogar"
    except http.client.HTTPException:  # respuesta que no es HTTP válido
        return "inalcanzable", "red"
    except (OSError, urllib.error.URLError) as e:  # frontera de red: DNS, TLS, timeout, conexión
        return "inalcanzable", _network_error(e)
    if not isinstance(data, dict) or data.get("service") != "fogar-gateway":
        return "inalcanzable", "no_es_fogar"
    return ("ok", "") if data.get("instance_id") == instance_id else ("otro_gateway", "")


@dataclass
class Route:
    kind: str  # public | tailscale_https | tailscale | lan
    url: str
    https: bool
    status: str = "sin_verificar"
    detail: str = ""
    recommended: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_ALL_INTERFACES = ("", "0.0.0.0", "::")


def lan_ip() -> str | None:
    """La IP de la LAN: la de la ruta hacia 8.8.8.8 (`connect` en UDP no envía nada); None si
    no hay ruta (una LAN sin salida): mejor decirlo que dar 127.0.0.1."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip: str = s.getsockname()[0]
            return ip
    except OSError:
        return None


def _is_loopback(host: str) -> bool:
    try:
        return host == "localhost" or ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:  # un nombre: se da tal cual
        return False


def lan_route(bind: str, port: int) -> Route | None:
    """La vía por la LAN, o None si el gateway solo escucha en loopback o no hay IP."""
    if _is_loopback(bind):
        return None
    host = lan_ip() if bind in _ALL_INTERFACES else bind
    if not host:
        return None
    host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return Route("lan", f"http://{host}:{port}/", False)


def _tailscale_json(exe: str, *args: str) -> dict[str, Any] | None:
    """`tailscale <args> --json` -> dict; None si no está, falla, tarda más de 3 s o no es JSON."""
    path = shutil.which(exe)
    if path is None:
        return None
    try:
        r = subprocess.run([path, *args, "--json"], timeout=3, capture_output=True, check=False)
        data = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):  # ValueError: JSON roto o no UTF-8
        return None
    return data if isinstance(data, dict) else None


def tailscale_status(exe: str = "tailscale") -> dict[str, Any] | None:
    return _tailscale_json(exe, "status")


def serve_status(exe: str = "tailscale") -> dict[str, Any] | None:
    return _tailscale_json(exe, "serve", "status")


def serve_https_port(serve: dict[str, Any] | None, port: int) -> int | None:
    """Puerto HTTPS de `tailscale serve` cuyo handler «/» hace proxy a este gateway."""
    if not isinstance(serve, dict):
        return None
    targets = (f"http://127.0.0.1:{port}", f"http://localhost:{port}")
    tcp, web = serve.get("TCP"), serve.get("Web")
    if not isinstance(tcp, dict) or not isinstance(web, dict):
        return None
    for hostport, cfg in web.items():
        handlers = cfg.get("Handlers") if isinstance(cfg, dict) else None
        root = handlers.get("/") if isinstance(handlers, dict) else None
        if not isinstance(root, dict) or str(root.get("Proxy", "")).rstrip("/") not in targets:
            continue
        p = str(hostport).rpartition(":")[2]
        listener = tcp.get(p)
        if p.isdigit() and isinstance(listener, dict) and listener.get("HTTPS"):
            return int(p)
    return None


def tailscale_routes(bind: str, port: int, exe: str = "tailscale") -> list[Route]:
    status = tailscale_status(exe)
    if not status or status.get("BackendState") != "Running":
        return []
    me = status.get("Self")
    dns = str(me.get("DNSName", "")).rstrip(".") if isinstance(me, dict) else ""
    if not dns:
        return []
    routes: list[Route] = []
    https_port = serve_https_port(serve_status(exe), port)
    if https_port:
        suffix = "" if https_port == 443 else f":{https_port}"
        routes.append(Route("tailscale_https", f"https://{dns}{suffix}/", True))
    if not _is_loopback(bind):  # escucha en la tailnet: también por HTTP directo
        routes.append(Route("tailscale", f"http://{dns}:{port}/", False))
    return routes


def in_docker(marker: str = "/.dockerenv") -> bool:
    return Path(marker).exists()
