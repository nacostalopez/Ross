"""Charging ROSS's own customers through Mercado Pago Suscripciones.

Not to be confused with app/connectors/mercadopago.py, which reads a
*merchant's* payments with their OAuth token. This module acts as ROSS
itself: one fixed access token (ROSS's own Mercado Pago account) and its
own webhook secret, both separate env vars.

Uses subscriptions "sin plan asociado" (POST /preapproval, status pending):
each checkout creates a preapproval carrying its own amount, so a plan's
price lives only in our `plans` table — no second copy in Mercado Pago's
panel to keep in sync.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from pydantic_settings import BaseSettings

logger = logging.getLogger("ross.billing")

API_BASE = "https://api.mercadopago.com"


class BillingSettings(BaseSettings):
    # ROSS's own production (or test) access token — Tus integraciones >
    # Credenciales. Not an OAuth token of any merchant.
    mercadopago_billing_access_token: str = ""
    # The webhook "clave secreta" of the app that receives billing
    # notifications (subscription_preapproval / subscription_authorized_payment).
    mercadopago_billing_webhook_secret: str = ""
    # Mercado Pago charges in the collector account's local currency only.
    billing_currency: str = "ARS"
    frontend_url: str = "http://localhost:3100"

    class Config:
        env_file = ".env"


class BillingNotConfigured(RuntimeError):
    """No billing access token set — checkout can't work in this environment."""


def _headers(settings: BillingSettings) -> dict:
    if not settings.mercadopago_billing_access_token:
        raise BillingNotConfigured("MERCADOPAGO_BILLING_ACCESS_TOKEN is not set")
    return {"Authorization": f"Bearer {settings.mercadopago_billing_access_token}"}


def external_reference(account_id, plan_id: str) -> str:
    """What ties a preapproval back to who's paying for what."""
    return f"{account_id}:{plan_id}"


def parse_external_reference(value) -> Optional[tuple[str, str]]:
    account_id, sep, plan_id = str(value or "").partition(":")
    return (account_id, plan_id) if sep and account_id and plan_id else None


def strip_activation_param(init_point: str) -> str:
    """Drop `activation=true` from a preapproval's init_point.

    Since 2026-09-02 Mercado Pago returns init_points carrying that
    parameter, and the page it opens says "Esta página no existe"; the same
    URL without it opens the normal checkout (mercadopago/sdk-nodejs#480,
    open with no official fix as of 2026-09-24). Harmless once they fix
    it — a URL without the parameter is what their docs have always shown.
    """
    parts = urlparse(init_point)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "activation"]
    return urlunparse(parts._replace(query=urlencode(query)))


def create_checkout(account_id, plan, payer_email: str, settings: Optional[BillingSettings] = None) -> dict:
    """Create a pending monthly preapproval for `plan`; returns its id and checkout URL."""
    settings = settings or BillingSettings()
    body = {
        "reason": f"ROSS {plan.name}",
        "external_reference": external_reference(account_id, plan.id),
        "payer_email": payer_email,
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": float(Decimal(plan.monthly_price)),
            "currency_id": settings.billing_currency,
        },
        "back_url": f"{settings.frontend_url}/index.html?billing=return",
        "status": "pending",
    }
    response = requests.post(f"{API_BASE}/preapproval", json=body, headers=_headers(settings))
    response.raise_for_status()
    data = response.json()
    return {"id": data["id"], "checkout_url": strip_activation_param(data["init_point"])}


def get_preapproval(preapproval_id: str, settings: Optional[BillingSettings] = None) -> dict:
    settings = settings or BillingSettings()
    response = requests.get(f"{API_BASE}/preapproval/{preapproval_id}", headers=_headers(settings))
    response.raise_for_status()
    return response.json()


def cancel_preapproval(preapproval_id: str, settings: Optional[BillingSettings] = None) -> None:
    settings = settings or BillingSettings()
    response = requests.put(
        f"{API_BASE}/preapproval/{preapproval_id}", json={"status": "cancelled"}, headers=_headers(settings)
    )
    response.raise_for_status()


def get_authorized_payment(authorized_payment_id: str, settings: Optional[BillingSettings] = None) -> dict:
    settings = settings or BillingSettings()
    response = requests.get(f"{API_BASE}/authorized_payments/{authorized_payment_id}", headers=_headers(settings))
    response.raise_for_status()
    return response.json()


# preapproval.status -> subscriptions.status. "pending" (checkout started,
# never finished) deliberately maps to nothing: it must not touch whatever
# subscription the account already has.
PREAPPROVAL_STATUS_MAP = {
    "authorized": "active",
    "paused": "past_due",
    "cancelled": "canceled",
}

# authorized_payment.payment.status -> invoices.status
INVOICE_STATUS_MAP = {
    "approved": "paid",
    "refunded": "void",
    "cancelled": "void",
    "charged_back": "void",
}


def parse_mp_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
