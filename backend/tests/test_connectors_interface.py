"""Tests validating connector implementations conform to BaseConnector."""
import pytest

from app.connectors import BaseConnector
from app.connectors.google import GoogleAdsConnector
from app.connectors.linkedin import LinkedInConnector
from app.connectors.mercadolibre import MercadoLibreConnector
from app.connectors.mercadopago import MercadoPagoConnector
from app.connectors.meta import MetaConnector
from app.connectors.shopify import ShopifyConnector
from app.connectors.tiendanube import TiendanubeConnector
from app.connectors.tiktok import TikTokConnector


@pytest.mark.connector
class TestConnectorInterface:
    """Every connector must implement the full BaseConnector interface.

    Instantiating an ABC subclass raises TypeError if any abstract method is
    missing, so a successful construction below is itself the conformance
    check — the get_oauth_url call is just a smoke test on top of it.
    """

    @pytest.mark.parametrize(
        "connector_cls",
        [
            ShopifyConnector,
            MetaConnector,
            GoogleAdsConnector,
            TiendanubeConnector,
            MercadoPagoConnector,
            MercadoLibreConnector,
            TikTokConnector,
            LinkedInConnector,
        ],
    )
    def test_is_subclass_of_base_connector(self, connector_cls):
        assert issubclass(connector_cls, BaseConnector)

    def test_shopify_conforms_to_interface(self):
        connector = ShopifyConnector(store_id="store-1", shop_domain="test.myshopify.com")
        assert connector.get_oauth_url("state").startswith("https://")

    def test_meta_conforms_to_interface(self):
        connector = MetaConnector(store_id="store-1")
        assert connector.get_oauth_url("state").startswith("https://")

    def test_google_conforms_to_interface(self):
        connector = GoogleAdsConnector(store_id="store-1")
        assert connector.get_oauth_url("state").startswith("https://")

    def test_tiendanube_conforms_to_interface(self):
        connector = TiendanubeConnector(store_id="store-1")
        assert connector.get_oauth_url("state").startswith("https://")

    def test_mercadopago_conforms_to_interface(self):
        connector = MercadoPagoConnector(store_id="store-1")
        assert connector.get_oauth_url("state").startswith("https://")

    def test_tiktok_conforms_to_interface(self):
        connector = TikTokConnector(store_id="store-1")
        assert connector.get_oauth_url("state").startswith("https://")

    def test_linkedin_conforms_to_interface(self):
        connector = LinkedInConnector(store_id="store-1")
        assert connector.get_oauth_url("state").startswith("https://")

    def test_mercadolibre_conforms_to_interface(self):
        connector = MercadoLibreConnector(store_id="store-1")
        assert connector.get_oauth_url("state").startswith("https://")
