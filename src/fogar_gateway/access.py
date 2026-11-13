"""Vías de acceso al gateway. Solo stdlib arriba: lo importan `core/config.py` y el CLI."""

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
