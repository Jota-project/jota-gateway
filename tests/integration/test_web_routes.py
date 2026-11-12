"""La web en `/` (M18, 17a): ficheros, vuelta a index.html, caché, cabeceras y la API intacta."""

import pytest

from fogar_gateway.api import web_routes


@pytest.fixture
def web_dir(tmp_path, monkeypatch):
    d = tmp_path / "web"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>Fogar</title>")
    (d / "assets" / "index-abc123.js").write_text("console.log(1)")
    (d / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secreto.txt").write_text("fuera")
    monkeypatch.setattr(web_routes, "WEB_DIR", d)
    return d


def _is_web(r):
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"


def test_root_serves_index_without_cache(client, web_dir):
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>Fogar</title>" in r.text
    assert r.headers["cache-control"] == "no-cache"
    _is_web(r)


@pytest.mark.parametrize("path", ["/clientes", "/clientes/42", "/sesiones/abc"])
def test_vue_routes_fall_back_to_index(client, web_dir, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "<title>Fogar</title>" in r.text


def test_hashed_assets_are_immutable(client, web_dir):
    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    _is_web(r)


def test_missing_asset_is_404_not_index(client, web_dir):
    assert client.get("/assets/viejo-000.js").status_code == 404


def test_root_files_are_not_immutable(client, web_dir):
    r = client.get("/favicon.svg")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_path_traversal_never_leaves_the_web_dir(client, web_dir):
    r = client.get("/%2e%2e/secreto.txt")
    assert "fuera" not in r.text


@pytest.mark.parametrize("path", ["/admin/nada", "/v1/nada", "/ws/nada", "/.well-known/nada", "/pair/x"])
def test_api_prefixes_keep_their_json_404(client, web_dir, path):
    r = client.get(path)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "content-security-policy" not in r.headers


def test_api_routes_still_win(client, web_dir):
    r = client.get("/healthz")
    assert r.json() == {"status": "ok"}
    assert "content-security-policy" not in r.headers


def test_without_build_root_is_404_and_api_works(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web_routes, "WEB_DIR", tmp_path / "no-hay-web")
    assert client.get("/").status_code == 404
    assert client.get("/healthz").status_code == 200


def test_web_dir_hangs_from_the_package_not_the_cwd():
    assert web_routes.WEB_DIR.parent.name == "fogar_gateway"
    assert web_routes.WEB_DIR.is_absolute()
