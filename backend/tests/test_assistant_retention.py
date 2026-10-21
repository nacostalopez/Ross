"""Retention of the assistant's traces and events: 180 days (docs/ia/fase-4-2-diseno.md, decision 11)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.ai import traces
from app.cli import purge_assistant_traces
from app.models import AssistantEvent, AssistantTrace

pytestmark = pytest.mark.db


def _ago(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


def _trace(db, account, days, tool="weekly_narrative"):
    row = AssistantTrace(
        id=uuid4(), account_id=account.id, tool=tool, outcome="ok", latency_ms=5, role="owner", created_at=_ago(days)
    )
    db.add(row)
    db.commit()
    return row


def _event(db, trace, user, days, kind="useful"):
    row = AssistantEvent(
        id=uuid4(), trace_id=trace.id, account_id=trace.account_id, user_id=user.id, kind=kind, created_at=_ago(days)
    )
    db.add(row)
    db.commit()
    return row


def _ids(db, model):
    return {r.id for r in db.query(model).all()}


class TestPurgeExpired:
    def test_old_rows_go_and_recent_ones_stay(self, test_db_session, test_account, test_user):
        old = _trace(test_db_session, test_account, 181)
        edge = _trace(test_db_session, test_account, 179)
        old_event_id = _event(test_db_session, old, test_user, 181).id
        edge_event = _event(test_db_session, edge, test_user, 179)

        counts = traces.purge_expired(test_db_session)

        assert counts["traces"] == 1 and counts["events"] >= 1
        assert _ids(test_db_session, AssistantTrace) == {edge.id}
        assert _ids(test_db_session, AssistantEvent) == {edge_event.id}
        assert old_event_id not in _ids(test_db_session, AssistantEvent)

    def test_the_events_of_a_deleted_trace_go_with_it(self, test_db_session, test_account, test_user):
        old = _trace(test_db_session, test_account, 200)
        # The event itself is recent, but its trace has expired.
        _event(test_db_session, old, test_user, 1)

        traces.purge_expired(test_db_session)

        assert test_db_session.query(AssistantTrace).count() == 0
        assert test_db_session.query(AssistantEvent).count() == 0

    def test_an_old_event_on_a_recent_trace_goes_alone(self, test_db_session, test_account, test_user):
        fresh = _trace(test_db_session, test_account, 10)
        _event(test_db_session, fresh, test_user, 190)

        counts = traces.purge_expired(test_db_session)

        assert counts == {"events": 1, "traces": 0}
        assert _ids(test_db_session, AssistantTrace) == {fresh.id}

    def test_another_accounts_fresh_rows_are_untouched(
        self, test_db_session, test_account, test_user, other_account, other_user
    ):
        _trace(test_db_session, test_account, 300)
        mine = _trace(test_db_session, other_account, 5)
        mine_event = _event(test_db_session, mine, other_user, 5)

        traces.purge_expired(test_db_session)

        assert _ids(test_db_session, AssistantTrace) == {mine.id}
        assert _ids(test_db_session, AssistantEvent) == {mine_event.id}

    def test_running_twice_is_harmless(self, test_db_session, test_account, test_user):
        old = _trace(test_db_session, test_account, 400)
        _event(test_db_session, old, test_user, 400)
        keep = _trace(test_db_session, test_account, 1)

        first = traces.purge_expired(test_db_session)
        second = traces.purge_expired(test_db_session)

        assert first["traces"] == 1
        assert second == {"events": 0, "traces": 0}
        assert _ids(test_db_session, AssistantTrace) == {keep.id}

    def test_an_empty_table_is_fine(self, test_db_session):
        assert traces.purge_expired(test_db_session) == {"events": 0, "traces": 0}


class TestCli:
    def test_it_purges_and_logs_only_counts(self, test_db_session, test_account, test_user, monkeypatch, capsys):
        old = _trace(test_db_session, test_account, 250, tool="secret_tool_name")
        old_id = str(old.id)
        _event(test_db_session, old, test_user, 250)
        keep = _trace(test_db_session, test_account, 2)
        monkeypatch.setattr(test_db_session, "close", lambda: None)

        counts = purge_assistant_traces.main(session_factory=lambda: test_db_session)

        assert counts["traces"] == 1
        assert _ids(test_db_session, AssistantTrace) == {keep.id}
        out = capsys.readouterr().out
        line = next(ln for ln in out.splitlines() if "assistant_traces_purged" in ln)
        assert '"traces": 1' in line and '"events": 1' in line
        assert old_id not in out and "secret_tool_name" not in out

    def test_it_closes_the_session(self, test_db_session):
        closed = []
        monkey_session = test_db_session
        original = monkey_session.close
        monkey_session.close = lambda: closed.append(True)
        try:
            purge_assistant_traces.main(session_factory=lambda: monkey_session)
        finally:
            monkey_session.close = original
        assert closed == [True]
