"""
web_routes.py
~~~~~~~~~~~~~
La web del gateway (M18, 17a): el build de `web/` servido en `/`, desde el propio paquete.

- `WEB_DIR` cuelga del paquete (`fogar_gateway/web/`), nunca del directorio de trabajo. La copia
  del build la hace el empaquetado (release y Docker); en un clon sin build, `/` da 404 y el
  gateway funciona igual.
- `assets/*` lleva hash en el nombre: caché de un año e `immutable`. Lo demás (`index.html`,
  el favicon), `no-cache`.
- Cualquier GET que no sea un fichero ni una ruta de la API devuelve `index.html`: son las rutas
  de Vue (`/clientes/…`). Los prefijos de la API nunca caen aquí: siguen con su 404 en JSON.
- Las cabeceras de seguridad (CSP sin excepciones) van solo en las respuestas de la web.

Este router se monta el último en `main.py`: las rutas de la API ganan siempre.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Primer segmento de las rutas que no son de la web (las de la API y las de FastAPI).
API_PREFIXES = frozenset(
    {"admin", "v1", "ws", "healthz", "ready", ".well-known", "pair", "docs", "redoc", "openapi.json"}
)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; "
        "style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
IMMUTABLE = "public, max-age=31536000, immutable"

router = APIRouter()


def web_available() -> bool:
    return (WEB_DIR / "index.html").is_file()


def _file(path: Path, cache: str) -> FileResponse:
    return FileResponse(path, headers={**SECURITY_HEADERS, "Cache-Control": cache})


@router.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def web(path: str) -> FileResponse:
    if path.split("/", 1)[0] in API_PREFIXES or not web_available():
        raise HTTPException(status_code=404)
    root = WEB_DIR.resolve()
    target = (root / path).resolve()
    # `resolve` deshace `..`: lo que salga de la carpeta de la web no se sirve nunca.
    if path and target.is_relative_to(root) and target.is_file():
        return _file(target, IMMUTABLE if path.startswith("assets/") else "no-cache")
    if path.startswith("assets/"):
        # Un asset que ya no existe (build viejo en caché) no es una ruta de Vue.
        raise HTTPException(status_code=404)
    return _file(root / "index.html", "no-cache")
