"""Detección de vías: LAN, Tailscale (y serve) y Docker."""

import json
import time

import pytest

from fogar_gateway import access
from fogar_gateway.access import (
    Route,
    in_docker,
    lan_route,
    serve_https_port,
    tailscale_routes,
)

DNS = "gh.tail1.ts.net."


def _serve(p: str = "443", proxy: str = "http://127.0.0.1:8004") -> dict:
    return {"TCP": {p: {"HTTPS": True}}, "Web": {f"gh.tail1.ts.net:{p}": {"Handlers": {"/": {"Proxy": proxy}}}}}


@pytest.fixture
def make_ts(tmp_path):
    def make(state: str = "Running", serve: dict | None = None) -> str:
        status = {"BackendState": state, "Self": {"DNSName": DNS}}
        script = tmp_path / "tailscale"
        script.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = serve ]; then cat <<\'EOF\'\n' + json.dumps(serve or {}) + "\nEOF\n"
            "else cat <<'EOF'\n" + json.dumps(status) + "\nEOF\nfi\n"
        )
        script.chmod(0o755)
        return str(script)

    return make


def test_tailscale_running_with_serve(make_ts):
    exe = make_ts(serve=_serve())
    assert [(r.kind, r.url, r.https) for r in tailscale_routes("0.0.0.0", 8004, exe)] == [
        ("tailscale_https", "https://gh.tail1.ts.net/", True),
        ("tailscale", "http://gh.tail1.ts.net:8004/", False),
    ]


def test_tailscale_serve_other_port(make_ts):
    routes = tailscale_routes("0.0.0.0", 8004, make_ts(serve=_serve("8443", "http://localhost:8004")))
    assert routes[0].url == "https://gh.tail1.ts.net:8443/"


def test_tailscale_serve_points_elsewhere(make_ts):
    routes = tailscale_routes("0.0.0.0", 8004, make_ts(serve=_serve(proxy="http://127.0.0.1:3000")))
    assert [r.kind for r in routes] == ["tailscale"]


def test_tailscale_loopback_bind_only_serve(make_ts):
    routes = tailscale_routes("127.0.0.1", 8004, make_ts(serve=_serve()))
    assert [r.kind for r in routes] == ["tailscale_https"]


def test_tailscale_specific_bind_has_no_http_route(make_ts):
    assert tailscale_routes("192.168.1.5", 8004, make_ts()) == []
    routes = tailscale_routes("192.168.1.5", 8004, make_ts(serve=_serve()))
    assert [r.kind for r in routes] == ["tailscale_https"]


@pytest.mark.parametrize("state", ["Stopped", "NeedsLogin"])
def test_tailscale_not_running(make_ts, state):
    assert tailscale_routes("0.0.0.0", 8004, make_ts(state)) == []


def test_tailscale_hangs(tmp_path):
    script = tmp_path / "tailscale"
    script.write_text("#!/bin/sh\nsleep 10\n")
    script.chmod(0o755)
    t0 = time.monotonic()
    assert tailscale_routes("0.0.0.0", 8004, str(script)) == []
    assert time.monotonic() - t0 < 4


def test_tailscale_bad_json_or_failure(tmp_path):
    script = tmp_path / "tailscale"
    script.write_text("#!/bin/sh\necho no-json\n")
    script.chmod(0o755)
    assert access.tailscale_status(str(script)) is None
    script.write_text("#!/bin/sh\necho '{}'\nexit 1\n")
    assert access.tailscale_status(str(script)) is None


def test_tailscale_missing():
    assert tailscale_routes("0.0.0.0", 8004, "/nope/tailscale") == []


def test_serve_https_port_requires_https_listener():
    serve = _serve()
    serve["TCP"]["443"] = {"HTTPS": False}
    assert serve_https_port(serve, 8004) is None
    assert serve_https_port(None, 8004) is None


def test_lan_route_loopback_none():
    for host in ("127.0.0.1", "::1", "localhost"):
        assert lan_route(host, 8004) is None


def test_lan_route_ipv6():
    assert lan_route("fd00::5", 8004).url == "http://[fd00::5]:8004/"


def test_lan_route_all_interfaces(monkeypatch):
    monkeypatch.setattr(access, "lan_ip", lambda: "192.168.1.5")
    assert lan_route("0.0.0.0", 8004) == Route("lan", "http://192.168.1.5:8004/", False)
    monkeypatch.setattr(access, "lan_ip", lambda: None)
    assert lan_route("", 8004) is None


def test_route_as_dict():
    assert Route("lan", "u", False).as_dict() == {
        "kind": "lan", "url": "u", "https": False,
        "status": "sin_verificar", "detail": "", "recommended": False,
    }


def test_in_docker(tmp_path):
    assert not in_docker(str(tmp_path / "nope"))
    marker = tmp_path / ".dockerenv"
    marker.touch()
    assert in_docker(str(marker))
