"""run_tool leaves exactly one trace per call, for answers and denials, and recording never changes the answer."""
from datetime import datetime, timezone

import pytest

from app.ai import traces
from app.ai.tools import DenialReason, resolve_context, run_tool
from app.config import settings
from app.models import AssistantTrace, Subscription

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.db


def _rows(db, account_id=None):
    query = db.query(AssistantTrace)
    if account_id is not None:
        query = query.filter(AssistantTrace.account_id == account_id)
    return query.all()


@pytest.fixture
def ctx(test_db_session, test_user, test_store):
    return resolve_context(test_db_session, test_user, test_store.id, now=NOW)


class TestOneTracePerCall:
    def test_a_successful_call_leaves_one_ok_trace(self, test_db_session, test_user, ctx):
        result = run_tool("summary", ctx, {"days": 7})

        assert result.ok is True
        (row,) = _rows(test_db_session, test_user.account_id)
        assert row.tool == "summary" and row.outcome == "ok"
        assert row.param_shape == {"days": 7}
        assert row.latency_ms >= 0

    def test_an_unknown_tool_leaves_one_trace(self, test_db_session, test_user, ctx):
        result = run_tool("nope", ctx, {"days": 3})

        assert result.denial is DenialReason.UNKNOWN_TOOL
        (row,) = _rows(test_db_session, test_user.account_id)
        assert row.tool == "nope" and row.outcome == "unknown_tool"
        assert row.param_shape == {}

    def test_a_policy_denial_leaves_one_trace(self, test_db_session, test_account, test_user, test_store):
        test_db_session.add(Subscription(account_id=test_account.id, plan_id="starter", status="active"))
        test_db_session.commit()
        ctx = resolve_context(test_db_session, test_user, test_store.id, now=NOW)

        result = run_tool("forecast", ctx)

        assert result.denial is DenialReason.PLAN_REQUIRED
        (row,) = _rows(test_db_session, test_user.account_id)
        assert row.tool == "forecast" and row.outcome == "plan_required"

    def test_invalid_params_leave_one_trace_without_any_of_their_values(self, test_db_session, test_user, ctx):
        too_long = settings.assistant_max_window_days + 1
        result = run_tool("summary", ctx, {"days": too_long, "store_id": "secret-value-123"})

        assert result.denial is DenialReason.INVALID_PARAMS
        (row,) = _rows(test_db_session, test_user.account_id)
        assert row.outcome == "invalid_params"
        assert row.param_shape == {}

    def test_two_calls_leave_two_traces(self, test_db_session, test_user, ctx):
        run_tool("summary", ctx)
        run_tool("summary", ctx)
        assert len(_rows(test_db_session, test_user.account_id)) == 2


class TestWhoTheTraceBelongsTo:
    def test_account_store_and_role_come_from_the_context(self, test_db_session, test_user, test_store, ctx):
        run_tool("summary", ctx)
        (row,) = _rows(test_db_session, test_user.account_id)
        assert row.account_id == test_user.account_id
        assert row.store_id == test_store.id
        assert row.role == ctx.role

    def test_a_viewers_trace_carries_the_viewers_role(self, test_db_session, viewer_user, test_store):
        ctx = resolve_context(test_db_session, viewer_user, test_store.id, now=NOW)
        run_tool("summary", ctx)
        (row,) = _rows(test_db_session, viewer_user.account_id)
        assert row.account_id == viewer_user.account_id
        assert row.role == ctx.role


class TestRecordingNeverGetsInTheWay:
    def test_the_demo_user_leaves_no_trace(self, test_db_session, test_user, ctx, monkeypatch):
        monkeypatch.setattr("app.dependencies.settings.demo_viewer_email", test_user.email)
        result = run_tool("summary", ctx)
        assert result.ok is True
        assert _rows(test_db_session) == []

    def test_a_failing_record_does_not_change_the_answer(self, ctx, monkeypatch):
        expected = run_tool("summary", ctx)

        def boom(*args, **kwargs):
            raise RuntimeError("recording is down")

        monkeypatch.setattr(traces, "record_trace", boom)
        assert run_tool("summary", ctx) == expected

    def test_a_broken_commit_does_not_change_the_answer(self, test_db_session, ctx, monkeypatch):
        expected = run_tool("summary", ctx)
        before = len(_rows(test_db_session))

        def broken_commit():
            raise RuntimeError("db down")

        with monkeypatch.context() as patch:
            patch.setattr(test_db_session, "commit", broken_commit)
            result = run_tool("summary", ctx)

        assert result == expected
        assert len(_rows(test_db_session)) == before


class TestAToolThatBreaks:
    def test_the_error_reaches_the_caller_and_leaves_a_trace(self, test_db_session, test_user, ctx, monkeypatch):
        from app.ai.tools import registry
        from app.ai.tools.base import NoParams, Tool

        def explode(_ctx, _params):
            raise RuntimeError("falló la consulta")

        broken = Tool(name="roto", module="analytics", description="x", params=NoParams, result=dict, run=explode)
        monkeypatch.setitem(registry._TOOLS, "roto", broken)

        with pytest.raises(RuntimeError):
            run_tool("roto", ctx, {})

        (row,) = _rows(test_db_session, test_user.account_id)
        assert row.tool == "roto" and row.outcome == "error"
