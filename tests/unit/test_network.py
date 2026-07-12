import pytest

from src.core.config import settings
from src.core.network import is_trusted_origin, is_trusted_proxy, resolve_client_ip


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, host, headers=None):
        self.client = _FakeClient(host)
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _reset_network_settings(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_LOOPBACK", True)
    monkeypatch.setattr(settings, "TRUSTED_NETWORKS", "")
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,::1")


def test_loopback_trusted_by_default():
    assert is_trusted_origin("127.0.0.1") is True
    assert is_trusted_origin("::1") is True


def test_loopback_untrusted_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_LOOPBACK", False)
    assert is_trusted_origin("127.0.0.1") is False


def test_ip_in_trusted_networks_is_trusted(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_NETWORKS", "192.168.1.0/24")
    assert is_trusted_origin("192.168.1.42") is True


def test_ip_outside_trusted_networks_is_untrusted(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_NETWORKS", "192.168.1.0/24")
    assert is_trusted_origin("203.0.113.5") is False


def test_malformed_ip_is_untrusted():
    assert is_trusted_origin("testclient") is False


def test_is_trusted_proxy_matches_default_loopback():
    assert is_trusted_proxy("127.0.0.1") is True
    assert is_trusted_proxy("203.0.113.5") is False


def test_resolve_client_ip_honors_x_real_ip_from_trusted_proxy():
    request = _FakeRequest("127.0.0.1", headers={"x-real-ip": "192.168.1.50"})
    assert resolve_client_ip(request) == "192.168.1.50"


def test_resolve_client_ip_ignores_x_real_ip_from_untrusted_peer():
    request = _FakeRequest("203.0.113.5", headers={"x-real-ip": "127.0.0.1"})
    assert resolve_client_ip(request) == "203.0.113.5"


def test_resolve_client_ip_falls_back_to_peer_when_no_header():
    request = _FakeRequest("127.0.0.1")
    assert resolve_client_ip(request) == "127.0.0.1"
