"""Fixtures for tests/e2e — hits the real, already-running production
jota-gateway + real OpenClaw. Every test here is marked e2e_real and is
excluded by default (see pytest.ini addopts). Run explicitly with:

    PYTHONPATH=. pytest tests/e2e -m e2e_real -v
"""
import os
import secrets

import httpx
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


def _create_test_client(admin_headers: dict, suffix: str) -> dict:
    resp = httpx.post(
        f"{GATEWAY_HTTP_URL}/admin/clients",
        json={
            "name": f"e2e-smoke-{suffix}",
            "client_type": "e2e-test",
            "output_mode": ["text"],
        },
        headers=admin_headers,
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def _delete_test_client(admin_headers: dict, client_id: str) -> None:
    httpx.delete(
        f"{GATEWAY_HTTP_URL}/admin/clients/{client_id}",
        headers=admin_headers,
        timeout=10.0,
    )


@pytest.fixture
def test_client_record(admin_headers):
    """A single ephemeral test client — created before the test, deleted after."""
    record = _create_test_client(admin_headers, secrets.token_hex(4))
    try:
        yield record
    finally:
        _delete_test_client(admin_headers, record["id"])


@pytest.fixture
def test_client_records_x3(admin_headers):
    """Three ephemeral test clients, for the concurrent-sessions scenario."""
    records = [_create_test_client(admin_headers, secrets.token_hex(4)) for _ in range(3)]
    try:
        yield records
    finally:
        for r in records:
            _delete_test_client(admin_headers, r["id"])


DEFAULT_TOOL_PROBE_TEMPLATE = (
    "Usa tu herramienta de eco y repite exactamente el siguiente texto, "
    "sin traducirlo ni modificarlo: {token}"
)


@pytest.fixture(scope="session")
def tool_probe_prompt():
    """Builds a prompt + expected verbatim token for the tool-use smoke test.

    Override the phrasing with E2E_TOOL_PROBE_PROMPT_TEMPLATE if the test
    agent's tool needs a different trigger phrase (must contain '{token}').
    """
    template = os.environ.get("E2E_TOOL_PROBE_PROMPT_TEMPLATE", DEFAULT_TOOL_PROBE_TEMPLATE)
    token = f"E2E-PROBE-{secrets.token_hex(4).upper()}"
    return template.format(token=token), token
