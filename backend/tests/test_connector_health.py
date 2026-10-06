"""Connector health API reports connection state and the latest sync outcome."""

from uuid import uuid4

import pytest

from app.models import StoreCredential
from app.security import encrypt_secret
from app.services.connector_status import _upsert_connector_status


@pytest.mark.db
def test_health_separates_latest_sync_failure_from_oauth_success(
    client, auth_header, test_store, test_db_session
):
    test_db_session.add(
        StoreCredential(
            id=uuid4(),
            store_id=test_store.id,
            provider="google",
            access_token=encrypt_secret("test-access-token"),
            provider_account_id="customer-123",
        )
    )
    test_db_session.commit()

    _upsert_connector_status(test_db_session, test_store.id, "google", synced=True, success=True)
    _upsert_connector_status(
        test_db_session,
        test_store.id,
        "google",
        synced=True,
        success=False,
        error="Provider timeout",
    )
    _upsert_connector_status(test_db_session, test_store.id, "google", success=True)

    response = client.get(f"/stores/{test_store.id}/connectors/health", headers=auth_header)

    assert response.status_code == 200
    info = response.json()["google"]
    assert info["connected"] is True
    assert info["last_sync_status"] == "error"
    assert info["last_sync_error"] == "Provider timeout"
    assert info["last_error"] is None
    assert info["last_synced_at"] is not None
    assert info["last_success_at"] is not None


@pytest.mark.db
def test_health_reports_oauth_failure_as_disconnected_not_sync_failure(
    client, auth_header, test_store, test_db_session
):
    _upsert_connector_status(
        test_db_session,
        test_store.id,
        "meta",
        success=False,
        error="OAuth consent failed",
    )

    response = client.get(f"/stores/{test_store.id}/connectors/health", headers=auth_header)

    assert response.status_code == 200
    info = response.json()["meta"]
    assert info["connected"] is False
    assert info["last_error"] == "OAuth consent failed"
    assert info["last_synced_at"] is None
    assert info["last_sync_status"] is None
    assert info["last_sync_error"] is None
