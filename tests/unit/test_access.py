import pytest
from pydantic import ValidationError

from fogar_gateway.access import parse_public_urls
from fogar_gateway.core.config import Settings


def test_parse_public_urls_normalizes():
    assert parse_public_urls(" https://Fogar.Example.com , https://x.ts.net:8443/ ") == [
        "https://fogar.example.com/",
        "https://x.ts.net:8443/",
    ]


def test_parse_public_urls_empty():
    assert parse_public_urls("") == []


@pytest.mark.parametrize(
    "bad",
    [
        "http://a.com",
        "https://a.com/panel",
        "https://a.com/?x=1",
        "https://a.com#f",
        "https://u:p@a.com",
        "a.com",
        "https://",
    ],
)
def test_parse_public_urls_rejects(bad):
    with pytest.raises(ValueError):
        parse_public_urls(bad)


def test_settings_rejects_http_public_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_URLS", "http://a.com")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_keeps_original_public_urls(monkeypatch):
    monkeypatch.setenv("PUBLIC_URLS", "https://A.com")
    assert Settings().PUBLIC_URLS == "https://A.com"
