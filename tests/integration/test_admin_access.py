"""GET /admin/access: las vías para llegar al gateway, con caché de 15 s."""

import pytest

from fogar_gateway import access as access_mod
from fogar_gateway.api import admin_routes, pairing_routes

ROUTE = access_mod.Route("lan", "http://192.168.1.5:8004", False, "sin_verificar", "", True)


@pytest.fixture()
def calls(monkeypatch):
    admin_routes._access_cache = None
    seen: list[tuple] = []

    def fake(*args, **kwargs):
        seen.append(args)
        return [ROUTE]

    monkeypatch.setattr(access_mod, "collect", fake)
    yield seen
    admin_routes._access_cache = None


def test_admin_access_requires_token(client):
    assert client.get("/admin/access", headers={"x-admin-token": "wrong"}).status_code == 401


def test_admin_access_lists_routes(client, admin_headers, calls):
    r = client.get("/admin/access", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["docker"], bool)
    assert body["routes"] == [ROUTE.as_dict()] and body["routes"][0]["recommended"] is True


def test_admin_access_cached(client, admin_headers, calls):
    client.get("/admin/access", headers=admin_headers)
    client.get("/admin/access", headers=admin_headers)
    assert len(calls) == 1


def test_admin_access_passes_instance_id(client, admin_headers, calls):
    client.get("/admin/access", headers=admin_headers)
    assert calls[0][3] == pairing_routes.INSTANCE_ID
