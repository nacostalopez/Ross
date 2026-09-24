"""Tests for Phase 2 billing: Mercado Pago Suscripciones checkout, cancel,
and the webhook that keeps Subscription/Invoice in sync. Mercado Pago's
API is always stubbed."""

import hashlib
import hmac
from unittest.mock import patch

import pytest
import requests
from fastapi import status

from app.models import Invoice, Plan, Subscription
from app.routes.billing import apply_authorized_payment, apply_preapproval
from app.services import billing_mercadopago as mp_billing


class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _billing_env(monkeypatch):
    monkeypatch.setenv("MERCADOPAGO_BILLING_ACCESS_TOKEN", "APP_USR-aramal")
    monkeypatch.setenv("MERCADOPAGO_BILLING_WEBHOOK_SECRET", "billing-secret")


@pytest.fixture
def priced_growth(test_db_session):
    plan = test_db_session.get(Plan, "growth")
    plan.monthly_price = 49000
    test_db_session.commit()
    return plan


def _signed(data_id, secret="billing-secret", request_id="req-9", ts="1727200000"):
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": request_id}


def _preapproval(account_id, plan_id="growth", status_="authorized", preapproval_id="pre-1"):
    return {
        "id": preapproval_id,
        "status": status_,
        "external_reference": f"{account_id}:{plan_id}",
        "payer_id": 555,
        "next_payment_date": "2026-10-24T10:00:00.000-03:00",
    }


class TestStripActivationParam:
    def test_removes_only_activation(self):
        url = "https://www.mercadopago.com.ar/subscriptions/checkout?preapproval_id=2c93&activation=true"
        assert (
            mp_billing.strip_activation_param(url)
            == "https://www.mercadopago.com.ar/subscriptions/checkout?preapproval_id=2c93"
        )

    def test_leaves_clean_url_alone(self):
        url = "https://www.mercadopago.com.ar/subscriptions/checkout?preapproval_id=2c93"
        assert mp_billing.strip_activation_param(url) == url


@pytest.mark.db
class TestPlans:
    def test_unpriced_plans_are_not_purchasable(self, client, auth_header, priced_growth):
        plans = {p["id"]: p for p in client.get("/billing/plans", headers=auth_header).json()}
        assert plans["growth"]["purchasable"] is True
        assert plans["growth"]["monthly_price"] == 49000
        assert plans["starter"]["purchasable"] is False


@pytest.mark.db
class TestCheckout:
    def test_unpriced_plan_is_refused(self, client, auth_header):
        response = client.post("/billing/checkout", headers=auth_header, json={"plan_id": "starter"})
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_creates_pending_preapproval_and_returns_clean_checkout_url(
        self, client, auth_header, test_user, priced_growth, test_db_session
    ):
        created = {
            "id": "pre-1",
            "status": "pending",
            "init_point": "https://www.mercadopago.com.ar/subscriptions/checkout?preapproval_id=pre-1&activation=true",
        }
        with patch("app.services.billing_mercadopago.requests.post", return_value=_FakeResponse(created)) as post:
            response = client.post("/billing/checkout", headers=auth_header, json={"plan_id": "growth"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["checkout_url"].endswith("preapproval_id=pre-1")
        body = post.call_args.kwargs["json"]
        assert body["external_reference"] == f"{test_user.account_id}:growth"
        assert body["auto_recurring"]["transaction_amount"] == 49000
        assert body["auto_recurring"]["currency_id"] == "ARS"
        assert body["status"] == "pending"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer APP_USR-aramal"
        # Starting a checkout changes nothing until Mercado Pago confirms it.
        assert test_db_session.query(Subscription).filter_by(account_id=test_user.account_id).count() == 0

    def test_only_owner_can_checkout(self, client, admin_auth_header, priced_growth):
        response = client.post("/billing/checkout", headers=admin_auth_header, json={"plan_id": "growth"})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_not_configured_is_503(self, client, auth_header, priced_growth, monkeypatch):
        monkeypatch.setenv("MERCADOPAGO_BILLING_ACCESS_TOKEN", "")
        response = client.post("/billing/checkout", headers=auth_header, json={"plan_id": "growth"})
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.db
class TestWebhook:
    def test_rejects_bad_signature(self, client):
        response = client.post(
            "/billing/webhooks/mercadopago?type=subscription_preapproval&data.id=pre-1",
            json={},
            headers=_signed("pre-1", secret="wrong"),
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authorized_preapproval_activates_the_plan(self, client, test_account, test_db_session):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="scale", status="active"))
        test_db_session.commit()

        with patch(
            "app.services.billing_mercadopago.requests.get",
            return_value=_FakeResponse(_preapproval(test_account.id)),
        ) as get:
            response = client.post(
                "/billing/webhooks/mercadopago?type=subscription_preapproval&data.id=pre-1",
                json={"type": "subscription_preapproval", "data": {"id": "pre-1"}},
                headers=_signed("pre-1"),
            )
        assert response.status_code == status.HTTP_200_OK
        # The body is only a pointer; the state comes from the API.
        assert get.call_args.args[0].endswith("/preapproval/pre-1")

        sub = test_db_session.query(Subscription).filter_by(account_id=test_account.id).one()
        test_db_session.refresh(sub)
        assert sub.plan_id == "growth"
        assert sub.status == "active"
        assert sub.payment_provider == "mercadopago"
        assert sub.provider_subscription_id == "pre-1"
        assert sub.current_period_end is not None

    def test_plan_change_cancels_the_previous_preapproval(self, test_account, test_db_session):
        test_db_session.add(
            Subscription(
                account_id=test_account.id,
                plan_id="growth",
                status="active",
                payment_provider="mercadopago",
                provider_subscription_id="pre-old",
            )
        )
        test_db_session.commit()

        with patch("app.services.billing_mercadopago.requests.put", return_value=_FakeResponse({})) as put:
            apply_preapproval(test_db_session, _preapproval(test_account.id, plan_id="scale", preapproval_id="pre-new"))
        assert put.call_args.args[0].endswith("/preapproval/pre-old")
        assert put.call_args.kwargs["json"] == {"status": "cancelled"}

        # ...and that old preapproval's own "cancelled" notification must not
        # knock the account off the new one.
        apply_preapproval(
            test_db_session,
            _preapproval(test_account.id, plan_id="growth", status_="cancelled", preapproval_id="pre-old"),
        )
        sub = test_db_session.query(Subscription).filter_by(account_id=test_account.id).one()
        assert (sub.plan_id, sub.status, sub.provider_subscription_id) == ("scale", "active", "pre-new")

    def test_pending_preapproval_changes_nothing(self, test_account, test_db_session):
        apply_preapproval(test_db_session, _preapproval(test_account.id, status_="pending"))
        assert test_db_session.query(Subscription).filter_by(account_id=test_account.id).count() == 0

    def test_unknown_account_is_ignored(self, test_db_session):
        apply_preapproval(test_db_session, _preapproval("00000000-0000-0000-0000-000000000000"))
        assert test_db_session.query(Subscription).count() == 0


@pytest.mark.db
class TestAuthorizedPayments:
    @pytest.fixture
    def paid_sub(self, test_account, test_db_session):
        sub = Subscription(
            account_id=test_account.id,
            plan_id="growth",
            status="active",
            payment_provider="mercadopago",
            provider_subscription_id="pre-1",
        )
        test_db_session.add(sub)
        test_db_session.commit()
        return sub

    def _charge(self, payment_status, charge_id=6114264375):
        return {
            "id": charge_id,
            "preapproval_id": "pre-1",
            "transaction_amount": "49000.00",
            "currency_id": "ARS",
            "last_modified": "2026-10-24T10:05:00.000-03:00",
            "payment": {"id": 1, "status": payment_status},
        }

    def test_approved_charge_becomes_a_paid_invoice_once(self, paid_sub, test_db_session):
        apply_authorized_payment(test_db_session, self._charge("approved"))
        apply_authorized_payment(test_db_session, self._charge("approved"))  # retried notification

        invoices = test_db_session.query(Invoice).filter_by(account_id=paid_sub.account_id).all()
        assert len(invoices) == 1
        assert invoices[0].status == "paid"
        assert float(invoices[0].amount) == 49000
        assert invoices[0].paid_at is not None

    def test_rejected_charge_marks_past_due_and_recovery_reactivates(self, paid_sub, test_db_session):
        apply_authorized_payment(test_db_session, self._charge("rejected"))
        test_db_session.refresh(paid_sub)
        assert paid_sub.status == "past_due"

        apply_authorized_payment(test_db_session, self._charge("approved"))
        test_db_session.refresh(paid_sub)
        assert paid_sub.status == "active"


@pytest.mark.db
class TestCancel:
    def test_cancels_at_mercadopago(self, client, auth_header, test_user, test_db_session):
        test_db_session.add(
            Subscription(
                account_id=test_user.account_id,
                plan_id="growth",
                status="active",
                payment_provider="mercadopago",
                provider_subscription_id="pre-1",
            )
        )
        test_db_session.commit()

        with patch("app.services.billing_mercadopago.requests.put", return_value=_FakeResponse({})) as put:
            response = client.post("/billing/cancel", headers=auth_header)
        assert response.json() == {"status": "canceled"}
        assert put.call_args.kwargs["json"] == {"status": "cancelled"}

    def test_nothing_to_cancel_on_a_grandfathered_account(self, client, auth_header, test_user, test_db_session):
        test_db_session.add(Subscription(account_id=test_user.account_id, plan_id="scale", status="active"))
        test_db_session.commit()
        response = client.post("/billing/cancel", headers=auth_header)
        assert response.status_code == status.HTTP_409_CONFLICT
