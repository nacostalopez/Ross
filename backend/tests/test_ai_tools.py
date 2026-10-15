"""The assistant's tool registry: its contract, the policy that authorizes calls, and running the analytics tools.

The cross-account battery that runs over every registered tool lives in test_ai_tools_isolation.py.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import status
from pydantic import ValidationError

from app.ai.tools import (
    DenialReason,
    Risk,
    StoreNotFound,
    Tool,
    all_tools,
    authorize,
    available_tools,
    resolve_context,
    run_tool,
)
from app.ai.tools.base import NoParams, Output, PeriodParams, ToolContext
from app.ai.tools.registry import register
from app.config import settings
from app.models import Subscription

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)

EXPECTED_TOOLS = {
    "summary",
    "pnl",
    "daily",
    "creatives",
    "ltv_cohorts",
    "cac_by_channel",
    "attribution_by_channel",
    "forecast",
    "weekly_narrative",
}


def _fake_tool(**overrides):
    values = dict(
        name="fake",
        module="analytics",
        description="una herramienta de prueba",
        params=NoParams,
        result=dict,
        run=lambda ctx, params: Output({}),
    )
    values.update(overrides)
    return Tool(**values)


def _bare_context(role="viewer", email="someone@example.com"):
    """A context for the policy checks that never reach the database."""
    return ToolContext(
        db=None,
        user=SimpleNamespace(email=email, account_id=uuid4()),
        store=SimpleNamespace(id=uuid4(), currency="USD"),
        role=role,
        now=NOW,
    )


class TestTheContract:
    def test_every_analytics_tool_is_registered_and_read_only(self):
        tools = {tool.name: tool for tool in all_tools()}
        assert set(tools) == EXPECTED_TOOLS
        assert all(tool.risk is Risk.READ for tool in tools.values())
        assert all(tool.module == "analytics" for tool in tools.values())

    @pytest.mark.parametrize("name", ["store_id", "account_id", "user_id"])
    def test_a_tool_cannot_take_identity_as_a_parameter(self, name):
        from pydantic import create_model

        from app.ai.tools.base import ToolParams

        params = create_model("Leaky", __base__=ToolParams, **{name: (str, ...)})
        with pytest.raises(ValueError, match="identity comes from the session"):
            _fake_tool(params=params)

    @pytest.mark.parametrize("name", ["store_id", "account_id", "user_id"])
    def test_a_call_cannot_smuggle_identity_in(self, name):
        with pytest.raises(ValidationError):
            PeriodParams.model_validate({"days": 7, name: str(uuid4())})

    def test_the_window_has_a_floor_and_a_configurable_ceiling(self):
        assert PeriodParams.model_validate({"days": 1}).days == 1
        with pytest.raises(ValidationError):
            PeriodParams.model_validate({"days": 0})
        with pytest.raises(ValidationError):
            PeriodParams.model_validate({"days": settings.assistant_max_window_days + 1})

    def test_registering_the_same_name_twice_is_an_error(self):
        with pytest.raises(ValueError, match="already registered"):
            register(_fake_tool(name="summary"))

    def test_an_unknown_tool_is_a_typed_denial_not_an_exception(self):
        result = run_tool("does_not_exist", _bare_context())
        assert result.ok is False
        assert result.denial is DenialReason.UNKNOWN_TOOL


class TestThePolicy:
    def test_a_read_tool_for_a_viewer_is_allowed(self):
        assert authorize(_bare_context(), _fake_tool()).allowed

    def test_a_module_the_account_has_not_activated_is_denied(self):
        decision = authorize(_bare_context(), _fake_tool(module="crm"))
        assert decision.reason is DenialReason.MODULE_INACTIVE

    def test_a_role_below_the_minimum_is_denied(self):
        decision = authorize(_bare_context(role="viewer"), _fake_tool(min_role="admin"))
        assert decision.reason is DenialReason.FORBIDDEN

    @pytest.mark.parametrize("role", ["admin", "owner"])
    def test_a_role_at_or_above_the_minimum_is_allowed(self, role):
        assert authorize(_bare_context(role=role), _fake_tool(min_role="admin")).allowed

    def test_an_unknown_role_is_denied(self):
        decision = authorize(_bare_context(role="intern"), _fake_tool())
        assert decision.reason is DenialReason.FORBIDDEN

    @pytest.mark.parametrize("role", ["viewer", "admin", "owner"])
    def test_a_high_risk_tool_is_denied_for_every_role(self, role):
        decision = authorize(_bare_context(role=role), _fake_tool(risk=Risk.HIGH))
        assert decision.reason is DenialReason.HIGH_RISK_DISABLED

    def test_a_low_risk_write_needs_the_users_confirmation(self):
        tool = _fake_tool(risk=Risk.WRITE_LOW, min_role="admin")
        ctx = _bare_context(role="admin")
        assert authorize(ctx, tool).reason is DenialReason.CONFIRMATION_REQUIRED

        confirmed = ToolContext(**{**ctx.__dict__, "confirmed": True})
        assert authorize(confirmed, tool).allowed

    def test_the_demo_account_cannot_write_even_when_confirmed(self, monkeypatch):
        monkeypatch.setattr(settings, "demo_viewer_email", "demo@example.com")
        tool = _fake_tool(risk=Risk.WRITE_LOW)
        ctx = _bare_context(email="demo@example.com")
        confirmed = ToolContext(**{**ctx.__dict__, "confirmed": True})
        assert authorize(confirmed, tool).reason is DenialReason.DEMO_READ_ONLY

    def test_the_decision_takes_no_confidence_score(self):
        # A score can't authorize anything because there is nowhere to pass one.
        import inspect

        assert list(inspect.signature(authorize).parameters) == ["ctx", "tool"]


@pytest.mark.db
class TestResolvingTheContext:
    def test_the_store_of_the_users_account_resolves(self, test_db_session, test_user, test_store):
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)
        assert ctx.store.id == test_store.id
        assert ctx.role == test_user.role

    def test_another_accounts_store_does_not_resolve(self, test_db_session, test_user, other_account):
        from app.models import Store

        foreign = Store(id=uuid4(), account_id=other_account.id, name="Foreign", platform="shopify")
        test_db_session.add(foreign)
        test_db_session.commit()

        with pytest.raises(StoreNotFound):
            resolve_context(test_db_session, test_user, foreign.id)

    def test_a_store_that_does_not_exist_answers_the_same_way(self, test_db_session, test_user):
        with pytest.raises(StoreNotFound):
            resolve_context(test_db_session, test_user, uuid4())

    def test_a_per_store_role_override_is_the_role_in_the_context(self, test_db_session, viewer_user, test_store):
        from app.models import StoreMembership

        test_db_session.add(StoreMembership(store_id=test_store.id, user_id=viewer_user.id, role="admin"))
        test_db_session.commit()

        assert resolve_context(test_db_session, viewer_user, test_store.id).role == "admin"


@pytest.mark.db
class TestRunningTheAnalyticsTools:
    def _seed(self, client, auth_header, store_id):
        orders = [
            {
                "order_id": "t-1",
                "time": "2026-03-05T10:00:00Z",
                "gross_amount": 100.0,
                "discounts": 5.0,
                "shipping_fee": 8.0,
                "payment_gateway_fee": 3.0,
                "cogs_total": 20.0,
                "currency": "USD",
            }
        ]
        assert client.post(f"/stores/{store_id}/orders", headers=auth_header, json=orders).status_code == 201
        spend = [{"time": "2026-03-04T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 10.0}]
        assert client.post(f"/stores/{store_id}/ad-spend", headers=auth_header, json=spend).status_code == 201

    def test_a_tool_returns_what_the_api_route_returns_for_the_same_window(
        self, client, auth_header, test_db_session, test_user, test_store
    ):
        self._seed(client, auth_header, test_store.id)
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        result = run_tool("pnl", ctx, {"days": 14})

        start = "2026-02-24T12:00:00+00:00"
        end = "2026-03-10T12:00:00+00:00"
        api = client.get(f"/stores/{test_store.id}/metrics/pnl", headers=auth_header, params={"start": start, "end": end})
        assert api.status_code == status.HTTP_200_OK
        assert result.ok is True
        assert result.data == api.json()
        assert result.data["net_profit"] == 64.0

    def test_the_result_says_where_the_figure_comes_from(self, test_db_session, test_user, test_store):
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        result = run_tool("summary", ctx, {"days": 7})

        assert result.ok is True
        assert result.tool == "summary"
        assert result.params == {"days": 7}
        assert result.as_of == NOW
        assert result.window == {"start": "2026-03-03T12:00:00+00:00", "end": "2026-03-10T12:00:00+00:00"}
        assert result.currency == test_store.currency

    def test_a_plan_without_the_feature_is_a_typed_denial_that_names_the_plan(
        self, test_db_session, test_account, test_user, test_store
    ):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="starter", status="active"))
        test_db_session.commit()
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        gated = run_tool("forecast", ctx)
        open_ = run_tool("summary", ctx)

        assert gated.ok is False
        assert gated.denial is DenialReason.PLAN_REQUIRED
        assert "Starter" in gated.message
        assert open_.ok is True

    def test_only_the_tools_the_plan_allows_are_offered(self, test_db_session, test_account, test_user, test_store):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="starter", status="active"))
        test_db_session.commit()
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        offered = {tool.name for tool in available_tools(ctx)}

        assert offered == {"summary", "pnl", "daily", "weekly_narrative"}

    def test_a_viewer_can_run_the_read_tools(self, test_db_session, viewer_user, test_store):
        ctx = resolve_context(test_db_session, viewer_user, test_store.id, now=NOW)
        assert run_tool("daily", ctx, {"days": 30}).ok is True

    def test_invalid_parameters_are_a_typed_denial_that_does_not_echo_the_value(
        self, test_db_session, test_user, test_store
    ):
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        result = run_tool("summary", ctx, {"store_id": "secret-value-123"})

        assert result.ok is False
        assert result.denial is DenialReason.INVALID_PARAMS
        assert "secret-value-123" not in result.message
        assert "store_id" in result.message

    def test_a_window_longer_than_the_ceiling_is_refused(self, test_db_session, test_user, test_store):
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        result = run_tool("summary", ctx, {"days": settings.assistant_max_window_days + 1})

        assert result.denial is DenialReason.INVALID_PARAMS

    def test_the_forecast_tool_returns_the_empty_forecast_when_there_is_not_enough_history(
        self, test_db_session, test_user, test_store
    ):
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        result = run_tool("forecast", ctx, {"history_days": 30, "forecast_days": 5})

        assert result.ok is True
        assert result.data["days"] == []
        assert result.data["true_roas"] is None

    def test_the_narrative_tool_returns_the_weekly_summary(self, test_db_session, test_user, test_store):
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        result = run_tool("weekly_narrative", ctx)

        assert result.ok is True
        assert set(result.data) == {"text", "mood", "source", "period_end", "facts"}
        assert result.data["period_end"] == "2026-03-10"
