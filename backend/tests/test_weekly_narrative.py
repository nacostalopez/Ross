"""Tests for "Ross explica tu semana" (app/services/weekly_narrative.py, app/routes/narrative.py)."""

import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import status
from pydantic import ValidationError

from app.config import Settings, settings
from app.models import WeeklyNarrative
from app.services import weekly_narrative
from app.services.narrative_validator import check_figures
from app.services.narrative_writers import (
    NarrativeRequest,
    NarrativeService,
    Outcome,
    TemplateNarrativeWriter,
    WriteResult,
    build_writer,
    resolve_provider,
)
from app.services.weekly_narrative import (
    SYSTEM_PROMPT,
    _prompt_lines,
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

    @pytest.mark.parametrize(
        "label, verb",
        [
            ("los descuentos", "fueron"),
            ("las comisiones", "fueron"),
            ("el costo de envío", "fue"),
            ("el costo de mercadería", "fue"),
            ("el gasto en publicidad", "fue"),
        ],
    )
    def test_the_verb_agrees_with_the_cost_that_grew(self, label, verb):
        text = template_text(_facts(driver={"label": label, "increase": 40_000.0}))
        assert f"Lo que más subió {verb} {label}:" in text


_WITH_DRIVER = _facts(driver={"label": "las comisiones", "increase": 45_000.0})


class TestKnownFigures:
    """The figure check, probed with texts a model could plausibly write. Each case says whether
    the owner may be shown it."""

    @pytest.mark.parametrize(
        "text, problem",
        [
            # Right
            ("Te quedaron $300.000 de ganancia real.", None),
            ("La ganancia real subió un 20%.", None),
            ("Ganaste 300000 pesos.", None),
            ("Ganaste 300 mil pesos.", None),
            ("Vendiste 1,5 millones.", None),
            ("Tu True ROAS fue de 2,50.", None),
            ("Tu True ROAS fue de 2.50.", None),
            ("Vendiste $1.500.000 y te quedaron $300.000 de ganancia real, un 20% más.", None),
            ("Las ventas subieron un 25% y la ganancia real un 20%.", None),
            ("Invertiste $200.000 en publicidad en los últimos 7 días.", None),
            ("Lo que más subió fueron las comisiones: $45.000 más que la semana anterior.", None),
            # Wrong
            ("Te quedaron $310.000 de ganancia real.", "unknown_figure"),
            ("La ganancia real subió un 33%.", "unknown_figure"),
            ("Gastaste $20.000 en publicidad.", "unknown_figure"),
            ("Ganaste 300 mil pesos y 1 millón.", "unknown_figure"),
            ("Tu True ROAS fue de 2,60.", "unknown_figure"),
            ("La ganancia real subió un 25%.", "wrong_metric"),  # 25 is the sales change
            ("Gastaste $300.000 en publicidad.", "wrong_metric"),  # real, but it is the profit
            ("Vendiste $300.000.", "wrong_metric"),
            ("Perdiste $300.000 esta semana.", "wrong_sign"),
            ("La ganancia real bajó un 20%.", "wrong_direction"),
            ("Las ventas bajaron un 25%.", "wrong_direction"),
            ("La ganancia real subió un 20% menos que antes.", "wrong_direction"),
        ],
    )
    def test_probes(self, text, problem):
        assert check_figures(text, _WITH_DRIVER) == problem

    def test_a_loss_told_as_profit_is_rejected(self):
        facts = _facts(real_profit=-80_000.0, profit_change=None)
        assert check_figures("Te quedaron $80.000 de ganancia real.", facts) == "wrong_sign"
        assert check_figures("Perdiste $80.000 esta semana.", facts) is None

    def test_a_percentage_the_data_does_not_have_is_rejected(self):
        facts = _facts(profit_change=None)
        assert check_figures("La ganancia real subió un 20%.", facts) == "unknown_figure"

    def test_a_cost_has_no_percentage(self):
        assert check_figures("Las comisiones subieron un 20%.", _WITH_DRIVER) == "wrong_metric"

    def test_text_without_figures_passes(self):
        assert check_figures("Buena semana, seguí así.", _facts()) is None

    @pytest.mark.parametrize(
        "facts",
        [
            _facts(),
            _WITH_DRIVER,
            _facts(real_profit=120_000.0, profit_change=-52.0, revenue_change=-18.0, true_roas=0.6),
            _facts(real_profit=-80_000.0, profit_change=None, true_roas=-0.4),
            _facts(profit_change=0.4, revenue_change=1.0),
            _facts(previous_revenue=0.0, previous_real_profit=0.0, revenue_change=None, profit_change=None),
            _facts(ad_spend=0.0, true_roas=None),
            _facts(currency="USD", revenue=18_400.0, real_profit=3_150.0, ad_spend=2_300.0, true_roas=1.9),
            _facts(revenue=48_250_000.0, real_profit=9_800_000.0, ad_spend=6_100_000.0),
            _facts(revenue=4_300.0, real_profit=900.0, ad_spend=700.0, true_roas=1.3),
            _facts(driver={"label": "el gasto en publicidad", "increase": 30_000.0}),
        ],
    )
    def test_the_template_always_passes_its_own_check(self, facts):
        assert check_figures(template_text(facts), facts) is None


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


class FakeWriter:
    """A stand-in for a provider: returns the result it was given, or raises."""

    def __init__(self, result=None, error=None, provider="fake", model="fake-1"):
        self.provider = provider
        self.model = model
        self._result = result
        self._error = error
        self.calls = []

    def write(self, request):
        self.calls.append(request)
        if self._error:
            raise self._error
        return self._result


def written(text, provider="fake", model="fake-1"):
    return WriteResult(Outcome.WRITTEN, text, provider=provider, model=model)


def failed(reason="timeout"):
    return WriteResult(Outcome.FAILED, provider="fake", model="fake-1", reason=reason)


def _use_writer(monkeypatch, writer):
    monkeypatch.setattr(weekly_narrative, "build_writer", lambda _settings: writer)
    return writer


def _service(writer):
    return NarrativeService(writer, TemplateNarrativeWriter(template_text), check_figures)


def _request(**facts):
    f = _facts(**facts)
    return NarrativeRequest(facts=f, lines=_prompt_lines(f), system_prompt=SYSTEM_PROMPT)


class TestNarrativeService:
    def test_uses_the_template_when_no_writer_is_configured(self):
        result = _service(None).generate(_request())
        assert (result.source, result.provider, result.model, result.cacheable) == ("plantilla", "template", None, True)
        assert "vendiste $1.500.000" in result.text

    def test_does_not_call_the_writer_when_told_not_to(self):
        writer = FakeWriter(written("irrelevante"))
        result = _service(writer).generate(_request(), use_model=False)
        assert writer.calls == []
        assert result.source == "plantilla" and result.cacheable

    def test_keeps_a_writers_text_that_only_uses_known_figures(self):
        writer = FakeWriter(written("Te quedaron $300.000 de ganancia real."))
        result = _service(writer).generate(_request())
        assert (result.source, result.provider, result.model, result.cacheable) == ("ia", "fake", "fake-1", True)
        assert result.text == "Te quedaron $300.000 de ganancia real."
        assert writer.calls[0].system_prompt == SYSTEM_PROMPT

    def test_a_failed_writer_gives_the_template_and_is_not_cached(self):
        result = _service(FakeWriter(failed("rate_limited"))).generate(_request())
        assert (result.source, result.cacheable, result.reason) == ("plantilla", False, "rate_limited")

    def test_a_disabled_writer_is_a_normal_template_and_is_cached(self):
        disabled = WriteResult(Outcome.DISABLED, provider="fake")
        result = _service(FakeWriter(disabled)).generate(_request())
        assert (result.source, result.cacheable, result.reason) == ("plantilla", True, "disabled")

    def test_an_invented_figure_gives_the_template_and_is_not_cached(self):
        result = _service(FakeWriter(written("Ganaste $310.000 esta semana."))).generate(_request())
        assert (result.source, result.cacheable, result.reason) == ("plantilla", False, "rejected_unknown_figure")
        assert "310.000" not in result.text

    def test_an_empty_text_is_not_used(self):
        result = _service(FakeWriter(written("   "))).generate(_request())
        assert result.source == "plantilla" and not result.cacheable

    def test_a_writer_that_raises_never_reaches_the_caller(self):
        result = _service(FakeWriter(error=RuntimeError("boom"))).generate(_request())
        assert (result.source, result.cacheable, result.reason) == ("plantilla", False, "unexpected_error")


class TestProviderChoice:
    def test_no_provider_means_the_template(self):
        assert resolve_provider("") == "template"
        assert build_writer(SimpleNamespace(narrative_provider="")) is None

    def test_template_can_be_asked_for_by_name(self):
        assert resolve_provider(" Template ") == "template"
        assert build_writer(SimpleNamespace(narrative_provider="template")) is None

    def test_a_provider_that_does_not_exist_stops_the_settings_from_loading(self):
        with pytest.raises(ValidationError):
            Settings(narrative_provider="anthropic")

    def test_a_typo_in_the_provider_stops_the_settings_from_loading(self):
        with pytest.raises(ValidationError):
            Settings(narrative_provider="olama")


class TestNoAiDependency:
    def test_the_narrative_works_without_the_anthropic_package(self):
        # Setting sys.modules["anthropic"] to None makes any `import anthropic` raise ImportError.
        code = (
            "import sys; sys.modules['anthropic'] = None\n"
            "from app.services import weekly_narrative as wn\n"
            "svc = wn.narrative_service()\n"
            "f = {'currency': 'ARS', 'revenue': 1.0, 'real_profit': 1.0, 'ad_spend': 0.0, 'true_roas': None,\n"
            "     'previous_revenue': 0.0, 'previous_real_profit': 0.0, 'revenue_change': None,\n"
            "     'profit_change': None, 'driver': None, 'has_data': True}\n"
            "from app.services.narrative_writers import NarrativeRequest\n"
            "print(svc.generate(NarrativeRequest(f, wn._prompt_lines(f), wn.SYSTEM_PROMPT)).source)\n"
        )
        backend = Path(__file__).resolve().parent.parent
        out = subprocess.run([sys.executable, "-c", code], cwd=backend, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "plantilla"

    def test_no_module_imports_it(self):
        app = Path(__file__).resolve().parent.parent / "app"
        offenders = [str(p) for p in app.rglob("*.py") if "import anthropic" in p.read_text(encoding="utf-8")]
        assert offenders == []


@pytest.mark.db
class TestGetOrBuild:
    def test_uses_the_template_without_a_provider(self, client, auth_header, test_store, test_db_session, monkeypatch):
        monkeypatch.setattr(settings, "narrative_provider", "")
        _seed(client, auth_header, test_store.id)

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert narrative.source == "plantilla"
        assert (narrative.provider, narrative.model) == ("template", None)
        assert narrative.mood == "festeja"
        assert "vendiste US$1.500" in narrative.text
        assert narrative.facts[0] == {"label": "Ventas", "value": "US$1.500", "change_pct": 50.0}
        assert test_db_session.get(WeeklyNarrative, (test_store.id, date(2026, 3, 13))) is not None

    def test_keeps_a_models_text_and_records_who_wrote_it(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        _seed(client, auth_header, test_store.id)
        writer = _use_writer(monkeypatch, FakeWriter(written("Vendiste US$1.500 y te quedaron US$1.200 de ganancia real.")))

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert narrative.source == "ia"
        assert narrative.text.startswith("Vendiste US$1.500")
        saved = test_db_session.get(WeeklyNarrative, (test_store.id, date(2026, 3, 13)))
        assert (saved.provider, saved.model) == ("fake", "fake-1")
        assert writer.calls[0].system_prompt == SYSTEM_PROMPT

    def test_falls_back_and_retries_later_when_the_model_invents_a_figure(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        _seed(client, auth_header, test_store.id)
        _use_writer(monkeypatch, FakeWriter(written("Ganaste US$9.999 esta semana.")))

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert narrative.source == "plantilla"
        assert "9.999" not in narrative.text
        # Not cached: the next visit gets another chance at the model's version.
        assert test_db_session.get(WeeklyNarrative, (test_store.id, date(2026, 3, 13))) is None

    def test_falls_back_and_retries_later_when_the_model_fails(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        _seed(client, auth_header, test_store.id)
        _use_writer(monkeypatch, FakeWriter(failed("timeout")))

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert (narrative.source, narrative.provider) == ("plantilla", "template")
        assert test_db_session.get(WeeklyNarrative, (test_store.id, date(2026, 3, 13))) is None

    def test_a_writer_that_crashes_does_not_break_the_narrative(
        self, client, auth_header, test_store, test_db_session, monkeypatch
    ):
        _seed(client, auth_header, test_store.id)
        _use_writer(monkeypatch, FakeWriter(error=RuntimeError("boom")))

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert narrative.source == "plantilla"

    def test_reuses_the_days_summary(self, client, auth_header, test_store, test_db_session, monkeypatch):
        _seed(client, auth_header, test_store.id)
        writer = _use_writer(monkeypatch, FakeWriter(written("Vendiste US$1.500 en los últimos 7 días.")))

        weekly_narrative.get_or_build(test_db_session, test_store, NOW)
        weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert len(writer.calls) == 1

    def test_does_not_call_the_model_without_data(self, test_store, test_db_session, monkeypatch):
        writer = _use_writer(monkeypatch, FakeWriter(written("irrelevante")))

        narrative = weekly_narrative.get_or_build(test_db_session, test_store, NOW)

        assert writer.calls == []
        assert narrative.mood == "neutral"


@pytest.mark.db
class TestNarrativeRoute:
    def test_returns_text_mood_and_facts(self, client, auth_header, test_store, monkeypatch):
        monkeypatch.setattr(settings, "narrative_provider", "")
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
