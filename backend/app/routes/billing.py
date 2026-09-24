"""Billing: plans, the account's subscription, Mercado Pago checkout, and
the webhook that keeps subscriptions/invoices in sync.

Phase 2 of the "Estrategia de Billing" proposal, with Mercado Pago
Suscripciones as the provider (see app/services/billing_mercadopago.py).
This wires the money path only: nothing here changes who gets which plan
by default — registration still pins new accounts to "scale" (see
app/routes/auth.py::register) until the business decides prices, trial
and the downgrade rule.
"""

import logging
from uuid import UUID, uuid4

import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.connectors.mercadopago import verify_notification_signature
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import Account, Invoice, Plan, Subscription, User
from app.rate_limit import limiter
from app.services import billing_mercadopago as mp_billing

logger = logging.getLogger("escal.billing")

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutIn(BaseModel):
    plan_id: str


def _plan_out(plan: Plan, currency: str) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "monthly_price": float(plan.monthly_price) if plan.monthly_price is not None else None,
        "currency": currency,
        "max_stores": plan.max_stores,
        "max_orders_per_month": plan.max_orders_per_month,
        "max_users": plan.max_users,
        "features": plan.features,
        # No price yet = can't be bought, only assigned (see 028_billing.sql).
        "purchasable": plan.monthly_price is not None,
    }


@router.get("/plans")
def list_plans(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    currency = mp_billing.BillingSettings().billing_currency
    order = {"starter": 0, "growth": 1, "scale": 2}
    plans = sorted(db.query(Plan).all(), key=lambda p: order.get(p.id, 99))
    return [_plan_out(p, currency) for p in plans]


@router.get("/subscription")
def get_subscription(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = db.query(Subscription).filter_by(account_id=current_user.account_id).first()
    if not sub:
        # Same fallback as app/dependencies.py::_plan_for_account.
        return {"plan_id": "scale", "status": "active", "payment_provider": None, "current_period_end": None}
    return {
        "plan_id": sub.plan_id,
        "status": sub.status,
        "payment_provider": sub.payment_provider,
        "current_period_end": sub.current_period_end,
        "trial_ends_at": sub.trial_ends_at,
    }


@router.get("/invoices")
def list_invoices(current_user: User = Depends(require_role("owner")), db: Session = Depends(get_db)):
    rows = (
        db.query(Invoice)
        .filter_by(account_id=current_user.account_id)
        .order_by(Invoice.issued_at.desc())
        .limit(24)
        .all()
    )
    return [
        {
            "id": str(inv.id),
            "amount": float(inv.amount),
            "currency": inv.currency,
            "status": inv.status,
            "issued_at": inv.issued_at,
            "paid_at": inv.paid_at,
        }
        for inv in rows
    ]


@router.post("/checkout")
def start_checkout(
    payload: CheckoutIn,
    current_user: User = Depends(require_role("owner")),
    db: Session = Depends(get_db),
):
    """Start a Mercado Pago subscription for a plan; the frontend redirects
    to the returned checkout_url. Nothing about the account's subscription
    changes here — only the webhook, once Mercado Pago confirms the payer
    authorized it, does that."""
    plan = db.get(Plan, payload.plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan inexistente")
    if plan.monthly_price is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este plan todavía no tiene precio")

    sub = db.query(Subscription).filter_by(account_id=current_user.account_id).first()
    if sub and sub.plan_id == plan.id and sub.payment_provider == "mercadopago" and sub.status == "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya estás suscripto a este plan")

    try:
        checkout = mp_billing.create_checkout(current_user.account_id, plan, current_user.email)
    except mp_billing.BillingNotConfigured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="El cobro no está configurado")
    except requests.RequestException as e:
        logger.error("billing_checkout_failed", extra={"account_id": str(current_user.account_id), "error": str(e)})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Mercado Pago no respondió, probá de nuevo")

    return {"checkout_url": checkout["checkout_url"]}


@router.post("/cancel")
def cancel_subscription(current_user: User = Depends(require_role("owner")), db: Session = Depends(get_db)):
    """Cancel the account's paid subscription at Mercado Pago (no more charges).

    The plan itself is left as is: what a canceled account drops to, and
    when, is one of the billing proposal's open business decisions.
    """
    sub = db.query(Subscription).filter_by(account_id=current_user.account_id).first()
    if not sub or sub.payment_provider != "mercadopago" or not sub.provider_subscription_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No hay una suscripción paga para cancelar")
    if sub.status == "canceled":
        return {"status": "canceled"}

    try:
        mp_billing.cancel_preapproval(sub.provider_subscription_id)
    except mp_billing.BillingNotConfigured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="El cobro no está configurado")
    except requests.RequestException as e:
        logger.error("billing_cancel_failed", extra={"account_id": str(current_user.account_id), "error": str(e)})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Mercado Pago no respondió, probá de nuevo")

    sub.status = "canceled"
    db.commit()
    return {"status": "canceled"}


@router.post("/webhooks/mercadopago")
@limiter.limit("300/minute")
async def mercadopago_billing_webhook(
    request: Request,
    x_signature: str = Header(None),
    x_request_id: str = Header(None),
    db: Session = Depends(get_db),
):
    """Mercado Pago subscription notifications.

    Processed inline, not in a background task: Mercado Pago allows 22
    seconds and retries on a non-2xx, and a retry is exactly what should
    happen if applying the change failed. The body is only a pointer — the
    preapproval / authorized payment is always re-fetched from the API.
    """
    data_id = request.query_params.get("data.id")
    settings = mp_billing.BillingSettings()
    if not verify_notification_signature(
        settings.mercadopago_billing_webhook_secret, x_signature, x_request_id, data_id
    ):
        logger.warning("billing_webhook_rejected", extra={"reason": "invalid_signature"})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    try:
        body = await request.json()
    except ValueError:
        body = {}
    topic = request.query_params.get("type") or body.get("type")
    if not data_id:
        return {"status": "ignored"}

    if topic == "subscription_preapproval":
        apply_preapproval(db, mp_billing.get_preapproval(data_id, settings), settings)
    elif topic == "subscription_authorized_payment":
        apply_authorized_payment(db, mp_billing.get_authorized_payment(data_id, settings))
    return {"status": "received"}


def apply_preapproval(db: Session, preapproval: dict, settings=None) -> None:
    """Mirror a preapproval's state onto the account's Subscription."""
    new_status = mp_billing.PREAPPROVAL_STATUS_MAP.get(preapproval.get("status"))
    ref = mp_billing.parse_external_reference(preapproval.get("external_reference"))
    if not new_status or not ref:
        return
    account_id, plan_id = ref
    try:
        account_uuid = UUID(account_id)
    except ValueError:
        return
    if not db.get(Account, account_uuid) or not db.get(Plan, plan_id):
        logger.warning("billing_unknown_reference", extra={"external_reference": f"{account_id}:{plan_id}"})
        return

    preapproval_id = str(preapproval["id"])
    sub = db.query(Subscription).filter_by(account_id=account_uuid).first()

    if new_status == "active":
        previous = sub.provider_subscription_id if sub and sub.payment_provider == "mercadopago" else None
        if not sub:
            sub = Subscription(account_id=account_uuid, plan_id=plan_id)
            db.add(sub)
        sub.plan_id = plan_id
        sub.status = "active"
        sub.payment_provider = "mercadopago"
        sub.provider_subscription_id = preapproval_id
        sub.provider_customer_id = str(preapproval.get("payer_id") or "") or None
        sub.current_period_end = mp_billing.parse_mp_datetime(preapproval.get("next_payment_date"))
        db.commit()
        # A plan change is a new preapproval; stop charging the old one.
        if previous and previous != preapproval_id:
            try:
                mp_billing.cancel_preapproval(previous, settings)
            except Exception as e:
                logger.error("billing_cancel_previous_failed", extra={"preapproval_id": previous, "error": str(e)})
        return

    # paused / cancelled only count for the preapproval the account is
    # actually on — an old one being cancelled (e.g. just above, after a
    # plan change) must not knock out the new one.
    if sub and sub.provider_subscription_id == preapproval_id:
        sub.status = new_status
        db.commit()


def apply_authorized_payment(db: Session, authorized_payment: dict) -> None:
    """Record one recurring charge as an Invoice (upsert by its id)."""
    sub = (
        db.query(Subscription)
        .filter_by(
            payment_provider="mercadopago", provider_subscription_id=str(authorized_payment.get("preapproval_id"))
        )
        .first()
    )
    if not sub:
        return

    payment = authorized_payment.get("payment") or {}
    invoice_status = mp_billing.INVOICE_STATUS_MAP.get(payment.get("status"), "open")
    provider_invoice_id = str(authorized_payment["id"])

    invoice = db.query(Invoice).filter_by(account_id=sub.account_id, provider_invoice_id=provider_invoice_id).first()
    if not invoice:
        invoice = Invoice(id=uuid4(), account_id=sub.account_id, provider_invoice_id=provider_invoice_id)
        db.add(invoice)
    invoice.amount = float(authorized_payment.get("transaction_amount") or 0)
    invoice.currency = authorized_payment.get("currency_id") or "ARS"
    invoice.status = invoice_status
    if invoice_status == "paid":
        invoice.paid_at = mp_billing.parse_mp_datetime(
            authorized_payment.get("last_modified") or authorized_payment.get("debit_date")
        )
        if sub.status == "past_due":
            sub.status = "active"
    elif payment.get("status") == "rejected" and sub.status == "active":
        sub.status = "past_due"
    db.commit()
