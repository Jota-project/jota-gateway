"""Proves the ephemeral test-client fixtures actually create and clean up
ClientRecords against the real, running Admin API."""

import httpx

from tests.e2e.conftest import GATEWAY_HTTP_URL


def test_single_ephemeral_client_created_and_cleaned_up(test_client_record, admin_headers):
    client_id = test_client_record["id"]
    assert test_client_record["client_type"] == "e2e-test"

    resp = httpx.get(f"{GATEWAY_HTTP_URL}/admin/clients/{client_id}", headers=admin_headers)
    assert resp.status_code == 200, "el cliente efímero debería existir mientras el test corre"


def test_three_ephemeral_clients_created_and_cleaned_up(test_client_records_x3, admin_headers):
    assert len(test_client_records_x3) == 3
    for record in test_client_records_x3:
        resp = httpx.get(f"{GATEWAY_HTTP_URL}/admin/clients/{record['id']}", headers=admin_headers)
        assert resp.status_code == 200


def test_cleanup_actually_deletes(admin_headers):
    """Runs a client through the fixture manually to confirm the finally-block DELETE lands."""
    from tests.e2e.conftest import _create_test_client, _delete_test_client

    record = _create_test_client(admin_headers, "cleanup-check")
    _delete_test_client(admin_headers, record["id"])

    resp = httpx.get(f"{GATEWAY_HTTP_URL}/admin/clients/{record['id']}", headers=admin_headers)
    assert resp.status_code == 404, "el cliente debería haber sido borrado"
