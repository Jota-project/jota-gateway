"""M6f (#218): el lifespan toma el lock de un solo proceso y lo libera al apagar."""

import pytest
from starlette.testclient import TestClient

import src.main as main_module
from src.core.config import settings
from src.core.startup_checks import LOCK_FILENAME, SingleProcessLockError
from src.main import app
from tests.integration.conftest import _configure_app_mocks


@pytest.fixture
def lock_enabled(tmp_path, monkeypatch, mock_services, mock_registry):
    monkeypatch.setattr(settings, "SINGLE_PROCESS_LOCK", True)
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{tmp_path}/gateway.db")
    _configure_app_mocks(mock_registry, monkeypatch)
    return tmp_path


def test_second_app_in_the_same_db_dir_fails_to_start(lock_enabled):
    with TestClient(app):
        assert (lock_enabled / LOCK_FILENAME).exists()
        with pytest.raises(SingleProcessLockError, match="multi-worker"):
            with TestClient(app):
                pass


def test_lock_is_released_on_shutdown(lock_enabled):
    with TestClient(app):
        pass
    with TestClient(app):  # no debe lanzar
        pass


def test_lock_can_be_disabled(lock_enabled, monkeypatch):
    monkeypatch.setattr(settings, "SINGLE_PROCESS_LOCK", False)
    with TestClient(app):
        with TestClient(app):
            pass
    assert not (lock_enabled / LOCK_FILENAME).exists()


def test_startup_warns_about_default_tokens(lock_enabled, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_TOKEN", "")
    monkeypatch.setattr(settings, "OPENCLAW_TOKEN", "changeme")
    seen: list[list[str]] = []
    real = main_module.warn_insecure_tokens
    monkeypatch.setattr(
        main_module, "warn_insecure_tokens", lambda s: seen.append(real(s)) or seen[-1]
    )
    with TestClient(app):
        pass
    assert seen and {"ADMIN_TOKEN", "OPENCLAW_TOKEN"} <= set(seen[0])
