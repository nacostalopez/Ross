"""Traces, events and the utility score (docs/ia/fase-4-2-diseno.md)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.ai import traces
from app.models import AssistantEvent, AssistantTrace, User
from app.security import hash_password


def _trace(db, user, tool="weekly_narrative", **kw):
    trace_id = traces.record_trace(
        db, user=user, store_id=None, tool=tool, outcome=kw.pop("outcome", "ok"), latency_ms=12, role="owner", **kw
    )
    assert trace_id is not None
    return trace_id


def _teammate(db, account):
    user = User(
        id=uuid4(),
        account_id=account.id,
        email=f"mate-{uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("x" * 12),
        role="viewer",
    )
    db.add(user)
    db.commit()
    return user


def _verdicts(db, account, owner, useful, not_useful, tool="weekly_narrative"):
    """`useful` + `not_useful` different people each judge a trace once."""
    for i in range(useful + not_useful):
        trace_id = _trace(db, owner, tool=tool)
        kind = "useful" if i < useful else "not_useful"
        traces.record_event(db, user=_teammate(db, account), trace_id=trace_id, kind=kind)


class TestParamShape:
    def test_keeps_only_bounded_integers(self):
        assert traces.param_shape({"days": 14, "q": "ventas de Ana", "other": 5, "flag": True}) == {"days": 14}

    def test_a_string_in_a_bounded_name_is_dropped(self):
        assert traces.param_shape({"days": "catorce"}) == {}

    def test_none_is_empty(self):
        assert traces.param_shape(None) == {}


@pytest.mark.db
class TestRecording:
    def test_a_trace_keeps_the_structure_only(self, test_db_session, test_user):
        trace_id = _trace(test_db_session, test_user, params={"days": 14, "note": "texto libre"})
        row = test_db_session.get(AssistantTrace, trace_id)
        assert row.param_shape == {"days": 14}
        assert row.account_id == test_user.account_id and row.tier == "L0" and row.provider == "template"

    def test_a_failure_to_record_is_swallowed(self, test_db_session, test_user):
        saved = traces.record_trace(
            test_db_session, user=test_user, store_id=None, tool="t", outcome="ok", latency_ms=1, role="x" * 50
        )
        assert saved is None
        # The session is still usable afterwards.
        assert test_db_session.query(AssistantTrace).count() == 0

    def test_the_demo_user_leaves_nothing(self, test_db_session, test_user, monkeypatch):
        monkeypatch.setattr("app.dependencies.settings.demo_viewer_email", test_user.email)
        saved = traces.record_trace(
            test_db_session, user=test_user, store_id=None, tool="t", outcome="ok", latency_ms=1, role="viewer"
        )
        assert saved is None
        assert test_db_session.query(AssistantTrace).count() == 0


@pytest.mark.db
class TestEvents:
    def test_one_event_per_person_trace_and_kind(self, test_db_session, test_user):
        trace_id = _trace(test_db_session, test_user)
        traces.record_event(test_db_session, user=test_user, trace_id=trace_id, kind="useful")
        traces.record_event(test_db_session, user=test_user, trace_id=trace_id, kind="useful")
        assert test_db_session.query(AssistantEvent).count() == 1

    def test_a_new_verdict_replaces_the_old_one(self, test_db_session, test_user):
        trace_id = _trace(test_db_session, test_user)
        traces.record_event(test_db_session, user=test_user, trace_id=trace_id, kind="useful")
        traces.record_event(test_db_session, user=test_user, trace_id=trace_id, kind="not_useful")
        assert [e.kind for e in test_db_session.query(AssistantEvent)] == ["not_useful"]

    def test_acted_is_reserved(self, test_db_session, test_user):
        trace_id = _trace(test_db_session, test_user)
        with pytest.raises(ValueError):
            traces.record_event(test_db_session, user=test_user, trace_id=trace_id, kind="acted")

    def test_a_trace_of_another_account_is_not_found(self, test_db_session, test_user, other_user):
        trace_id = _trace(test_db_session, other_user)
        assert traces.record_event(test_db_session, user=test_user, trace_id=trace_id, kind="useful") is None
        assert test_db_session.query(AssistantEvent).count() == 0


@pytest.mark.db
class TestScore:
    def test_below_the_minimum_there_is_no_number(self, test_db_session, test_user, test_account):
        _verdicts(test_db_session, test_account, test_user, 3, 0)
        score = traces.utility_score(test_db_session, account_id=test_account.id, tool="weekly_narrative")
        assert score.samples == 3 and score.score is None and score.status == "sin_datos"

    def test_with_enough_samples_it_is_the_share_of_useful(self, test_db_session, test_user, test_account):
        _verdicts(test_db_session, test_account, test_user, 15, 5)
        score = traces.utility_score(test_db_session, account_id=test_account.id, tool="weekly_narrative")
        assert score.samples == 20 and score.score == pytest.approx(0.75) and score.status == "ok"

    def test_old_verdicts_fall_out_of_the_window(self, test_db_session, test_user, test_account):
        _verdicts(test_db_session, test_account, test_user, 20, 0)
        later = datetime.now(timezone.utc) + timedelta(days=31)
        score = traces.utility_score(test_db_session, account_id=test_account.id, tool="weekly_narrative", now=later)
        assert score.samples == 0 and score.score is None

    def test_another_accounts_data_does_not_count(self, test_db_session, test_user, test_account, other_account):
        _verdicts(test_db_session, test_account, test_user, 20, 0)
        score = traces.utility_score(test_db_session, account_id=other_account.id, tool="weekly_narrative")
        assert score.runs == 0 and score.samples == 0

    def test_it_reports_the_validator_rejection_rate(self, test_db_session, test_user, test_account):
        _trace(test_db_session, test_user)
        _trace(test_db_session, test_user, validator_rejected=True)
        score = traces.utility_score(test_db_session, account_id=test_account.id, tool="weekly_narrative")
        assert score.runs == 2 and score.validator_rejection_rate == 0.5


@pytest.mark.db
class TestPrivacyHooks:
    def test_the_export_lists_my_marks_without_content(self, client, test_db_session, test_user, auth_header):
        trace_id = _trace(test_db_session, test_user, params={"days": 14})
        traces.record_event(test_db_session, user=test_user, trace_id=trace_id, kind="useful")
        body = client.get("/privacy/export", headers=auth_header).json()
        assert [(e["tool"], e["kind"]) for e in body["assistant_events"]] == [("weekly_narrative", "useful")]
        assert "param_shape" not in str(body)
