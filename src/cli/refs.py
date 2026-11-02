"""Resolver un cliente por id, prefijo único de id (≥ 4 caracteres) o nombre. Nunca por clave."""

from typing import Any

from src.cli.api import ApiError

MIN_PREFIX = 4


def _ambiguous(what: str, matches: list[dict[str, Any]]) -> ApiError:
    ids = ", ".join(c["id"] for c in matches)
    return ApiError(f"{what} es ambiguo; usa uno de estos ids: {ids}", 2)


def resolve(clients: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    # Manda el id: exacto, después prefijo, después nombre.
    for c in clients:
        if c["id"] == ref:
            return c
    if len(ref) >= MIN_PREFIX:
        by_prefix = [c for c in clients if c["id"].startswith(ref)]
        if len(by_prefix) > 1:
            raise _ambiguous(f"El prefijo «{ref}»", by_prefix)
        if by_prefix:
            return by_prefix[0]
    by_name = [c for c in clients if c["name"].casefold() == ref.casefold()]
    if len(by_name) > 1:
        raise _ambiguous(f"El nombre «{ref}»", by_name)
    if by_name:
        return by_name[0]
    raise ApiError(f"No hay ningún cliente «{ref}». Mira `jota-gw clients list`.")
