"""#249: run_migrations() desde la app no debe tocar la config de logging del proceso."""

import logging

from jota_gateway.core.config import settings
from jota_gateway.db import database


def test_run_migrations_does_not_alter_root_logging(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{tmp_path / 'log.db'}")
    database._engine = None
    root = logging.getLogger()
    monkeypatch.setattr(root, "level", logging.INFO)
    handlers_before = list(root.handlers)
    level_before = root.level

    database.run_migrations()

    assert root.level == level_before == logging.INFO
    assert root.handlers == handlers_before
    assert logging.getLogger("jota_gateway.services.bridge").isEnabledFor(logging.INFO)
