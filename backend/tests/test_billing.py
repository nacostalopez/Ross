"""Tests for the billing scaffolding (app/models/billing.py,
app/dependencies.py::require_plan_feature). See the "Estrategia de
Billing" proposal doc for the full design — this only covers the part
that's actually live today: every account defaults to "scale" (no
plan restricts anyone yet), and the gating mechanism itself works when a
plan doesn't include a feature.
"""
import pytest
from fastapi import status

from app.dependencies import _plan_for_account, require_plan_feature
from app.models import Plan, Subscription


@pytest.mark.db
class TestPlanForAccount:
    def test_defaults_to_scale_with_no_subscription_row(self, test_db_session, test_account):
        plan = _plan_for_account(test_db_session, test_account.id)
        assert plan.id == "scale"

    def test_reads_the_accounts_actual_subscription(self, test_db_session, test_account):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="starter", status="active"))
        test_db_session.commit()

        plan = _plan_for_account(test_db_session, test_account.id)
        assert plan.id == "starter"


@pytest.mark.db
class TestRequirePlanFeature:
    def test_allows_when_plan_has_the_feature(self, test_db_session, test_account, test_user):
        # test_user has no Subscription row -> defaults to scale, which has everything.
        result = require_plan_feature("forecast")(test_user, test_db_session)
        assert result is test_user

    def test_blocks_with_402_when_plan_lacks_the_feature(self, test_db_session, test_account, test_user):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="starter", status="active"))
        test_db_session.commit()

        with pytest.raises(Exception) as exc_info:
            require_plan_feature("forecast")(test_user, test_db_session)
        assert exc_info.value.status_code == status.HTTP_402_PAYMENT_REQUIRED


@pytest.mark.db
class TestRegistrationCreatesSubscription:
    def test_register_creates_a_scale_subscription(self, client, test_db_session):
        response = client.post(
            "/auth/register",
            json={"account_name": "New Co", "email": "newco@example.com", "password": "supersecret123"},
        )
        assert response.status_code == status.HTTP_201_CREATED

        from app.models import User

        user = test_db_session.query(User).filter_by(email="newco@example.com").one()
        sub = test_db_session.query(Subscription).filter_by(account_id=user.account_id).one()
        assert sub.plan_id == "scale"
        assert sub.status == "active"


@pytest.mark.db
class TestGatedRoutesStillWorkOnScale:
    """Every test_user/test_store fixture defaults to "scale" (no
    Subscription row -> _plan_for_account's fallback) — this is the
    "nothing changed for anyone yet" guarantee the whole design depends on.
    A real downgrade path is exercised directly against
    require_plan_feature above, not through the HTTP layer, since nothing
    in the product can assign a lower plan yet (that's Phase 2).
    """

    def test_forecast_still_reachable(self, client, auth_header, test_store):
        response = client.get(f"/stores/{test_store.id}/metrics/forecast", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK

    def test_ltv_cohorts_still_reachable(self, client, auth_header, test_store):
        response = client.get(
            f"/stores/{test_store.id}/metrics/ltv-cohorts?start=2026-01-01T00:00:00Z&end=2026-02-01T00:00:00Z",
            headers=auth_header,
        )
        assert response.status_code == status.HTTP_200_OK

    def test_audit_log_still_reachable(self, client, auth_header, test_store):
        response = client.get(f"/stores/{test_store.id}/orders/audit-log", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK

    def test_store_role_override_still_settable(self, client, auth_header, test_store, admin_user):
        response = client.put(
            f"/stores/{test_store.id}/members/{admin_user.id}", headers=auth_header, json={"role": "viewer"}
        )
        assert response.status_code == status.HTTP_200_OK

    def test_weekly_report_still_enableable(self, client, auth_header, test_store):
        response = client.put(f"/stores/{test_store.id}/report-preferences", headers=auth_header, json={"enabled": True})
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.db
class TestGatingActuallyBlocksOnStarter:
    """The one end-to-end proof the mechanism really is wired onto the
    HTTP layer, not just unit-tested in isolation above."""

    def test_forecast_blocked_on_starter(self, client, auth_header, test_store, test_db_session, test_account):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="starter", status="active"))
        test_db_session.commit()

        response = client.get(f"/stores/{test_store.id}/metrics/forecast", headers=auth_header)
        assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED

    def test_disabling_weekly_report_is_never_blocked(
        self, client, auth_header, test_store, test_db_session, test_account
    ):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="starter", status="active"))
        test_db_session.commit()

        response = client.put(f"/stores/{test_store.id}/report-preferences", headers=auth_header, json={"enabled": False})
        assert response.status_code == status.HTTP_200_OK

    def test_clearing_a_store_role_override_is_never_blocked(
        self, client, auth_header, test_store, admin_user, test_db_session, test_account
    ):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="starter", status="active"))
        test_db_session.commit()

        response = client.put(
            f"/stores/{test_store.id}/members/{admin_user.id}", headers=auth_header, json={"role": None}
        )
        assert response.status_code == status.HTTP_200_OK
