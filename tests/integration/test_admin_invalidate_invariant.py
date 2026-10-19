"""G-07 (#218): invariante de `db_client.invalidate` en las mutaciones de /admin/clients.

1. Toda mutación que cambia lo que ve `get_session` debe invalidar la caché (si no, una
   sesión revocada sigue resolviéndose hasta 60 s/10 s).
2. `invalidate` va DESPUÉS del commit: antes, un `get_session` concurrente repuebla la
   caché con la fila vieja entre la invalidación y el commit (CLAUDE.md, «Cache pattern»).
"""

import asyncio

import pytest
from sqlalchemy import event

from src.core.exceptions import ClientInactive, ClientNotFound
from src.services.db_client import db_client
from tests.integration.conftest import CLIENT_ID, VALID_KEY


def _warm_cache() -> None:
    asyncio.run(db_client.get_session(VALID_KEY))
    assert VALID_KEY in db_client._session_cache


def test_patch_invalidates_the_session_cache(client, admin_headers, seed_client):
    _warm_cache()

    r = client.patch(f"/admin/clients/{CLIENT_ID}", json={"is_active": False}, headers=admin_headers)
    assert r.status_code == 200

    assert VALID_KEY not in db_client._session_cache
    with pytest.raises(ClientInactive):
        asyncio.run(db_client.get_session(VALID_KEY))


@pytest.mark.parametrize(
    ("method", "path", "body", "expected_status"),
    [
        ("patch", f"/admin/clients/{CLIENT_ID}", {"is_active": False}, 200),
        ("delete", f"/admin/clients/{CLIENT_ID}", None, 204),
        ("post", f"/admin/clients/{CLIENT_ID}/rotate-key", None, 200),
    ],
)
def test_invalidate_runs_after_the_commit(
    client, admin_headers, seed_client, db_engine, monkeypatch, method, path, body, expected_status
):
    order: list[str] = []
    event.listen(db_engine, "commit", lambda conn: order.append("commit"))
    real_invalidate = db_client.invalidate

    def _spy(key):
        order.append("invalidate")
        real_invalidate(key)

    monkeypatch.setattr(db_client, "invalidate", _spy)

    kwargs = {"headers": admin_headers}
    if body is not None:
        kwargs["json"] = body
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == expected_status

    assert "invalidate" in order, "la mutación no invalidó la caché"
    assert "commit" in order
    assert order.index("commit") < order.index("invalidate"), order
    # Y la sesión vieja ya no se resuelve.
    with pytest.raises((ClientInactive, ClientNotFound)):
        asyncio.run(db_client.get_session(VALID_KEY))
