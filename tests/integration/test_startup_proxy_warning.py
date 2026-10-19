"""#216: WARNING al arrancar si TRUST_LOOPBACK está activo y TRUSTED_PROXIES vacío.

Con esa combinación un proxy en el mismo host que no (o mal) envía X-Real-IP convierte a todo
Internet en "loopback" (su X-Real-IP se ignora). Aviso sin datos sensibles.
"""

import logging

import pytest

from src.core.config import settings
from src.core.network import warn_if_open_proxy_config


@pytest.fixture
def empty_proxies(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_LOOPBACK", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")


def test_warns_when_loopback_trusted_and_no_proxies(empty_proxies, caplog):
    with caplog.at_level(logging.WARNING):
        warn_if_open_proxy_config()
    assert any(
        r.levelno == logging.WARNING and "TRUSTED_PROXIES" in r.getMessage()
        for r in caplog.records
    )


def test_no_warning_with_default_proxies(monkeypatch, caplog):
    monkeypatch.setattr(settings, "TRUST_LOOPBACK", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "127.0.0.1,::1")
    with caplog.at_level(logging.WARNING):
        warn_if_open_proxy_config()
    assert not caplog.records


def test_no_warning_when_loopback_not_trusted(monkeypatch, caplog):
    monkeypatch.setattr(settings, "TRUST_LOOPBACK", False)
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", "")
    with caplog.at_level(logging.WARNING):
        warn_if_open_proxy_config()
    assert not caplog.records


@pytest.fixture
def startup_records(empty_proxies):
    """Handler propio: el lifespan corre durante el setup de `client`."""
    records: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _H(level=logging.WARNING)
    log = logging.getLogger("src.core.network")
    log.addHandler(handler)
    yield records
    log.removeHandler(handler)


def test_lifespan_emits_the_warning(startup_records, client):
    # `client` arranca el lifespan real (después de startup_records, por orden de fixtures).
    assert any("TRUSTED_PROXIES" in r.getMessage() for r in startup_records)
