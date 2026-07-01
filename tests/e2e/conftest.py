"""Fixtures for tests/e2e — hits the real, already-running production
jota-gateway + real OpenClaw. Every test here is marked e2e_real and is
excluded by default (see pytest.ini addopts). Run explicitly with:

    PYTHONPATH=. pytest tests/e2e -m e2e_real -v
"""
import os

import pytest

from src.core.config import settings

GATEWAY_HTTP_URL = os.environ.get("E2E_GATEWAY_HTTP_URL", "http://127.0.0.1:8004")
GATEWAY_WS_URL = os.environ.get("E2E_GATEWAY_WS_URL", "ws://127.0.0.1:8004/ws/stream")


def pytest_collection_modifyitems(items):
    """Force-applies e2e_real to every test collected under tests/e2e/, so a new
    test file can never accidentally skip the default-exclusion safety net by
    forgetting its own marker. (pytestmark in a conftest.py would NOT do this —
    it only marks tests within the conftest's own module, which has none.)"""
    for item in items:
        if "tests/e2e/" in item.nodeid:
            item.add_marker(pytest.mark.e2e_real)


@pytest.fixture(scope="session")
def e2e_agent() -> str:
    """The dedicated OpenClaw test agent — never a production agent."""
    agent = os.environ.get("E2E_TEST_AGENT")
    if not agent:
        pytest.skip(
            "E2E_TEST_AGENT no está definida — obligatoria para tests/e2e "
            "(debe apuntar a un agente de test dedicado en OpenClaw)."
        )
    return agent


@pytest.fixture(scope="session")
def admin_headers() -> dict:
    if not settings.ADMIN_TOKEN:
        pytest.skip("ADMIN_TOKEN no configurado en el entorno — obligatorio para tests/e2e.")
    return {"X-Admin-Token": settings.ADMIN_TOKEN}
