"""The endpoints to mark an assistant answer and to read a tool's score (docs/ia/fase-4-2-diseno.md)."""
from uuid import uuid4

import pytest

from app.ai import traces
from app.models import AssistantEvent, AssistantTrace, User
from app.security import hash_password

TOOL = "weekly_narrative"


def _trace(db, user, tool=TOOL):
    trace_id = traces.record_trace(db, user=user, store_id=None, tool=tool, outcome="ok", latency_ms=5, role="owner")
    assert trace_id is not None
    return trace_id


def _mark(client, headers, trace_id, kind):
    return client.post(f"/assistant/traces/{trace_id}/events", json={"kind": kind}, headers=headers)


def _verdicts(db, account, owner, useful, not_useful):
    """Different people of `account` each judge a trace once."""
    for i in range(useful + not_useful):
        person = User(
            id=uuid4(),
            account_id=account.id,
            email=f"mate-{uuid4().hex[:8]}@example.com",
            hashed_password=hash_password("x" * 12),
            role="viewer",
        )
        db.add(person)
        db.commit()
        kind = "useful" if i < useful else "not_useful"
        traces.record_event(db, user=person, trace_id=_trace(db, owner), kind=kind)


@pytest.mark.db
class TestMarking:
    def test_an_event_is_created_and_repeating_it_changes_nothing(self, client, test_db_session, test_user, auth_header):
        trace_id = _trace(test_db_session, test_user)
        assert _mark(client, auth_header, trace_id, "useful").status_code == 204
        assert _mark(client, auth_header, trace_id, "useful").status_code == 204
        assert test_db_session.query(AssistantEvent).count() == 1

    def test_a_new_verdict_replaces_the_old_one(self, client, test_db_session, test_user, auth_header):
        trace_id = _trace(test_db_session, test_user)
        _mark(client, auth_header, trace_id, "useful")
        assert _mark(client, auth_header, trace_id, "not_useful").status_code == 204
        assert [e.kind for e in test_db_session.query(AssistantEvent)] == ["not_useful"]

    def test_a_viewer_may_mark(self, client, test_db_session, test_user, viewer_auth_header):
        trace_id = _trace(test_db_session, test_user)
        assert _mark(client, viewer_auth_header, trace_id, "shown").status_code == 204

    @pytest.mark.parametrize("kind", ["acted", "nonsense"])
    def test_a_kind_that_is_reserved_or_unknown_is_rejected(self, client, test_db_session, test_user, auth_header, kind):
        trace_id = _trace(test_db_session, test_user)
        assert _mark(client, auth_header, trace_id, kind).status_code == 422
        assert test_db_session.query(AssistantEvent).count() == 0

    def test_a_trace_of_another_account_and_a_missing_one_answer_the_same(
        self, client, test_db_session, test_user, other_user, auth_header
    ):
        foreign = _mark(client, auth_header, _trace(test_db_session, other_user), "useful")
        missing = _mark(client, auth_header, uuid4(), "useful")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()
        assert test_db_session.query(AssistantEvent).count() == 0

    def test_without_a_session_it_is_refused(self, client, test_db_session, test_user):
        response = _mark(client, {}, _trace(test_db_session, test_user), "useful")
        assert response.status_code in (401, 403)
        assert test_db_session.query(AssistantEvent).count() == 0

    def test_the_demo_account_writes_nothing(self, client, test_db_session, test_user, auth_header, monkeypatch):
        trace_id = _trace(test_db_session, test_user)
        monkeypatch.setattr("app.dependencies.settings.demo_viewer_email", test_user.email)
        assert _mark(client, auth_header, trace_id, "useful").status_code == 403
        assert test_db_session.query(AssistantEvent).count() == 0
        assert test_db_session.query(AssistantTrace).count() == 1


@pytest.mark.db
class TestScore:
    def test_without_enough_samples_there_is_no_number(self, client, test_db_session, test_user, test_account, auth_header):
        _verdicts(test_db_session, test_account, test_user, 5, 1)
        body = client.get(f"/assistant/tools/{TOOL}/score", headers=auth_header).json()
        assert body["status"] == "sin_datos" and body["score"] is None
        assert body["samples"] == 6 and body["useful"] == 5 and body["not_useful"] == 1
        assert body["tool"] == TOOL and body["tier"] == "L0" and body["runs"] == 6

    def test_with_twenty_samples_it_is_a_number(self, client, test_db_session, test_user, test_account, admin_auth_header):
        _verdicts(test_db_session, test_account, test_user, 15, 5)
        response = client.get(f"/assistant/tools/{TOOL}/score", params={"tier": "L0"}, headers=admin_auth_header)
        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "ok" and body["score"] == pytest.approx(0.75) and body["samples"] == 20

    def test_it_never_mixes_accounts(self, client, test_db_session, test_user, other_user, other_account, auth_header):
        _verdicts(test_db_session, other_account, other_user, 20, 0)
        body = client.get(f"/assistant/tools/{TOOL}/score", headers=auth_header).json()
        assert body["runs"] == 0 and body["samples"] == 0 and body["score"] is None

    def test_a_viewer_is_denied(self, client, viewer_auth_header):
        assert client.get(f"/assistant/tools/{TOOL}/score", headers=viewer_auth_header).status_code == 403

    def test_without_a_session_it_is_refused(self, client):
        assert client.get(f"/assistant/tools/{TOOL}/score").status_code in (401, 403)

    def test_an_unknown_tool_is_not_found(self, client, auth_header):
        assert client.get("/assistant/tools/no_such_tool/score", headers=auth_header).status_code == 404
