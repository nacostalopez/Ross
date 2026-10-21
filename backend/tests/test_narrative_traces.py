"""The weekly narrative goes through run_tool, so each visit leaves a trace, and a rejected text is recorded."""

import pytest
from fastapi import status

from app.config import settings
from app.models import AssistantTrace
from app.services import weekly_narrative
from app.services.narrative_writers import Outcome, WriteResult

FACTS = {
    "currency": "ARS", "revenue": 1_500_000.0, "real_profit": 300_000.0, "ad_spend": 200_000.0, "true_roas": 2.5,
    "previous_revenue": 1_200_000.0, "previous_real_profit": 250_000.0, "revenue_change": 25.0,
    "profit_change": 20.0, "driver": None, "has_data": True,
}


class _Writer:
    provider = "fake"
    model = "fake-1"

    def __init__(self, text):
        self._text = text

    def write(self, request):
        return WriteResult(Outcome.WRITTEN, self._text, provider="fake", model="fake-1")


def _traces(db):
    return db.query(AssistantTrace).filter_by(tool="weekly_narrative").all()


@pytest.mark.db
class TestNarrativeTraces:
    def test_a_visit_leaves_one_trace_and_a_second_visit_another(self, client, auth_header, test_store, test_db_session, monkeypatch):
        monkeypatch.setattr(settings, "narrative_provider", "")
        url = f"/stores/{test_store.id}/narrative"
        assert client.get(url, headers=auth_header).status_code == status.HTTP_200_OK
        assert client.get(url, headers=auth_header).status_code == status.HTTP_200_OK

        rows = _traces(test_db_session)
        assert len(rows) == 2
        assert all(r.outcome == "ok" and not r.validator_rejected and r.store_id == test_store.id for r in rows)

    def test_a_text_the_validator_rejects_is_recorded(self, client, auth_header, test_store, test_db_session, monkeypatch):
        monkeypatch.setattr(weekly_narrative, "collect_facts", lambda *a, **k: dict(FACTS))
        # 25% is the change in sales, not in profit: the figure check rejects it and the template answers.
        monkeypatch.setattr(weekly_narrative, "build_writer", lambda _s: _Writer("La ganancia real subió un 25%."))

        response = client.get(f"/stores/{test_store.id}/narrative", headers=auth_header)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["source"] == "plantilla"
        (row,) = _traces(test_db_session)
        assert row.validator_rejected is True and row.outcome == "ok"

    def test_a_text_that_passes_is_not_marked_rejected(self, client, auth_header, test_store, test_db_session, monkeypatch):
        monkeypatch.setattr(weekly_narrative, "collect_facts", lambda *a, **k: dict(FACTS))
        monkeypatch.setattr(weekly_narrative, "build_writer", lambda _s: _Writer("La ganancia real subió un 20%."))

        response = client.get(f"/stores/{test_store.id}/narrative", headers=auth_header)

        assert response.json()["source"] == "ia"
        (row,) = _traces(test_db_session)
        assert row.validator_rejected is False

    def test_another_accounts_store_is_a_404_and_leaves_no_trace(self, client, test_store, other_user, test_db_session):
        login = client.post("/auth/login", json={"email": other_user.email, "password": "otherpassword123"})
        header = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get(f"/stores/{test_store.id}/narrative", headers=header).status_code == status.HTTP_404_NOT_FOUND
        assert _traces(test_db_session) == []
