"""Rutas compartidas por el gateway y la CLI (M14c, #295)."""

import pytest
from sqlalchemy.engine import make_url

from fogar_gateway import paths


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    return tmp_path


def test_home_defaults_to_dot_fogar_gateway(monkeypatch, tmp_path):
    monkeypatch.delenv("FOGAR_GATEWAY_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.home() == tmp_path / ".fogar-gateway"


def test_relative_home_is_made_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", "inst")
    assert paths.home() == tmp_path / "inst"
    assert paths.config_file() == tmp_path / "inst" / "config.env"


def test_relative_sqlite_url_hangs_from_home(base):
    url = paths.absolute_database_url("sqlite:///data/gateway.db")
    assert url == f"sqlite:///{base}/data/gateway.db"
    assert paths.sqlite_file("sqlite:///data/gateway.db") == base / "data" / "gateway.db"


@pytest.mark.parametrize("url", [
    "sqlite:////srv/gw/gateway.db",
    "sqlite://",
    "sqlite:///:memory:",
    "postgresql://u:pw@h/db",
])
def test_absolute_memory_and_other_urls_are_untouched(base, url):
    assert paths.absolute_database_url(url) == url


def test_query_and_driver_are_kept(base):
    url = paths.absolute_database_url("sqlite+pysqlite:///data/gateway.db?timeout=5")
    assert url == f"sqlite+pysqlite:///{base}/data/gateway.db?timeout=5"


def test_sqlite_file_is_none_for_memory_and_other_backends(base):
    assert paths.sqlite_file("sqlite://") is None
    assert paths.sqlite_file("sqlite:///:memory:") is None
    assert paths.sqlite_file("postgresql://u:pw@h/db") is None


def test_uri_characters_in_home_reach_sqlalchemy_intact(monkeypatch, tmp_path):
    home = tmp_path / "a#b?c%d e"
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(home))
    url = paths.absolute_database_url("sqlite:///data/gateway.db")
    assert make_url(url).database == str(home / "data" / "gateway.db")
    assert paths.sqlite_file(url) == home / "data" / "gateway.db"
