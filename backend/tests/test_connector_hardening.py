"""Connector hardening: a connector that is not configured refuses clearly, the Shopify callback only
talks to myshopify.com, unsigned or empty-secret webhooks are rejected, provider errors do not leak
secrets, and every call to a provider has a timeout."""

import base64
import hashlib
import hmac
import json
from uuid import uuid4

import pytest
import requests
from fastapi import status

from app.connectors import HTTP_TIMEOUT, ConnectorNotConfigured, redact, require_config
from app.connectors.meta import MetaConnector
from app.connectors.shopify import ShopifyConnector, normalize_shop
from app.connectors.tiendanube import TiendanubeConnector
from app.models import ConnectorStatus, Store

# provider -> the environment variable that, when empty, must make /auth-url refuse
CLIENT_ID_VAR = {
    "shopify": "SHOPIFY_API_KEY",
    "meta": "META_APP_ID",
    "google": "GOOGLE_CLIENT_ID",
    "tiktok": "TIKTOK_APP_ID",
    "linkedin": "LINKEDIN_CLIENT_ID",
    "tiendanube": "TIENDANUBE_CLIENT_ID",
    "mercadolibre": "MERCADOLIBRE_CLIENT_ID",
    "mercadopago": "MERCADOPAGO_CLIENT_ID",
}


class TestConnectorConfigReport:
    def test_everything_configured(self):
        from app.connectors.config_check import connector_config_report

        # google also needs a developer token, which the shared test environment does not set
        report = connector_config_report()
        assert report["meta"]["state"] == "configured"
        assert report["shopify"]["missing"] == []

    def test_a_missing_secret_is_partial_and_named(self, monkeypatch):
        from app.connectors.config_check import connector_config_report

        monkeypatch.setenv("META_APP_SECRET", "")
        info = connector_config_report()["meta"]
        assert info["state"] == "partial"
        assert info["missing"] == ["META_APP_SECRET"]

    def test_nothing_set_is_not_configured(self, monkeypatch):
        from app.connectors.config_check import connector_config_report

        monkeypatch.setenv("TIKTOK_APP_ID", "")
        monkeypatch.setenv("TIKTOK_APP_SECRET", "")
        assert connector_config_report()["tiktok"]["state"] == "not_configured"

    def test_a_localhost_redirect_is_a_problem_in_production_only(self):
        from app.connectors.config_check import connector_config_report

        assert connector_config_report(production=False)["meta"]["problems"] == []
        assert connector_config_report(production=True)["meta"]["problems"] == ["META_REDIRECT_URI is not an https URL"]

    def test_the_report_never_contains_a_secret(self):
        from app.connectors.config_check import connector_config_report

        assert "test-meta-secret" not in repr(connector_config_report())


class TestRedact:
    def test_hides_the_query_string_of_a_url(self):
        error = requests.HTTPError(
            "400 Client Error: Bad Request for url: "
            "https://graph.facebook.com/v19.0/oauth/access_token?client_id=1&client_secret=abc123&code=xyz"
        )
        text = redact(error)
        assert "abc123" not in text and "xyz" not in text
        assert "graph.facebook.com/v19.0/oauth/access_token" in text

    def test_hides_a_token_outside_a_url(self):
        assert "SECRET1" not in redact(Exception("failed with access_token=SECRET1 end"))

    def test_leaves_ordinary_text_alone(self):
        assert redact(Exception("status=500 barcode=9")) == "status=500 barcode=9"

    def test_an_exception_with_no_message_still_says_something(self):
        # A wrong encryption key raises InvalidToken with an empty message, which used to show as "sin detalle".
        assert redact(Exception()) == "Exception"


class TestRequireConfig:
    def test_blank_values_are_reported_by_name(self):
        with pytest.raises(ConnectorNotConfigured) as caught:
            require_config("meta", META_APP_ID="", META_REDIRECT_URI="https://x.test", OTHER="  ")
        assert caught.value.missing == ["META_APP_ID", "OTHER"]

    def test_filled_values_pass(self):
        require_config("meta", META_APP_ID="1", META_REDIRECT_URI="https://x.test")


@pytest.mark.db
class TestAuthUrlNeedsConfiguration:
    @pytest.mark.parametrize("provider", sorted(CLIENT_ID_VAR))
    def test_an_empty_client_id_gives_503_not_a_broken_url(
        self, client, auth_header, test_store, monkeypatch, provider
    ):
        monkeypatch.setenv(CLIENT_ID_VAR[provider], "")
        params = {"store_id": str(test_store.id)}
        if provider == "shopify":
            params["shop_domain"] = "mystore.myshopify.com"

        response = client.post(f"/connectors/{provider}/auth-url", headers=auth_header, params=params)

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "no está configurado" in response.json()["detail"]

    @pytest.mark.parametrize("provider", sorted(CLIENT_ID_VAR))
    def test_a_configured_connector_still_builds_its_url(self, client, auth_header, test_store, provider):
        params = {"store_id": str(test_store.id)}
        if provider == "shopify":
            params["shop_domain"] = "mystore.myshopify.com"

        response = client.post(f"/connectors/{provider}/auth-url", headers=auth_header, params=params)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["auth_url"].startswith("https://")


class TestShopifyShop:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("mystore.myshopify.com", "mystore.myshopify.com"),
            ("MyStore", "mystore.myshopify.com"),
            (" my-store.myshopify.com ", "my-store.myshopify.com"),
        ],
    )
    def test_accepts_a_myshopify_domain(self, raw, expected):
        assert normalize_shop(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "evil.com",
            "mystore.myshopify.com.evil.com",
            "evil.com/x?.myshopify.com",
            "mystore.myshopify.com@evil.com",
            "evil.com#.myshopify.com",
            "",
            "-bad.myshopify.com",
            "a b.myshopify.com",
        ],
    )
    def test_refuses_anything_else(self, raw):
        with pytest.raises(ValueError):
            normalize_shop(raw)

    def test_the_connector_refuses_a_foreign_host(self):
        with pytest.raises(ValueError):
            ShopifyConnector("store-1", "evil.com")

    def test_the_token_exchange_never_reaches_a_foreign_host(self, monkeypatch):
        sent = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: sent.append((a, k)))
        connector = ShopifyConnector("store-1", "mystore.myshopify.com")

        with pytest.raises(ValueError):
            connector.exchange_auth_code("code", "https://x.test", "evil.com")

        assert sent == []


@pytest.mark.db
class TestShopifyRoutesRefuseForeignShops:
    def test_auth_url_rejects_a_foreign_shop(self, client, auth_header, test_store):
        response = client.post(
            "/connectors/shopify/auth-url",
            headers=auth_header,
            params={"store_id": str(test_store.id), "shop_domain": "evil.com"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_callback_does_not_send_the_secret_to_a_foreign_shop(self, client, auth_header, test_store, monkeypatch):
        sent = []
        monkeypatch.setattr(requests, "post", lambda *a, **k: sent.append((a, k)))
        state = client.post(
            "/connectors/shopify/auth-url",
            headers=auth_header,
            params={"store_id": str(test_store.id), "shop_domain": "mystore.myshopify.com"},
        ).json()["state"]

        response = client.post(
            "/connectors/shopify/callback",
            headers=auth_header,
            params={"store_id": str(test_store.id), "code": "c", "shop": "evil.com", "state": state},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert sent == []


def _shopify_sign(body: str, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode()


class TestWebhookSignatureUnit:
    @pytest.mark.parametrize("signature", [None, "", "not-ascii-é"])
    def test_shopify_rejects_a_missing_or_odd_header(self, monkeypatch, signature):
        monkeypatch.setenv("SHOPIFY_API_SECRET", "s3cret")
        assert ShopifyConnector("s").validate_webhook_signature("{}", signature) is False

    @pytest.mark.parametrize("signature", [None, "", "not-ascii-é"])
    def test_tiendanube_rejects_a_missing_or_odd_header(self, monkeypatch, signature):
        monkeypatch.setenv("TIENDANUBE_CLIENT_SECRET", "s3cret")
        assert TiendanubeConnector("s").validate_webhook_signature("{}", signature) is False

    def test_shopify_with_no_secret_rejects_even_a_correctly_computed_signature(self, monkeypatch):
        monkeypatch.setenv("SHOPIFY_API_SECRET", "")
        forged = _shopify_sign("{}", "")
        assert ShopifyConnector("s").validate_webhook_signature("{}", forged) is False

    def test_tiendanube_with_no_secret_rejects_even_a_correctly_computed_signature(self, monkeypatch):
        monkeypatch.setenv("TIENDANUBE_CLIENT_SECRET", "")
        forged = _shopify_sign("{}", "")
        assert TiendanubeConnector("s").validate_webhook_signature("{}", forged) is False

    def test_a_valid_signature_still_passes(self, monkeypatch):
        monkeypatch.setenv("SHOPIFY_API_SECRET", "s3cret")
        assert ShopifyConnector("s").validate_webhook_signature("{}", _shopify_sign("{}", "s3cret")) is True


@pytest.fixture
def tiendanube_store(test_db_session, test_account):
    """A store on the Tiendanube platform (test_store defaults to shopify)."""
    store = Store(
        id=uuid4(), account_id=test_account.id, name="Tiendanube Test Store", platform="tiendanube",
        currency="USD", timezone="UTC",
    )
    test_db_session.add(store)
    test_db_session.commit()
    test_db_session.refresh(store)
    return store


@pytest.mark.db
class TestWebhookRoutesNeverCrashOnBadSignatures:
    def test_shopify_webhook_without_a_signature_header_is_401(self, client, test_store):
        response = client.post(
            f"/connectors/shopify/webhook/{test_store.id}",
            content=json.dumps({"id": 1}),
            headers={"Content-Type": "application/json", "X-Shopify-Topic": "orders/create"},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_tiendanube_webhook_without_a_signature_header_is_401(self, client, tiendanube_store):
        response = client.post(
            f"/connectors/tiendanube/webhook/{tiendanube_store.id}",
            content=json.dumps({"id": 1}),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_shopify_webhook_with_no_secret_configured_is_401(self, client, test_store, monkeypatch):
        monkeypatch.setenv("SHOPIFY_API_SECRET", "")
        body = json.dumps({"id": 1})
        response = client.post(
            f"/connectors/shopify/webhook/{test_store.id}",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Topic": "orders/create",
                "X-Shopify-Hmac-Sha256": _shopify_sign(body, ""),
            },
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.db
class TestProviderErrorsDoNotLeakSecrets:
    def test_a_failed_meta_sync_stores_and_returns_no_token(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        state = client.post(
            "/connectors/meta/auth-url", headers=auth_header, params={"store_id": str(test_store.id)}
        ).json()["state"]

        class _Token:
            def raise_for_status(self):
                pass

            def json(self):
                return {"access_token": "fake-access-token", "expires_in": 3600}

        monkeypatch.setattr(requests, "get", lambda *a, **k: _Token())
        client.post(
            "/connectors/meta/callback",
            headers=auth_header,
            params={"store_id": str(test_store.id), "code": "c", "state": state, "ad_account_id": "act_1"},
        )

        def boom(self, access_token, start_date, end_date):
            raise requests.HTTPError(
                "400 Client Error: Bad Request for url: "
                "https://graph.facebook.com/v19.0/act_1/insights?access_token=LEAKED-TOKEN&fields=spend"
            )

        monkeypatch.setattr(MetaConnector, "fetch_ad_spend", boom)

        response = client.post(
            "/connectors/meta/sync-ad-spend",
            headers=auth_header,
            params={
                "store_id": str(test_store.id),
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-01-31T00:00:00Z",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "LEAKED-TOKEN" not in response.text
        row = test_db_session.query(ConnectorStatus).filter_by(store_id=test_store.id, provider="meta").one()
        assert "LEAKED-TOKEN" not in (row.last_error or "")
        assert "LEAKED-TOKEN" not in (row.last_sync_error or "")


class TestEveryProviderCallHasATimeout:
    def test_shopify_token_exchange(self, monkeypatch):
        seen = {}

        class _Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"access_token": "t"}

        def fake_post(*args, **kwargs):
            seen.update(kwargs)
            return _Response()

        monkeypatch.setattr(requests, "post", fake_post)
        ShopifyConnector("s", "mystore.myshopify.com").exchange_auth_code("c", "https://x.test", "mystore.myshopify.com")
        assert seen["timeout"] == HTTP_TIMEOUT

    def test_no_outbound_call_in_the_app_is_missing_one(self):
        # A call without a timeout can hold a worker for as long as the provider keeps the socket open.
        import re
        from pathlib import Path

        app = Path(__file__).resolve().parent.parent / "app"
        missing = []
        for path in app.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"requests\.(get|post|put|delete|patch)\(", text):
                depth, i = 1, match.end()
                while depth:
                    depth += {"(": 1, ")": -1}.get(text[i], 0)
                    i += 1
                if "timeout" not in text[match.start() : i]:
                    missing.append(f"{path.name}:{text.count(chr(10), 0, match.start()) + 1}")
        assert missing == []
