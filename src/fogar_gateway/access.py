"""Vías de acceso al gateway. Solo stdlib arriba: lo importan `core/config.py` y el CLI."""

import http.client
import json
import socket
import ssl
import urllib.error
import urllib.request
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
