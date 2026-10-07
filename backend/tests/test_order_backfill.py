"""Backfilling Shopify and Tiendanube orders from before the store connected."""

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests
from fastapi import status
from sqlalchemy import select

from app.connectors.shopify import ShopifyConnector
from app.connectors.tiendanube import TiendanubeConnector
from app.models import StoreCredential
from app.models import orders as orders_table
from app.security import encrypt_secret

START = "2026-01-01T00:00:00Z"
END = "2026-03-31T00:00:00Z"


class _FakeResponse:
    def __init__(self, body, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._body


def _shopify_order(order_id, total="1000.00", cancelled_at=None):
    return {
        "id": order_id,
        "created_at": "2026-02-10T12:00:00-03:00",
        "total_price": total,
        "total_discounts": "0.00",
        "shipping_lines": [],
        "currency": "ARS",
        "cancelled_at": cancelled_at,
        "customer": {"id": 501, "email": "cliente@example.com"},
    }


def _tiendanube_order(order_id, total="1000.00", status_="open"):
    return {
        "id": order_id,
        "created_at": "2026-02-10T12:00:00-03:00",
        "total": total,
        "discount": "0.00",
        "shipping_cost_customer": "0.00",
        "currency": "ARS",
        "status": status_,
        "customer": {"id": 601, "email": "cliente@example.com"},
    }


PROVIDERS = [
    ("shopify", ShopifyConnector, "mitienda.myshopify.com", _shopify_order, {"cancelled_at": "2026-02-11T09:00:00-03:00"}),
    ("tiendanube", TiendanubeConnector, "1234567", _tiendanube_order, {"status_": "cancelled"}),
]


def _orders(db, store_id):
    rows = db.execute(select(orders_table).where(orders_table.c.store_id == store_id)).mappings().all()
    return {row["order_id"]: row for row in rows}


def _sync(client, auth_header, store_id, provider):
    return client.post(
        f"/connectors/{provider}/sync-orders",
        headers=auth_header,
        params={"store_id": str(store_id), "start_date": START, "end_date": END},
    )


@pytest.mark.db
@pytest.mark.parametrize(("provider", "connector_class", "account_id", "make_order", "cancelled"), PROVIDERS)
def test_backfill_ingests_past_orders_and_removes_cancelled_ones(
    client, auth_header, test_store, test_db_session, provider, connector_class, account_id, make_order, cancelled
):
    test_db_session.add(
        StoreCredential(
            id=uuid4(),
            store_id=test_store.id,
            provider=provider,
            access_token=encrypt_secret("access-1"),
            provider_account_id=account_id,
        )
    )
    test_db_session.commit()

    with patch.object(connector_class, "fetch_orders", return_value=[make_order(1), make_order(2)]) as fetch, patch(
        "app.routes.connectors.send_meta_purchase_event"
    ) as meta_event, patch("app.routes.connectors.send_google_purchase_conversion") as google_conversion:
        first = _sync(client, auth_header, test_store.id, provider)

    assert first.status_code == status.HTTP_200_OK
    assert first.json() == {"status": "success", "orders_synced": 2, "orders_removed": 0}
    assert fetch.call_args.args[0] == "access-1"
    assert set(_orders(test_db_session, test_store.id)) == {"1", "2"}
    # Past sales aren't conversions happening now: nothing goes to the ad platforms.
    meta_event.assert_not_called()
    google_conversion.assert_not_called()

    # Order 2 was cancelled after it was ingested; a second run is idempotent for order 1.
    with patch.object(connector_class, "fetch_orders", return_value=[make_order(1), make_order(2, **cancelled)]):
        second = _sync(client, auth_header, test_store.id, provider)

    assert second.json() == {"status": "success", "orders_synced": 1, "orders_removed": 1}
    orders = _orders(test_db_session, test_store.id)
    assert set(orders) == {"1"}
    assert float(orders["1"]["gross_amount"]) == 1000
    assert orders["1"]["customer_id"] is not None


@pytest.mark.db
@pytest.mark.parametrize("provider", ["shopify", "tiendanube"])
def test_backfill_requires_a_connection(client, auth_header, test_store, provider):
    response = _sync(client, auth_header, test_store.id, provider)
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.db
def test_failed_backfill_is_recorded_on_the_connector(client, auth_header, test_store, test_db_session):
    test_db_session.add(
        StoreCredential(
            id=uuid4(),
            store_id=test_store.id,
            provider="tiendanube",
            access_token=encrypt_secret("access-1"),
            provider_account_id="1234567",
        )
    )
    test_db_session.commit()

    with patch.object(TiendanubeConnector, "fetch_orders", side_effect=requests.HTTPError("401")):
        response = _sync(client, auth_header, test_store.id, "tiendanube")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    health = client.get(f"/stores/{test_store.id}/connectors/health", headers=auth_header).json()
    assert health["tiendanube"]["last_sync_status"] == "error"


class TestShopifyFetchOrders:
    def test_follows_the_next_link_without_repeating_the_filters(self):
        connector = ShopifyConnector("store", "mitienda.myshopify.com")
        next_url = "https://mitienda.myshopify.com/admin/api/2024-01/orders.json?limit=250&page_info=abc"
        pages = [
            _FakeResponse(
                {"orders": [_shopify_order(1)]},
                headers={"Link": f'<https://mitienda.myshopify.com/prev>; rel="previous", <{next_url}>; rel="next"'},
            ),
            _FakeResponse({"orders": [_shopify_order(2)]}),
        ]
        with patch("app.connectors.shopify.requests.get", side_effect=pages) as get:
            orders = connector.fetch_orders("token", *_window())

        assert [o["id"] for o in orders] == [1, 2]
        assert get.call_args_list[0].kwargs["params"]["status"] == "any"
        assert get.call_args_list[1].args[0] == next_url
        assert get.call_args_list[1].kwargs["params"] == {}


class TestTiendanubeFetchOrders:
    def test_a_404_past_the_last_full_page_ends_the_listing(self):
        connector = TiendanubeConnector("store", "1234567")
        full_page = [_tiendanube_order(i) for i in range(200)]
        pages = [_FakeResponse(full_page), _FakeResponse({"description": "Last page is 1"}, status_code=404)]
        with patch("app.connectors.tiendanube.requests.get", side_effect=pages):
            orders = connector.fetch_orders("token", *_window())
        assert len(orders) == 200

    def test_a_404_on_the_first_page_is_an_error(self):
        connector = TiendanubeConnector("store", "1234567")
        with patch("app.connectors.tiendanube.requests.get", return_value=_FakeResponse({}, status_code=404)):
            with pytest.raises(requests.HTTPError):
                connector.fetch_orders("token", *_window())


def _window():
    return datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 3, 31, tzinfo=timezone.utc)
