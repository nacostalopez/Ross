"""Route-level tests for the Mercado Libre and Mercado Pago connectors:
OAuth callback persistence, order/payment ingestion (and removal of
orders that stop being sales), idempotent Product Ads sync, single-use
refresh-token rotation, and notification authentication. Provider HTTP is
always stubbed.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests
from fastapi import status
from sqlalchemy import func, select

from app.connectors.mercadolibre import MercadoLibreConnector
from app.connectors.mercadopago import MercadoPagoConnector
from app.models import Customer, Store, StoreCredential, TokenRefreshAudit
from app.models import ad_spend as ad_spend_table
from app.models import orders as orders_table
from app.routes.connectors import _sync_mercadolibre_order, _sync_mercadopago_payment
from app.security import decrypt_secret, encrypt_secret

START = "2026-01-01T00:00:00Z"
END = "2026-12-31T00:00:00Z"


class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._body


def _ml_order(order_id, status_="paid", buyer_id=900, total=1000, created="2026-05-01T10:00:00.000-03:00"):
    return {
        "id": order_id,
        "status": status_,
        "date_created": created,
        "currency_id": "ARS",
        "total_amount": total,
        "order_items": [{"item": {"id": "MLA1"}, "sale_fee": 100, "quantity": 1, "unit_price": total}],
        "buyer": {"id": buyer_id},
    }


def _payment(payment_id, status_="approved", external_reference=None, order_type=None, amount=1000, fee=50):
    payment = {
        "id": payment_id,
        "status": status_,
        "date_approved": "2026-05-02T10:00:00.000-03:00",
        "currency_id": "ARS",
        "transaction_amount": amount,
        "fee_details": [{"type": "mercadopago_fee", "amount": fee, "fee_payer": "collector"}],
        "payer": {"id": 77, "email": "comprador@example.com"},
        "external_reference": external_reference,
    }
    if order_type:
        payment["order"] = {"type": order_type, "id": 1}
    return payment


def _add_credential(db, store_id, provider, account_id, expires_at=None, refresh="refresh-1"):
    credential = StoreCredential(
        id=uuid4(),
        store_id=store_id,
        provider=provider,
        access_token=encrypt_secret("access-1"),
        refresh_token=encrypt_secret(refresh) if refresh else None,
        expires_at=expires_at,
        provider_account_id=account_id,
    )
    db.add(credential)
    db.commit()
    return credential


def _orders(db, store_id):
    rows = db.execute(select(orders_table).where(orders_table.c.store_id == store_id)).mappings().all()
    return {row["order_id"]: row for row in rows}


@pytest.fixture
def mp_store(test_db_session, test_account):
    store = Store(
        id=uuid4(),
        account_id=test_account.id,
        name="Links de pago",
        platform="mercadopago",
        currency="ARS",
        timezone="America/Argentina/Buenos_Aires",
    )
    test_db_session.add(store)
    test_db_session.commit()
    return store


# ---------------------------------------------------------------------------
# Mercado Libre
# ---------------------------------------------------------------------------


@pytest.mark.db
class TestMercadoLibreOAuth:
    def test_callback_persists_seller_id_and_refresh_token(self, client, auth_header, test_store, test_db_session):
        state = client.post(
            "/connectors/mercadolibre/auth-url", headers=auth_header, params={"store_id": str(test_store.id)}
        ).json()["state"]

        token_body = {"access_token": "APP_USR-1", "refresh_token": "TG-1", "expires_in": 21600, "user_id": 207035636}
        with patch("app.connectors.mercadolibre.requests.post", return_value=_FakeResponse(token_body)) as post:
            response = client.post(
                "/connectors/mercadolibre/callback",
                headers=auth_header,
                params={"store_id": str(test_store.id), "code": "TG-code", "state": state},
            )
        assert response.status_code == status.HTTP_200_OK
        # Mercado Libre's token endpoint is form-encoded, not JSON.
        assert post.call_args.kwargs["data"]["grant_type"] == "authorization_code"

        credential = (
            test_db_session.query(StoreCredential).filter_by(store_id=test_store.id, provider="mercadolibre").one()
        )
        assert credential.provider_account_id == "207035636"
        assert decrypt_secret(credential.refresh_token) == "TG-1"
        assert credential.expires_at is not None

    def test_auth_url_points_at_country_consent_screen(self, client, auth_header, test_store):
        response = client.post(
            "/connectors/mercadolibre/auth-url", headers=auth_header, params={"store_id": str(test_store.id)}
        )
        assert response.json()["auth_url"].startswith("https://auth.mercadolibre.com.ar/authorization?")


@pytest.mark.db
class TestMercadoLibreSyncOrders:
    def test_ingests_paid_orders_and_removes_cancelled_ones(self, client, auth_header, test_store, test_db_session):
        _add_credential(test_db_session, test_store.id, "mercadolibre", "207035636")

        with patch.object(MercadoLibreConnector, "fetch_orders", return_value=[_ml_order(1), _ml_order(2)]):
            first = client.post(
                "/connectors/mercadolibre/sync-orders",
                headers=auth_header,
                params={"store_id": str(test_store.id), "start_date": START, "end_date": END},
            )
        assert first.status_code == status.HTTP_200_OK
        assert set(_orders(test_db_session, test_store.id)) == {"1", "2"}

        # Order 2 got cancelled after it was ingested.
        with patch.object(
            MercadoLibreConnector, "fetch_orders", return_value=[_ml_order(1), _ml_order(2, status_="cancelled")]
        ):
            second = client.post(
                "/connectors/mercadolibre/sync-orders",
                headers=auth_header,
                params={"store_id": str(test_store.id), "start_date": START, "end_date": END},
            )
        assert second.json() == {"status": "success", "orders_synced": 1, "orders_removed": 1}

        orders = _orders(test_db_session, test_store.id)
        assert set(orders) == {"1"}
        assert float(orders["1"]["payment_gateway_fee"]) == 100
        assert float(orders["1"]["net_profit"]) == 900
        assert orders["1"]["attribution_utm_source"] == "mercadolibre"

    def test_buyer_without_email_still_becomes_a_customer(self, client, auth_header, test_store, test_db_session):
        _add_credential(test_db_session, test_store.id, "mercadolibre", "207035636")
        orders = [_ml_order(10, buyer_id=555), _ml_order(11, buyer_id=555, created="2026-06-01T10:00:00.000-03:00")]
        with patch.object(MercadoLibreConnector, "fetch_orders", return_value=orders):
            client.post(
                "/connectors/mercadolibre/sync-orders",
                headers=auth_header,
                params={"store_id": str(test_store.id), "start_date": START, "end_date": END},
            )

        customers = test_db_session.query(Customer).filter_by(store_id=test_store.id).all()
        assert len(customers) == 1
        assert customers[0].external_customer_id == "ml:555"
        rows = _orders(test_db_session, test_store.id)
        assert rows["10"]["customer_id"] == rows["11"]["customer_id"] == customers[0].id

    def test_requires_a_connection(self, client, auth_header, test_store):
        response = client.post(
            "/connectors/mercadolibre/sync-orders",
            headers=auth_header,
            params={"store_id": str(test_store.id), "start_date": START, "end_date": END},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.db
class TestMercadoLibreSyncAdSpend:
    def _sync(self, client, auth_header, store_id):
        now = datetime.now(timezone.utc)
        return client.post(
            "/connectors/mercadolibre/sync-ad-spend",
            headers=auth_header,
            params={
                "store_id": str(store_id),
                "start_date": (now - timedelta(days=30)).isoformat(),
                "end_date": now.isoformat(),
            },
        )

    def test_resync_replaces_instead_of_duplicating(self, client, auth_header, test_store, test_db_session):
        _add_credential(test_db_session, test_store.id, "mercadolibre", "207035636")
        day = datetime.now(timezone.utc).date() - timedelta(days=2)
        record = {
            "time": datetime.combine(day, datetime.min.time()),
            "platform": "mercadolibre",
            "campaign_id": "77",
            "campaign_name": "Crecimiento A",
            "adset_id": "",
            "spend": 12.5,
            "impressions": 100,
            "clicks": 5,
        }
        with (
            patch.object(MercadoLibreConnector, "fetch_advertiser_id", return_value="adv-1"),
            patch.object(MercadoLibreConnector, "fetch_ad_spend", return_value=[record]),
        ):
            assert self._sync(client, auth_header, test_store.id).json()["records_synced"] == 1
            assert self._sync(client, auth_header, test_store.id).json()["records_synced"] == 1

        total = test_db_session.execute(
            select(func.count(), func.sum(ad_spend_table.c.spend)).where(
                ad_spend_table.c.store_id == test_store.id, ad_spend_table.c.platform == "mercadolibre"
            )
        ).one()
        assert total[0] == 1
        assert float(total[1]) == 12.5

    def test_seller_without_product_ads_is_not_an_error(self, client, auth_header, test_store, test_db_session):
        _add_credential(test_db_session, test_store.id, "mercadolibre", "207035636")
        with patch.object(MercadoLibreConnector, "fetch_advertiser_id", return_value=None):
            response = self._sync(client, auth_header, test_store.id)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["product_ads_enabled"] is False


@pytest.mark.db
class TestTokenRefresh:
    def test_expired_token_is_refreshed_and_new_refresh_token_saved(
        self, client, auth_header, test_store, test_db_session
    ):
        credential = _add_credential(
            test_db_session,
            test_store.id,
            "mercadolibre",
            "207035636",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            refresh="TG-old",
        )
        token_body = {"access_token": "APP_USR-new", "refresh_token": "TG-new", "expires_in": 21600}
        with (
            patch("app.connectors.mercadolibre.requests.post", return_value=_FakeResponse(token_body)) as post,
            patch.object(MercadoLibreConnector, "fetch_orders", return_value=[]) as fetch,
        ):
            response = client.post(
                "/connectors/mercadolibre/sync-orders",
                headers=auth_header,
                params={"store_id": str(test_store.id), "start_date": START, "end_date": END},
            )
        assert response.status_code == status.HTTP_200_OK
        assert post.call_args.kwargs["data"]["refresh_token"] == "TG-old"
        assert fetch.call_args.args[0] == "APP_USR-new"

        test_db_session.refresh(credential)
        # Single-use refresh tokens: losing the new one would disconnect the store.
        assert decrypt_secret(credential.refresh_token) == "TG-new"
        assert decrypt_secret(credential.access_token) == "APP_USR-new"
        assert test_db_session.query(TokenRefreshAudit).filter_by(store_id=test_store.id, success=True).count() == 1

    def test_token_far_from_expiry_is_not_refreshed(self, client, auth_header, test_store, test_db_session):
        _add_credential(
            test_db_session,
            test_store.id,
            "mercadolibre",
            "207035636",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
        )
        with (
            patch("app.connectors.mercadolibre.requests.post") as post,
            patch.object(MercadoLibreConnector, "fetch_orders", return_value=[]),
        ):
            client.post(
                "/connectors/mercadolibre/sync-orders",
                headers=auth_header,
                params={"store_id": str(test_store.id), "start_date": START, "end_date": END},
            )
        post.assert_not_called()


@pytest.mark.db
class TestMercadoLibreNotifications:
    def test_rejects_other_applications(self, client, monkeypatch):
        monkeypatch.setenv("MERCADOLIBRE_CLIENT_ID", "12345")
        response = client.post(
            "/connectors/mercadolibre/notifications",
            json={"topic": "orders_v2", "resource": "/orders/1", "user_id": 1, "application_id": 999},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_accepts_own_application_and_ignores_other_topics(self, client, monkeypatch):
        monkeypatch.setenv("MERCADOLIBRE_CLIENT_ID", "12345")
        response = client.post(
            "/connectors/mercadolibre/notifications",
            json={"topic": "items", "resource": "/items/MLA1", "user_id": 1, "application_id": 12345},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_background_sync_refetches_the_order(self, test_store, test_db_session):
        _add_credential(test_db_session, test_store.id, "mercadolibre", "207035636")
        with patch.object(MercadoLibreConnector, "fetch_order", return_value=_ml_order(42)) as fetch:
            _sync_mercadolibre_order(test_db_session, "207035636", "/orders/42")
        fetch.assert_called_once_with("access-1", "/orders/42")
        assert "42" in _orders(test_db_session, test_store.id)

        with patch.object(MercadoLibreConnector, "fetch_order", return_value=_ml_order(42, status_="cancelled")):
            _sync_mercadolibre_order(test_db_session, "207035636", "/orders/42")
        assert "42" not in _orders(test_db_session, test_store.id)


# ---------------------------------------------------------------------------
# Mercado Pago
# ---------------------------------------------------------------------------


@pytest.mark.db
class TestMercadoPagoSyncPayments:
    def _sync(self, client, auth_header, store_id):
        return client.post(
            "/connectors/mercadopago/sync-payments",
            headers=auth_header,
            params={"store_id": str(store_id), "start_date": START, "end_date": END},
        )

    def test_mercadopago_store_gets_payments_as_orders(self, client, auth_header, mp_store, test_db_session):
        _add_credential(test_db_session, mp_store.id, "mercadopago", "seller-1")
        payments = [_payment(1), _payment(2, order_type="mercadolibre"), _payment(3, status_="rejected")]
        with patch.object(MercadoPagoConnector, "fetch_payments", return_value=payments):
            response = self._sync(client, auth_header, mp_store.id)
        assert response.json()["mode"] == "orders"

        orders = _orders(test_db_session, mp_store.id)
        # The Mercado Libre sale belongs to that connector; the rejected payment was never a sale.
        assert set(orders) == {"mp:1"}
        assert float(orders["mp:1"]["payment_gateway_fee"]) == 50

        with patch.object(MercadoPagoConnector, "fetch_payments", return_value=[_payment(1, status_="refunded")]):
            self._sync(client, auth_header, mp_store.id)
        assert _orders(test_db_session, mp_store.id) == {}

    def test_other_platform_only_gets_fees_on_matching_orders(self, client, auth_header, test_store, test_db_session):
        test_db_session.execute(
            orders_table.insert().values(
                time=datetime(2026, 5, 2, tzinfo=timezone.utc),
                store_id=test_store.id,
                order_id="5001",
                gross_amount=1000,
                discounts=0,
                shipping_fee=0,
                payment_gateway_fee=0,
                cogs_total=0,
                currency="ARS",
            )
        )
        test_db_session.commit()
        _add_credential(test_db_session, test_store.id, "mercadopago", "seller-1")

        payments = [_payment(1, external_reference="5001", fee=61), _payment(2, external_reference="no-such-order")]
        with patch.object(MercadoPagoConnector, "fetch_payments", return_value=payments):
            response = self._sync(client, auth_header, test_store.id)
        assert response.json() == {"status": "success", "mode": "fees", "payments_seen": 2, "orders_updated": 1}

        orders = _orders(test_db_session, test_store.id)
        assert set(orders) == {"5001"}  # no revenue double-counted
        assert float(orders["5001"]["payment_gateway_fee"]) == 61
        assert float(orders["5001"]["net_profit"]) == 939

    def test_background_payment_notification_applies_payment(self, mp_store, test_db_session):
        _add_credential(test_db_session, mp_store.id, "mercadopago", "seller-1")
        with patch.object(MercadoPagoConnector, "fetch_payment", return_value=_payment(9)):
            _sync_mercadopago_payment(test_db_session, "seller-1", "9")
        assert "mp:9" in _orders(test_db_session, mp_store.id)


def _signed_headers(secret, data_id, request_id="req-1", ts="1704908010"):
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": request_id}


@pytest.mark.db
class TestMercadoPagoNotifications:
    def test_rejects_unsigned(self, client, monkeypatch):
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", "whsec")
        response = client.post("/connectors/mercadopago/notifications?type=payment&data.id=123", json={})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_rejects_signature_made_with_client_secret(self, client, monkeypatch):
        # The old connector verified against the client secret — the wrong key.
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", "whsec")
        monkeypatch.setenv("MERCADOPAGO_CLIENT_SECRET", "client-secret")
        response = client.post(
            "/connectors/mercadopago/notifications?type=payment&data.id=123",
            json={"user_id": 1},
            headers=_signed_headers("client-secret", "123"),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_accepts_valid_signature(self, client, monkeypatch):
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", "whsec")
        with patch("app.routes.connectors._process_mercadopago_payment_notification") as task:
            response = client.post(
                "/connectors/mercadopago/notifications?type=payment&data.id=123",
                json={"type": "payment", "user_id": 44444, "data": {"id": "123"}},
                headers=_signed_headers("whsec", "123"),
            )
        assert response.status_code == status.HTTP_200_OK
        task.assert_called_once_with("44444", "123")
