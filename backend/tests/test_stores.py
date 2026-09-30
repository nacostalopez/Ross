"""Tests for store creation — currency validation in particular.

A store created with a currency symbol instead of an ISO 4217 code (e.g.
"AR$" instead of "ARS") used to save fine but then crashed the frontend's
Intl.NumberFormat everywhere that store's numbers get displayed. This is
the backend half of the fix: reject it at creation time instead.
"""

import pytest
from fastapi import status


@pytest.mark.db
class TestStoreCurrencyValidation:
    def test_valid_iso_currency_is_accepted(self, client, auth_header):
        response = client.post(
            "/stores",
            headers=auth_header,
            json={"name": "Tienda ARS", "platform": "shopify", "currency": "ARS"},
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["currency"] == "ARS"

    def test_currency_symbol_is_rejected(self, client, auth_header):
        response = client.post(
            "/stores",
            headers=auth_header,
            json={"name": "Tienda Mala", "platform": "shopify", "currency": "AR$"},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_lowercase_currency_is_rejected(self, client, auth_header):
        response = client.post(
            "/stores",
            headers=auth_header,
            json={"name": "Tienda Minuscula", "platform": "shopify", "currency": "usd"},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_default_currency_is_usd(self, client, auth_header):
        response = client.post(
            "/stores",
            headers=auth_header,
            json={"name": "Tienda Default", "platform": "shopify"},
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["currency"] == "USD"
