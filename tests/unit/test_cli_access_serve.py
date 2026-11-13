"""free_serve_port y enable_serve: activar `tailscale serve` sin pisar nada."""

import pytest

from fogar_gateway.cli.access import enable_serve, free_serve_port


@pytest.fixture
def make_ts(tmp_path):
    def make(stdout: str = "", stderr: str = "", code: int = 0, record: bool = False) -> str:
        script = tmp_path / "tailscale"
        rec = f'printf "%s\\n" "$@" > {tmp_path}/argv\n' if record else ""
        script.write_text(f"#!/bin/sh\n{rec}echo '{stdout}'\necho '{stderr}' >&2\nexit {code}\n")
        script.chmod(0o755)
        return str(script)

    return make


def test_free_port_prefers_443():
    assert free_serve_port({}) == 443
    assert free_serve_port(None) == 443


def test_free_port_falls_back_8443():
    assert free_serve_port({"TCP": {"443": {"HTTPS": True}}}) == 8443


def test_free_port_none_when_both_taken():
    assert free_serve_port({"TCP": {"443": {}, "8443": {}}}) is None


def test_enable_serve_ok(make_ts):
    assert enable_serve(443, 8004, make_ts()) == ("ok", "")


def test_enable_serve_permission(make_ts):
    exe = make_ts(stderr="Access denied: serve config denied", code=1)
    assert enable_serve(443, 8004, exe) == ("permiso", "")


def test_enable_serve_operator_flag(make_ts):
    assert enable_serve(443, 8004, make_ts(stderr="use sudo tailscale set --operator=$USER", code=1)) == ("permiso", "")


def test_enable_serve_https_off(make_ts):
    exe = make_ts(stdout="Serve is not enabled. Visit:\nhttps://login.tailscale.com/f/serve?node=x\n", code=1)
    assert enable_serve(443, 8004, exe) == ("https_off", "https://login.tailscale.com/f/serve?node=x")


def test_enable_serve_other_failure(make_ts):
    assert enable_serve(443, 8004, make_ts(stderr="boom", code=2)) == ("error", "exit 2")


def test_enable_serve_missing_binary():
    assert enable_serve(443, 8004, "no-such-tailscale-xyz") == ("error", "FileNotFoundError")


def test_enable_serve_args(make_ts, tmp_path):
    enable_serve(8443, 8004, make_ts(record=True))
    assert (tmp_path / "argv").read_text().split() == ["serve", "--bg", "--https=8443", "http://127.0.0.1:8004"]
