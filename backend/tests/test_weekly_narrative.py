"""Tests for "Ross explica tu semana" (app/services/weekly_narrative.py, app/routes/narrative.py)."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import status

from app.config import settings
from app.models import WeeklyNarrative
from app.services import weekly_narrative
from app.services.weekly_narrative import (
    _prompt_lines,
    _uses_only_known_figures,
    format_money,
    mood_for,
    template_text,
)

# The window ends on 2026-03-13: "this week" is Mar 6-12, "last week" Feb 27-Mar 5.
NOW = datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc)


def _facts(**overrides):
    facts = {
        "currency": "ARS",
        "revenue": 1_500_000.0,
        "real_profit": 300_000.0,
        "ad_spend": 200_000.0,
        "true_roas": 2.5,
        "previous_revenue": 1_200_000.0,
        "previous_real_profit": 250_000.0,
        "revenue_change": 25.0,
        "profit_change": 20.0,
        "driver": None,
        "has_data": True,
    }
    facts.update(overrides)
    return facts


class TestFormatting:
    def test_money_uses_argentine_thousands_separator(self):
        assert format_money(1234567.4, "ARS") == "$1.234.567"
        assert format_money(-2500, "USD") == "-US$2.500"
        assert format_money(10, "CLP") == "CLP 10"


class TestMood:
    def test_celebrates_a_profit_that_grew(self):
        assert mood_for(_facts(profit_change=8.0)) == "festeja"

    def test_worries_about_a_loss(self):
        assert mood_for(_facts(real_profit=-50_000.0, profit_change=None)) == "preocupada"

    def test_worries_about_a_big_drop(self):
        assert mood_for(_facts(profit_change=-15.0)) == "preocupada"

    def test_neutral_for_a_flat_week(self):
        assert mood_for(_facts(profit_change=2.0)) == "neutral"

    def test_neutral_without_data(self):
        assert mood_for(_facts(has_data=False)) == "neutral"


class TestTemplate:
    def test_mentions_profit_change_driver_and_roas(self):
        text = template_text(_facts(driver={"label": "el costo de envío", "increase": 40_000.0}))
        assert "vendiste $1.500.000" in text
        assert "$300.000 de ganancia real, un 20% más que la semana anterior" in text
        assert "el costo de envío: $40.000 más" in text
        assert "True ROAS fue de 2,50" in text

    def test_reports_a_loss_plainly(self):
        text = template_text(_facts(real_profit=-80_000.0, profit_change=None))
        assert "perdiste $80.000" in text

    def test_explains_how_to_get_started_without_data(self):
        assert "Cuando conectes tu tienda" in template_text(_facts(has_data=False))


class TestKnownFigures:
    def test_accepts_figures_copied_from_the_data(self):
        lines = _prompt_lines(_facts())
        text = "Esta semana te quedaron $300.000 de ganancia real, un 20% más. Tu True ROAS fue de 2,50."
        assert _uses_only_known_figures(text, lines)

    def test_rejects_an_invented_figure(self):
        lines = _prompt_lines(_facts())
        assert not _uses_only_known_figures("Ganaste $310.000 en los últimos 7 días.", lines)


def _seed(client, auth_header, store_id):
    orders = [
        {"order_id": "prev-1", "time": "2026-03-02T12:00:00Z", "gross_amount": 1000.0, "currency": "USD"},
        {
            "order_id": "cur-1",
            "time": "2026-03-09T12:00:00Z",
            "gross_amount": 1500.0,
            "shipping_fee": 100.0,
            "currency": "USD",
        },
    ]
    assert client.post(f"/stores/{store_id}/orders", headers=auth_header, json=orders).status_code == 201
    spend = [{"time": "2026-03-09T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 200.0}]
    assert client.post(f"/stores/{store_id}/ad-spend", headers=auth_header, json=spend).status_code == 201


def _fake_claude(monkeypatch, text, stop_reason="end_turn"):
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])

    class FakeClient:
        def __init__(self, **kwargs):
            self.beta = SimpleNamespace(messages=FakeMessages())

    monkeypatch.setattr(weekly_narrative.anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    return calls


@pytest.mark.db
class TestGetOrBuild:
    def test_uses_the_template_without_an_api_key(self, client, auth_header, test_store, test_db_session, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        _seed(client, auth_header, test_store.id)

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert narrative.source == "plantilla"
        assert narrative.mood == "festeja"
        assert "vendiste US$1.500" in narrative.text
        assert narrative.facts[0] == {"label": "Ventas", "value": "US$1.500", "change_pct": 50.0}
        assert test_db_session.get(WeeklyNarrative, (test_store.id, date(2026, 3, 13))) is not None

    def test_keeps_claudes_text_when_it_only_uses_known_figures(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        _seed(client, auth_header, test_store.id)
        calls = _fake_claude(monkeypatch, "Vendiste US$1.500 y te quedaron US$1.200 de ganancia real.")

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert narrative.source == "ia"
        assert narrative.text.startswith("Vendiste US$1.500")
        assert calls[0]["model"] == settings.narrative_model
        assert calls[0]["fallbacks"] == "default"

    def test_falls_back_and_retries_later_when_claude_invents_a_figure(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        _seed(client, auth_header, test_store.id)
        _fake_claude(monkeypatch, "Ganaste US$9.999 esta semana.")

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert narrative.source == "plantilla"
        assert "9.999" not in narrative.text
        # Not cached: the next visit gets another chance at Claude's version.
        assert test_db_session.get(WeeklyNarrative, (test_store.id, date(2026, 3, 13))) is None

    def test_reuses_the_days_summary(self, client, auth_header, test_store, test_db_session, monkeypatch):
        _seed(client, auth_header, test_store.id)
        calls = _fake_claude(monkeypatch, "Vendiste US$1.500 en los últimos 7 días.")

        weekly_narrative.get_or_build(test_db_session, test_store, NOW)
        weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert len(calls) == 1

    def test_does_not_call_claude_without_data(self, test_store, test_db_session, monkeypatch):
        calls = _fake_claude(monkeypatch, "irrelevante")

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert calls == []
        assert narrative.mood == "neutral"


@pytest.mark.db
class TestNarrativeRoute:
    def test_returns_text_mood_and_facts(self, client, auth_header, test_store, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        response = client.get(f"/stores/{test_store.id}/narrative", headers=auth_header)

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["mood"] == "neutral"
        assert body["source"] == "plantilla"
        assert [fact["label"] for fact in body["facts"]] == ["Ventas", "Ganancia real", "Publicidad"]

    def test_other_accounts_cannot_read_it(self, client, test_store, other_user):
        login = client.post("/auth/login", json={"email": other_user.email, "password": "otherpassword123"})
        header = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get(f"/stores/{test_store.id}/narrative", headers=header)

        assert response.status_code == status.HTTP_404_NOT_FOUND
