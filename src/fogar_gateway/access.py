"""Vías de acceso al gateway. Solo stdlib arriba: lo importan `core/config.py` y el CLI."""

from urllib.parse import urlsplit


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
            host = port = None
        if (
            u.scheme != "https"
            or not host
            or u.username is not None
            or u.password is not None
            or u.path not in ("", "/")
            or u.query
            or u.fragment
        ):
            raise ValueError(
                f"PUBLIC_URLS: «{item}» no es válida; usa https://host[:puerto] sin ruta, "
                "parámetros ni usuario"
            )
        urls.append(f"https://{host}{f':{port}' if port else ''}/")
    return urls
