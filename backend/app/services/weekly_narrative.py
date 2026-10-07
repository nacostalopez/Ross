"""Ross explica tu semana: the last 7 days of a store, told in plain language.

The numbers never come from the model. collect_facts() computes them here,
from the same P&L query the dashboard uses, and formats every figure the text
may mention. Claude only writes the sentences around those figures, and
_uses_only_known_figures() throws the text away if it contains a number that
isn't one of them, so a writing mistake can never show the owner a wrong
amount. With no ANTHROPIC_API_KEY, or when the call fails or the check
rejects the text, the summary comes from template_text() instead.

The result is cached per store per day (weekly_narratives), so opening the
dashboard doesn't call the API every time.
"""

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Store, WeeklyNarrative
from app.routes.metrics import PNL_SQL

logger = logging.getLogger("ross.narrative")

# Cost lines of the P&L, in the words the summary uses for them.
COST_LINES = {
    "discounts": "los descuentos",
    "shipping_fee": "el costo de envío",
    "payment_gateway_fee": "las comisiones",
    "cogs_total": "el costo de mercadería",
    "total_ad_spend": "el gasto en publicidad",
}

CURRENCY_PREFIX = {"ARS": "$", "USD": "US$", "EUR": "€", "BRL": "R$"}

SYSTEM_PROMPT = """Sos Ross, la mascota de ROSS, una herramienta de rentabilidad para tiendas online de Argentina.
Le explicás al dueño de una tienda cómo le fue en los últimos 7 días, comparado con los 7 anteriores.

Escribí dos o tres oraciones cortas, en español rioplatense con voseo, de no más de 60 palabras en total.
Empezá por lo más importante: cuánta ganancia real le quedó y si subió o bajó. Si hay un costo que cambió mucho, decí cuál.
Cerrá con una sugerencia concreta solo si los datos la justifican.

Reglas:
- Usá únicamente las cifras de los datos, copiadas exactamente como están escritas. No calcules ni redondees cifras nuevas.
- No uses adjetivos ni artículos que le den género a Ross. Ross habla en primera persona sin marcar género.
- Sin markdown, sin emojis, sin saludos ni despedidas. Devolvé solo el texto."""

_NUMBER = re.compile(r"\d[\d.,]*")


def format_money(value: float, currency: str) -> str:
    prefix = CURRENCY_PREFIX.get(currency, f"{currency} ")
    amount = f"{abs(value):,.0f}".replace(",", ".")
    return f"{'-' if value < 0 else ''}{prefix}{amount}"


def format_pct(value: float) -> str:
    return f"{abs(value):.0f}%"


def format_ratio(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _pct_change(current: float, previous: float) -> Optional[float]:
    # A change against zero or a loss isn't a meaningful percentage.
    if previous <= 0:
        return None
    return (current - previous) / previous * 100


def _pnl(db: Session, store: Store, start: datetime, end: datetime) -> dict:
    # PNL_SQL filters with BETWEEN, so end the window a microsecond early: an order at exactly
    # midnight belongs to the next window only.
    row = (
        db.execute(PNL_SQL, {"store_id": str(store.id), "start": start, "end": end - timedelta(microseconds=1)})
        .mappings()
        .one()
    )
    return {key: float(value) for key, value in row.items()}


def collect_facts(db: Session, store: Store, period_end: date) -> dict:
    """The 7 full days before period_end against the 7 before those."""
    end = datetime.combine(period_end, time.min, tzinfo=timezone.utc)
    current = _pnl(db, store, end - timedelta(days=7), end)
    previous = _pnl(db, store, end - timedelta(days=14), end - timedelta(days=7))

    # The cost line that grew the most, if any grew at all.
    driver = None
    for key, label in COST_LINES.items():
        increase = current[key] - previous[key]
        if increase > 0 and (driver is None or increase > driver["increase"]):
            driver = {"label": label, "increase": increase}

    ad_spend = current["total_ad_spend"]
    return {
        "currency": store.currency or "USD",
        "revenue": current["revenue"],
        "real_profit": current["real_profit_after_ads"],
        "ad_spend": ad_spend,
        "true_roas": current["net_profit"] / ad_spend if ad_spend > 0 else None,
        "previous_revenue": previous["revenue"],
        "previous_real_profit": previous["real_profit_after_ads"],
        "revenue_change": _pct_change(current["revenue"], previous["revenue"]),
        "profit_change": _pct_change(current["real_profit_after_ads"], previous["real_profit_after_ads"]),
        "driver": driver,
        "has_data": any(current[key] for key in ("revenue", "total_ad_spend"))
        or any(previous[key] for key in ("revenue", "total_ad_spend")),
    }


def mood_for(facts: dict) -> str:
    """Which mascot scene goes with the week: festeja, preocupada or neutral."""
    if not facts["has_data"]:
        return "neutral"
    profit, change = facts["real_profit"], facts["profit_change"]
    if profit < 0 or (change is not None and change <= -10):
        return "preocupada"
    if profit > 0 and ((change is not None and change >= 5) or facts["previous_real_profit"] <= 0):
        return "festeja"
    return "neutral"


def display_facts(facts: dict) -> list[dict]:
    """The figures shown beside the text, so every claim in it can be checked."""
    cur = facts["currency"]
    rows = [
        {"label": "Ventas", "value": format_money(facts["revenue"], cur), "change_pct": facts["revenue_change"]},
        {
            "label": "Ganancia real",
            "value": format_money(facts["real_profit"], cur),
            "change_pct": facts["profit_change"],
        },
        {"label": "Publicidad", "value": format_money(facts["ad_spend"], cur), "change_pct": None},
    ]
    if facts["true_roas"] is not None:
        rows.append({"label": "True ROAS", "value": format_ratio(facts["true_roas"]), "change_pct": None})
    return rows


def _change_phrase(change: Optional[float]) -> str:
    if change is None:
        return ""
    if round(change) == 0:
        return ", lo mismo que la semana anterior"
    return f", un {format_pct(change)} {'más' if change > 0 else 'menos'} que la semana anterior"


def template_text(facts: dict) -> str:
    if not facts["has_data"]:
        return (
            "Todavía no tengo ventas ni gasto en publicidad de los últimos 14 días para comparar. "
            "Cuando conectes tu tienda y tus cuentas de publicidad, acá te cuento cómo te fue cada semana."
        )
    cur = facts["currency"]
    profit = facts["real_profit"]
    if profit >= 0:
        first = (
            f"En los últimos 7 días vendiste {format_money(facts['revenue'], cur)} y te quedaron "
            f"{format_money(profit, cur)} de ganancia real{_change_phrase(facts['profit_change'])}."
        )
    else:
        first = (
            f"En los últimos 7 días vendiste {format_money(facts['revenue'], cur)}, pero después de costos "
            f"y publicidad perdiste {format_money(abs(profit), cur)}."
        )
    parts = [first]
    if facts["driver"]:
        parts.append(
            f"Lo que más subió fue {facts['driver']['label']}: "
            f"{format_money(facts['driver']['increase'], cur)} más que la semana anterior."
        )
    if facts["true_roas"] is not None:
        parts.append(f"Tu True ROAS fue de {format_ratio(facts['true_roas'])}.")
    return " ".join(parts)


def _prompt_lines(facts: dict) -> list[str]:
    cur = facts["currency"]
    lines = [
        f"Ventas de los últimos 7 días: {format_money(facts['revenue'], cur)}",
        f"Ventas de los 7 días anteriores: {format_money(facts['previous_revenue'], cur)}",
        f"Ganancia real de los últimos 7 días (después de costos y publicidad): {format_money(facts['real_profit'], cur)}",
        f"Ganancia real de los 7 días anteriores: {format_money(facts['previous_real_profit'], cur)}",
        f"Gasto en publicidad de los últimos 7 días: {format_money(facts['ad_spend'], cur)}",
    ]
    if facts["revenue_change"] is not None:
        direction = "subieron" if facts["revenue_change"] >= 0 else "bajaron"
        lines.append(f"Las ventas {direction} un {format_pct(facts['revenue_change'])}")
    if facts["profit_change"] is not None:
        direction = "subió" if facts["profit_change"] >= 0 else "bajó"
        lines.append(f"La ganancia real {direction} un {format_pct(facts['profit_change'])}")
    if facts["driver"]:
        lines.append(
            f"El costo que más subió: {facts['driver']['label']}, "
            f"{format_money(facts['driver']['increase'], cur)} más que la semana anterior"
        )
    if facts["true_roas"] is not None:
        lines.append(f"True ROAS (ganancia por cada peso de publicidad): {format_ratio(facts['true_roas'])}")
    return lines


def _uses_only_known_figures(text: str, prompt_lines: list[str]) -> bool:
    allowed = {match.rstrip(".,") for line in prompt_lines for match in _NUMBER.findall(line)}
    allowed |= {"7", "14"}  # the windows themselves ("los últimos 7 días")
    return all(match.rstrip(".,") in allowed for match in _NUMBER.findall(text))


def ai_text(facts: dict) -> Optional[str]:
    """Claude's version of the summary, or None to fall back to the template."""
    if not settings.anthropic_api_key:
        return None
    lines = _prompt_lines(facts)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=30.0, max_retries=1)
    try:
        response = client.beta.messages.create(
            model=settings.narrative_model,
            max_tokens=4000,
            output_config={"effort": "low"},
            # A refused request is retried on the fallback model Anthropic recommends.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "Datos de la tienda:\n" + "\n".join(lines)}],
        )
    except anthropic.RateLimitError:
        logger.warning("narrative_rate_limited")
        return None
    except anthropic.APIStatusError as exc:
        logger.warning("narrative_api_error", extra={"status_code": exc.status_code})
        return None
    except anthropic.APIConnectionError:
        logger.warning("narrative_connection_error")
        return None

    if response.stop_reason != "end_turn":
        logger.warning("narrative_not_finished", extra={"stop_reason": response.stop_reason})
        return None
    text = " ".join(block.text for block in response.content if block.type == "text").strip()
    if not text or not _uses_only_known_figures(text, lines):
        logger.warning("narrative_rejected_unknown_figures")
        return None
    return text


def get_or_build(db: Session, store: Store, now: Optional[datetime] = None) -> WeeklyNarrative:
    """Today's summary for the store, written once and reused until tomorrow."""
    period_end = (now or datetime.now(timezone.utc)).date()
    cached = db.get(WeeklyNarrative, (store.id, period_end))
    if cached:
        return cached

    facts = collect_facts(db, store, period_end)
    written = ai_text(facts) if facts["has_data"] else None
    narrative = WeeklyNarrative(
        store_id=store.id,
        period_end=period_end,
        text=written or template_text(facts),
        mood=mood_for(facts),
        source="ia" if written else "plantilla",
        facts=display_facts(facts),
    )
    # If Claude was supposed to write it and couldn't, don't keep the template version for the
    # whole day: the next visit tries again.
    if written or not settings.anthropic_api_key or not facts["has_data"]:
        db.merge(narrative)
        db.commit()
    return narrative
