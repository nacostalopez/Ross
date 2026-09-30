"""Tests for the CAPI feedback loop (app/services/capi.py) — sending
confirmed purchases to Meta Conversions API / Google Enhanced Conversions
using the hashed email/phone already on `customers`.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import requests
from fastapi import status

from app.models import CapiEvent, Customer, StoreCredential
from app.security import encrypt_secret
from app.services.capi import send_google_purchase_conversion, send_meta_purchase_event


@pytest.fixture(autouse=True)
def _capi_uses_test_session(request, monkeypatch):
    """send_meta_purchase_event/send_google_purchase_conversion open their
    own SessionLocal() in production (see capi.py's module docstring) —
    that would otherwise point at DATABASE_URL, not the test database. Data
    created by a test (via test_db_session) only lives inside that
    session's own uncommitted outer transaction (see conftest.py's
    SAVEPOINT recipe), so capi.py's send functions must reuse that exact
    session object to see it — a second, independently-connected session
    would see none of it. Reusing the same object means capi.py's own
    db.close() would expunge/detach objects (like the test_store fixture)
    the test still needs afterward, so close() is neutered here; commit()
    is left alone since that's exactly what the SAVEPOINT-restart listener
    is designed to survive."""
    # Only DB-marked tests get a session: requesting test_db_session here
    # unconditionally would drag the database into TestGoogleConsentMode,
    # which calls the connector directly and needs none.
    if request.node.get_closest_marker("db") is None:
        return
    test_db_session = request.getfixturevalue("test_db_session")
    monkeypatch.setattr(test_db_session, "close", lambda: None)
    monkeypatch.setattr("app.services.capi.SessionLocal", lambda: test_db_session)


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_body


def _make_credential(db, store_id, provider, *, enabled=True, destination_id="test-destination"):
    credential = StoreCredential(
        id=uuid4(),
        store_id=store_id,
        provider=provider,
        access_token=encrypt_secret("fake-access-token"),
        capi_enabled=enabled,
        capi_destination_id=destination_id,
    )
    db.add(credential)
    db.commit()
    return credential


def _make_customer(db, store_id, *, email_hash="a" * 64, phone_hash=None):
    customer = Customer(id=uuid4(), store_id=store_id, email_hash=email_hash, phone_hash=phone_hash)
    db.add(customer)
    db.commit()
    return customer


def _order_row(customer_id=None, gross_amount=100.0, currency="USD"):
    return {
        "time": "2026-01-05T00:00:00+00:00",
        "gross_amount": gross_amount,
        "currency": currency,
        "customer_id": customer_id,
    }


@pytest.mark.db
class TestSendMetaPurchaseEvent:
    def test_noop_without_credential(self, test_db_session, test_store, monkeypatch):
        calls = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append((a, k)) or _FakeResponse())

        send_meta_purchase_event(test_store.id, "order-1", _order_row(customer_id=uuid4()))

        assert calls == []
        assert test_db_session.query(CapiEvent).count() == 0

    def test_noop_when_capi_disabled(self, test_db_session, test_store, monkeypatch):
        _make_credential(test_db_session, test_store.id, "meta", enabled=False)
        calls = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append((a, k)) or _FakeResponse())

        send_meta_purchase_event(test_store.id, "order-1", _order_row(customer_id=uuid4()))

        assert calls == []

    def test_noop_without_customer(self, test_db_session, test_store, monkeypatch):
        _make_credential(test_db_session, test_store.id, "meta")
        calls = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append((a, k)) or _FakeResponse())

        send_meta_purchase_event(test_store.id, "order-1", _order_row(customer_id=None))

        assert calls == []
        assert test_db_session.query(CapiEvent).count() == 0

    def test_sends_hashed_user_data_and_records_success(self, test_db_session, test_store, monkeypatch):
        _make_credential(test_db_session, test_store.id, "meta", destination_id="pixel-123")
        customer = _make_customer(test_db_session, test_store.id, email_hash="e" * 64, phone_hash="p" * 64)
        calls = []

        def fake_post(url, params=None, json=None, **kwargs):
            calls.append((url, params, json))
            return _FakeResponse(200)

        monkeypatch.setattr(requests, "post", fake_post)

        send_meta_purchase_event(test_store.id, "order-1", _order_row(customer_id=customer.id))

        assert len(calls) == 1
        url, params, payload = calls[0]
        assert "pixel-123/events" in url
        event = payload["data"][0]
        assert event["event_name"] == "Purchase"
        assert event["user_data"]["em"] == ["e" * 64]
        assert event["user_data"]["ph"] == ["p" * 64]
        assert event["custom_data"] == {"value": 100.0, "currency": "USD"}

        capi_event = test_db_session.query(CapiEvent).filter_by(store_id=test_store.id, order_id="order-1").one()
        assert capi_event.status == "sent"
        assert capi_event.error is None

    def test_already_sent_is_not_resent(self, test_db_session, test_store, monkeypatch):
        _make_credential(test_db_session, test_store.id, "meta")
        customer = _make_customer(test_db_session, test_store.id)
        calls = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or _FakeResponse())

        row = _order_row(customer_id=customer.id)
        send_meta_purchase_event(test_store.id, "order-1", row)
        send_meta_purchase_event(test_store.id, "order-1", row)

        assert len(calls) == 1

    def test_http_failure_is_recorded_and_does_not_raise(self, test_db_session, test_store, monkeypatch):
        _make_credential(test_db_session, test_store.id, "meta")
        customer = _make_customer(test_db_session, test_store.id)

        def failing_post(*a, **k):
            return _FakeResponse(500)

        monkeypatch.setattr(requests, "post", failing_post)

        send_meta_purchase_event(test_store.id, "order-1", _order_row(customer_id=customer.id))

        capi_event = test_db_session.query(CapiEvent).filter_by(store_id=test_store.id, order_id="order-1").one()
        assert capi_event.status == "failed"
        assert capi_event.error is not None


@pytest.mark.db
class TestSendGooglePurchaseConversion:
    def test_sends_user_identifiers_and_records_success(self, test_db_session, test_store, monkeypatch):
        _make_credential(
            test_db_session,
            test_store.id,
            "google",
            destination_id="customers/1234567890/conversionActions/999",
        )
        customer = _make_customer(test_db_session, test_store.id, email_hash="e" * 64, phone_hash="p" * 64)
        calls = []

        def fake_post(url, headers=None, json=None, **kwargs):
            calls.append((url, headers, json))
            return _FakeResponse(200, json_body={})

        monkeypatch.setattr(requests, "post", fake_post)

        send_google_purchase_conversion(test_store.id, "order-1", _order_row(customer_id=customer.id))

        assert len(calls) == 1
        url, headers, payload = calls[0]
        assert "customers/1234567890:uploadClickConversions" in url
        conversion = payload["conversions"][0]
        assert conversion["conversionAction"] == "customers/1234567890/conversionActions/999"
        assert {"hashedEmail": "e" * 64} in conversion["userIdentifiers"]
        assert {"hashedPhoneNumber": "p" * 64} in conversion["userIdentifiers"]
        assert conversion["orderId"] == "order-1"
        # No consent-management source is wired up yet (see
        # GoogleAdsConnector.send_purchase_conversion's docstring) — the CAPI
        # orchestration layer must never fabricate a consent value.
        assert "consent" not in conversion

        capi_event = test_db_session.query(CapiEvent).filter_by(store_id=test_store.id, order_id="order-1").one()
        assert capi_event.status == "sent"

    def test_partial_failure_is_recorded_as_failed(self, test_db_session, test_store, monkeypatch):
        _make_credential(
            test_db_session,
            test_store.id,
            "google",
            destination_id="customers/1234567890/conversionActions/999",
        )
        customer = _make_customer(test_db_session, test_store.id)

        def fake_post(*a, **k):
            return _FakeResponse(200, json_body={"partialFailureError": {"message": "bad hash"}})

        monkeypatch.setattr(requests, "post", fake_post)

        send_google_purchase_conversion(test_store.id, "order-1", _order_row(customer_id=customer.id))

        capi_event = test_db_session.query(CapiEvent).filter_by(store_id=test_store.id, order_id="order-1").one()
        assert capi_event.status == "failed"
        assert "bad hash" in capi_event.error


class TestGoogleConsentMode:
    """GoogleAdsConnector.send_purchase_conversion's consent block, tested
    directly (no DB/orchestration layer) — see its docstring for why the
    block is opt-in rather than defaulted."""

    def _connector(self):
        from app.connectors.google import GoogleAdsConnector

        return GoogleAdsConnector(store_id="store-1", customer_id="1234567890")

    def test_no_consent_block_when_not_provided(self, monkeypatch):
        calls = []

        def fake_post(url, headers=None, json=None, **kwargs):
            calls.append(json)
            return _FakeResponse(200, json_body={})

        monkeypatch.setattr(requests, "post", fake_post)

        self._connector().send_purchase_conversion(
            "fake-token",
            "customers/1234567890/conversionActions/999",
            "order-1",
            datetime(2026, 1, 5, tzinfo=timezone.utc),
            100.0,
            "USD",
            "e" * 64,
            None,
        )

        assert "consent" not in calls[0]["conversions"][0]

    def test_consent_block_included_when_both_values_provided(self, monkeypatch):
        calls = []

        def fake_post(url, headers=None, json=None, **kwargs):
            calls.append(json)
            return _FakeResponse(200, json_body={})

        monkeypatch.setattr(requests, "post", fake_post)

        self._connector().send_purchase_conversion(
            "fake-token",
            "customers/1234567890/conversionActions/999",
            "order-1",
            datetime(2026, 1, 5, tzinfo=timezone.utc),
            100.0,
            "USD",
            "e" * 64,
            None,
            ad_user_data_consent="GRANTED",
            ad_personalization_consent="DENIED",
        )

        assert calls[0]["conversions"][0]["consent"] == {
            "adUserData": "GRANTED",
            "adPersonalization": "DENIED",
        }

    def test_no_consent_block_when_only_one_value_provided(self, monkeypatch):
        """Partial consent data is treated the same as none — half a
        consent decision isn't a decision."""
        calls = []

        def fake_post(url, headers=None, json=None, **kwargs):
            calls.append(json)
            return _FakeResponse(200, json_body={})

        monkeypatch.setattr(requests, "post", fake_post)

        self._connector().send_purchase_conversion(
            "fake-token",
            "customers/1234567890/conversionActions/999",
            "order-1",
            datetime(2026, 1, 5, tzinfo=timezone.utc),
            100.0,
            "USD",
            "e" * 64,
            None,
            ad_user_data_consent="GRANTED",
        )

        assert "consent" not in calls[0]["conversions"][0]


@pytest.mark.db
class TestCapiEndToEndViaIngestion:
    def test_order_ingestion_schedules_capi_send(self, client, auth_header, test_store, test_db_session, monkeypatch):
        _make_credential(test_db_session, test_store.id, "meta", destination_id="pixel-abc")
        calls = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1) or _FakeResponse())

        response = client.post(
            f"/stores/{test_store.id}/orders",
            headers=auth_header,
            json=[
                {
                    "order_id": "order-e2e",
                    "time": "2026-01-05T00:00:00Z",
                    "gross_amount": 42.0,
                    "currency": "USD",
                    "customer_email": "capi-buyer@example.com",
                }
            ],
        )
        assert response.status_code == status.HTTP_201_CREATED

        # TestClient runs BackgroundTasks synchronously before returning.
        capi_event = test_db_session.query(CapiEvent).filter_by(store_id=test_store.id, order_id="order-e2e").one()
        assert capi_event.status == "sent"
        assert len(calls) == 1
