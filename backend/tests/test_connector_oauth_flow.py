"""Tests for the Shopify/Meta/Google OAuth callback round-trip — in
particular that provider_account_id (shop domain / ad account id /
Ads customer id) actually gets persisted and then reused by the sync
routes, which was a real pre-existing gap (see README "Status / next
steps") fixed alongside the frontend "Conectar" flow.
"""

import pytest
import requests
from fastapi import status

from app.models import StoreCredential


class _FakeTokenResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _fake_oauth_token_exchange(monkeypatch):
    """Every provider's exchange_auth_code() hits a real OAuth token
    endpoint (Shopify/Google via requests.post, Meta via requests.get) —
    stub both so no real network call happens."""
    body = {"access_token": "fake-access-token", "refresh_token": "fake-refresh-token", "expires_in": 3600}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeTokenResponse(body))
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeTokenResponse(body))


def _get_auth_url_and_state(client, auth_header, store_id, provider, **extra_params):
    response = client.post(
        f"/connectors/{provider}/auth-url",
        headers=auth_header,
        params={"store_id": str(store_id), **extra_params},
    )
    assert response.status_code == status.HTTP_200_OK
    return response.json()["state"]


@pytest.mark.db
class TestShopifyOAuthFlow:
    def test_callback_persists_shop_domain_as_provider_account_id(
        self, client, auth_header, test_store, test_db_session
    ):
        state = _get_auth_url_and_state(
            client, auth_header, test_store.id, "shopify", shop_domain="mystore.myshopify.com"
        )

        response = client.post(
            "/connectors/shopify/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "shop": "mystore.myshopify.com",
                "state": state,
            },
        )
        assert response.status_code == status.HTTP_200_OK

        credential = test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="shopify").one()
        assert credential.provider_account_id == "mystore.myshopify.com"

    def test_callback_rejects_missing_state(self, client, auth_header, test_store):
        response = client.post(
            "/connectors/shopify/callback",
            headers=auth_header,
            params={"store_id": str(test_store.id), "code": "fake-code", "shop": "mystore.myshopify.com"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_callback_rejects_already_consumed_state(self, client, auth_header, test_store):
        state = _get_auth_url_and_state(
            client, auth_header, test_store.id, "shopify", shop_domain="mystore.myshopify.com"
        )
        params = {
            "store_id": str(test_store.id),
            "code": "fake-code",
            "shop": "mystore.myshopify.com",
            "state": state,
        }
        first = client.post("/connectors/shopify/callback", headers=auth_header, params=params)
        assert first.status_code == status.HTTP_200_OK

        second = client.post("/connectors/shopify/callback", headers=auth_header, params=params)
        assert second.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.db
class TestMetaOAuthFlow:
    def test_callback_persists_ad_account_id(self, client, auth_header, test_store, test_db_session):
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "meta")

        response = client.post(
            "/connectors/meta/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state,
                "ad_account_id": "act_123456789",
            },
        )
        assert response.status_code == status.HTTP_200_OK

        credential = test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="meta").one()
        assert credential.provider_account_id == "act_123456789"

    def test_callback_without_ad_account_id_leaves_it_null(self, client, auth_header, test_store, test_db_session):
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "meta")

        response = client.post(
            "/connectors/meta/callback",
            headers=auth_header,
            params={"store_id": str(test_store.id), "code": "fake-code", "state": state},
        )
        assert response.status_code == status.HTTP_200_OK

        credential = test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="meta").one()
        assert credential.provider_account_id is None

    def test_reconnecting_without_ad_account_id_does_not_clobber_existing_value(
        self, client, auth_header, test_store, test_db_session
    ):
        state1 = _get_auth_url_and_state(client, auth_header, test_store.id, "meta")
        client.post(
            "/connectors/meta/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state1,
                "ad_account_id": "act_111",
            },
        )

        state2 = _get_auth_url_and_state(client, auth_header, test_store.id, "meta")
        client.post(
            "/connectors/meta/callback",
            headers=auth_header,
            params={"store_id": str(test_store.id), "code": "fake-code-2", "state": state2},
        )

        credential = test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="meta").one()
        assert credential.provider_account_id == "act_111"

    def test_sync_route_constructs_connector_with_stored_ad_account_id(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "meta")
        client.post(
            "/connectors/meta/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state,
                "ad_account_id": "act_999",
            },
        )

        captured = {}

        def fake_fetch_ad_spend(self, access_token, start_date, end_date):
            captured["ad_account_id"] = self.ad_account_id
            return []

        from app.connectors.meta import MetaConnector

        monkeypatch.setattr(MetaConnector, "fetch_ad_spend", fake_fetch_ad_spend)

        response = client.post(
            "/connectors/meta/sync-ad-spend",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-01-31T00:00:00Z",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert captured["ad_account_id"] == "act_999"


@pytest.mark.db
class TestGoogleOAuthFlow:
    def test_callback_persists_customer_id(self, client, auth_header, test_store, test_db_session):
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "google")

        response = client.post(
            "/connectors/google/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state,
                "customer_id": "1234567890",
            },
        )
        assert response.status_code == status.HTTP_200_OK

        credential = test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="google").one()
        assert credential.provider_account_id == "1234567890"

    def test_sync_route_constructs_connector_with_stored_customer_id(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "google")
        client.post(
            "/connectors/google/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state,
                "customer_id": "1112223333",
            },
        )

        captured = {}

        def fake_fetch_ad_spend(self, access_token, start_date, end_date):
            captured["customer_id"] = self.customer_id
            return []

        from app.connectors.google import GoogleAdsConnector

        monkeypatch.setattr(GoogleAdsConnector, "fetch_ad_spend", fake_fetch_ad_spend)

        response = client.post(
            "/connectors/google/sync-ad-spend",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-01-31T00:00:00Z",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert captured["customer_id"] == "1112223333"


@pytest.mark.db
class TestTikTokOAuthFlow:
    """TikTok wraps its token response in a {"code": 0, "data": {...}}
    envelope, unlike every other provider here — the module-level
    _fake_oauth_token_exchange fixture returns a flat body, so each test
    below re-patches requests.post with a TikTok-shaped one instead."""

    def _fake_tiktok_token_response(self, monkeypatch):
        monkeypatch.setattr(
            requests,
            "post",
            lambda *a, **k: _FakeTokenResponse({"code": 0, "message": "OK", "data": {"access_token": "fake-access-token"}}),
        )

    def test_callback_persists_advertiser_id(self, client, auth_header, test_store, test_db_session, monkeypatch):
        self._fake_tiktok_token_response(monkeypatch)
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "tiktok")

        response = client.post(
            "/connectors/tiktok/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state,
                "advertiser_id": "adv-123456",
            },
        )
        assert response.status_code == status.HTTP_200_OK

        credential = test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="tiktok").one()
        assert credential.provider_account_id == "adv-123456"

    def test_callback_without_advertiser_id_leaves_it_null(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        self._fake_tiktok_token_response(monkeypatch)
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "tiktok")

        response = client.post(
            "/connectors/tiktok/callback",
            headers=auth_header,
            params={"store_id": str(test_store.id), "code": "fake-code", "state": state},
        )
        assert response.status_code == status.HTTP_200_OK

        credential = test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="tiktok").one()
        assert credential.provider_account_id is None

    def test_sync_route_constructs_connector_with_stored_advertiser_id(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        self._fake_tiktok_token_response(monkeypatch)
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "tiktok")
        client.post(
            "/connectors/tiktok/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state,
                "advertiser_id": "adv-999",
            },
        )

        captured = {}

        def fake_fetch_ad_spend(self, access_token, start_date, end_date):
            captured["advertiser_id"] = self.advertiser_id
            return []

        from app.connectors.tiktok import TikTokConnector

        monkeypatch.setattr(TikTokConnector, "fetch_ad_spend", fake_fetch_ad_spend)

        response = client.post(
            "/connectors/tiktok/sync-ad-spend",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-01-31T00:00:00Z",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert captured["advertiser_id"] == "adv-999"


@pytest.mark.db
class TestLinkedInOAuthFlow:
    def test_callback_persists_ad_account_id(self, client, auth_header, test_store, test_db_session):
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "linkedin")

        response = client.post(
            "/connectors/linkedin/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state,
                "ad_account_id": "512345678",
            },
        )
        assert response.status_code == status.HTTP_200_OK

        credential = (
            test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="linkedin").one()
        )
        assert credential.provider_account_id == "512345678"

    def test_sync_route_constructs_connector_with_stored_ad_account_id(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        state = _get_auth_url_and_state(client, auth_header, test_store.id, "linkedin")
        client.post(
            "/connectors/linkedin/callback",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "code": "fake-code",
                "state": state,
                "ad_account_id": "512999999",
            },
        )

        captured = {}

        def fake_fetch_ad_spend(self, access_token, start_date, end_date):
            captured["ad_account_id"] = self.ad_account_id
            return []

        from app.connectors.linkedin import LinkedInConnector

        monkeypatch.setattr(LinkedInConnector, "fetch_ad_spend", fake_fetch_ad_spend)

        response = client.post(
            "/connectors/linkedin/sync-ad-spend",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-01-31T00:00:00Z",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert captured["ad_account_id"] == "512999999"
