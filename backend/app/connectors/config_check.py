"""Which connectors are configured in this environment, for the boot log.

A connector with a client id but no secret (or a redirect URI still pointing at localhost in
production) looks fine until a user clicks Conectar. This reports it at startup instead, naming
the environment variables that are missing and never their values."""

from typing import Optional

from app.connectors.google import GoogleSettings
from app.connectors.linkedin import LinkedInSettings
from app.connectors.mercadolibre import MercadoLibreSettings
from app.connectors.mercadopago import MercadoPagoSettings
from app.connectors.meta import MetaSettings
from app.connectors.shopify import ShopifySettings
from app.connectors.tiendanube import TiendanubeSettings
from app.connectors.tiktok import TikTokSettings

# provider -> (settings class, {ENV_VAR: attribute}, the attribute holding its redirect URI or None)
CONNECTORS = {
    "shopify": (
        ShopifySettings,
        {"SHOPIFY_API_KEY": "shopify_api_key", "SHOPIFY_API_SECRET": "shopify_api_secret"},
        "shopify_redirect_uri",
    ),
    "meta": (MetaSettings, {"META_APP_ID": "meta_app_id", "META_APP_SECRET": "meta_app_secret"}, "meta_redirect_uri"),
    "google": (
        GoogleSettings,
        {
            "GOOGLE_CLIENT_ID": "google_client_id",
            "GOOGLE_CLIENT_SECRET": "google_client_secret",
            "GOOGLE_DEVELOPER_TOKEN": "google_developer_token",
        },
        "google_redirect_uri",
    ),
    "tiktok": (
        TikTokSettings,
        {"TIKTOK_APP_ID": "tiktok_app_id", "TIKTOK_APP_SECRET": "tiktok_app_secret"},
        "tiktok_redirect_uri",
    ),
    "linkedin": (
        LinkedInSettings,
        {"LINKEDIN_CLIENT_ID": "linkedin_client_id", "LINKEDIN_CLIENT_SECRET": "linkedin_client_secret"},
        "linkedin_redirect_uri",
    ),
    "tiendanube": (
        TiendanubeSettings,
        {"TIENDANUBE_CLIENT_ID": "tiendanube_client_id", "TIENDANUBE_CLIENT_SECRET": "tiendanube_client_secret"},
        None,  # Tiendanube's redirect is declared in its partner portal, not sent by the app
    ),
    "mercadolibre": (
        MercadoLibreSettings,
        {"MERCADOLIBRE_CLIENT_ID": "mercadolibre_client_id", "MERCADOLIBRE_CLIENT_SECRET": "mercadolibre_client_secret"},
        "mercadolibre_redirect_uri",
    ),
    "mercadopago": (
        MercadoPagoSettings,
        {
            "MERCADOPAGO_CLIENT_ID": "mercadopago_client_id",
            "MERCADOPAGO_CLIENT_SECRET": "mercadopago_client_secret",
            "MERCADOPAGO_WEBHOOK_SECRET": "mercadopago_webhook_secret",
        },
        "mercadopago_redirect_uri",
    ),
}


def connector_config_report(production: bool = False) -> dict:
    """{provider: {"state": "configured" | "partial" | "not_configured", "missing": [...], "problems": [...]}}.

    "partial" means some of its variables are set and others are not: the dangerous case, because
    the connector looks installed. In production a redirect URI that is not https is a problem too.
    """
    report = {}
    for provider, (settings_cls, required, redirect_attr) in CONNECTORS.items():
        values = settings_cls()
        missing = [env for env, attr in required.items() if not (getattr(values, attr) or "").strip()]
        problems = []
        if redirect_attr:
            redirect: Optional[str] = getattr(values, redirect_attr)
            if production and not (redirect or "").startswith("https://"):
                problems.append(f"{redirect_attr.upper()} is not an https URL")
        if len(missing) == len(required):
            state = "not_configured"
        elif missing:
            state = "partial"
        else:
            state = "configured"
        report[provider] = {"state": state, "missing": missing, "problems": problems}
    return report
