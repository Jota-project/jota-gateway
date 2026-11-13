import threading

from fogar_gateway import access
from fogar_gateway.access import Route, collect, configured_public_urls, recommend


def test_recommend_order():
    rs = recommend(
        [
            Route("lan", "http://l/", False),
            Route("tailscale_https", "https://t/", True, "ok"),
            Route("public", "https://p/", True, "protegida"),
        ]
    )
    assert [r.kind for r in rs] == ["public", "tailscale_https", "lan"]
    assert [r.recommended for r in rs] == [True, False, False]


def test_public_ok_beats_public_protegida():
    rs = recommend(
        [Route("public", "https://a/", True, "protegida"), Route("public", "https://b/", True, "ok")]
    )
    assert [r.url for r in rs] == ["https://b/", "https://a/"] and rs[0].recommended


def test_unreachable_public_not_recommended():
    rs = recommend([Route("public", "https://p/", True, "inalcanzable"), Route("lan", "http://l/", False)])
    assert [r.kind for r in rs] == ["lan", "public"]
    assert [r.recommended for r in rs] == [True, False]


def test_no_recommendable_route():
    rs = recommend([Route("public", "https://p/", True, "otro_gateway")])
    assert not any(r.recommended for r in rs)


def test_collect_docker_only_public(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no debe llamarse")

    monkeypatch.setattr(access, "tailscale_routes", boom)
    monkeypatch.setattr(access, "lan_route", boom)
    monkeypatch.setattr(access, "verify", lambda url, iid: ("ok", ""))
    rs = collect("0.0.0.0", 8004, ["https://p/"], "iid", docker=True)
    assert [r.kind for r in rs] == ["public"] and rs[0].recommended and rs[0].status == "ok"


def test_collect_lan_never_verified(monkeypatch):
    seen: list[str] = []
    lock = threading.Lock()

    def fake_verify(url, iid):
        with lock:
            seen.append(url)
        return "ok", ""

    monkeypatch.setattr(access, "verify", fake_verify)
    monkeypatch.setattr(access, "tailscale_routes", lambda b, p, exe="tailscale": [Route("tailscale", "http://t:8004/", False)])
    monkeypatch.setattr(access, "lan_route", lambda b, p: Route("lan", "http://192.168.1.2:8004/", False))
    rs = collect("0.0.0.0", 8004, ["https://p/"], "iid", docker=False)
    assert sorted(seen) == ["http://t:8004/", "https://p/"]
    assert [r.kind for r in rs] == ["public", "tailscale", "lan"]
    assert rs[-1].status == "sin_verificar"


def test_collect_never_raises_on_verify_error(monkeypatch):
    def boom(url, iid):
        raise RuntimeError("x")

    monkeypatch.setattr(access, "verify", boom)
    monkeypatch.setattr(access, "tailscale_routes", lambda b, p, exe="tailscale": [])
    monkeypatch.setattr(access, "lan_route", lambda b, p: Route("lan", "http://l/", False))
    rs = collect("0.0.0.0", 8004, ["https://p/"], "iid", docker=False)
    assert rs[0].kind == "lan" and rs[0].recommended
    assert rs[1].status == "inalcanzable"


def test_configured_public_urls_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    (tmp_path / "config.env").write_text("PUBLIC_URLS=https://file.example\n")
    monkeypatch.setenv("PUBLIC_URLS", "https://env.example, nope, https://b.example/x")
    assert configured_public_urls() == ["https://env.example/"]


def test_configured_public_urls_reads_config_env_fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    monkeypatch.delenv("PUBLIC_URLS", raising=False)
    assert configured_public_urls() == []
    (tmp_path / "config.env").write_text("PUBLIC_URLS=https://file.example\n")
    assert configured_public_urls() == ["https://file.example/"]


def test_verify_error_logs_only_type(monkeypatch, caplog):
    def boom(url, iid):
        raise RuntimeError("secreto-user:pass")

    monkeypatch.setattr(access, "verify", boom)
    r = Route("public", "https://u:pass@p/", True)
    with caplog.at_level("WARNING", logger="fogar_gateway.access"):
        access._verify_route(r, "iid")
    assert "RuntimeError" in caplog.text
    assert "secreto" not in caplog.text and "pass" not in caplog.text


def test_configured_public_urls_unreadable_config(monkeypatch, tmp_path):
    import dotenv

    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    monkeypatch.delenv("PUBLIC_URLS", raising=False)

    def deny(*a, **k):
        raise PermissionError("no")

    monkeypatch.setattr(dotenv, "dotenv_values", deny)
    assert configured_public_urls() == []
