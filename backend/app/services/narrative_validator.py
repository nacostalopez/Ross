"""Decides whether a model's text about a store's week can be shown to its owner.

The numbers come from collect_facts(); the model only writes sentences around them. This module
checks every figure in the text against those facts, not just "is this number somewhere in the
data": which magnitude it is said to be (sales, profit, ad spend, a percentage), whether the sign
and the direction ("subió", "perdiste") agree with the data, and the formats a model may use
("300000", "300 mil", "1,5 millones", "2.50"). check_figures() returns None when the text is fine,
or a short code for the first problem found.
"""

import re
from typing import Optional

# One figure: an optional currency symbol, the number, then "%" or a unit ("mil", "millones") or a word ("pesos").
_FIGURE = re.compile(
    r"(?P<cur>US\$|R\$|\$|€)?\s?(?P<num>\d+(?:[.,]\d+)*)(?P<pct>\s?%)?"
    r"(?:\s+(?P<unit>millones|millón|millon|mil)\b)?"
    r"(?:\s+(?P<word>pesos|dólares|dolares|euros|reales)\b)?",
    re.IGNORECASE,
)
_THOUSANDS = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")

_UNITS = {"mil": 1_000, "millones": 1_000_000, "millón": 1_000_000, "millon": 1_000_000}
_WINDOW_DAYS = {7.0, 14.0}  # "los últimos 7 días"

# What a figure is being said about: the nearest of these words before it (or "de/en <word>" right after).
_SALES = re.compile(r"vend|ventas|factur|ingresos")
_PROFIT = re.compile(r"ganancia|ganaste|ganamos|quedaron|quedó|quedo|perdiste|perdimos|pérdida|perdida|utilidad")
_ADS = re.compile(r"publicidad|gastaste|invertiste|anuncios")
_COST = re.compile(r"costo|comisi|descuento|envío|envio|mercadería|mercaderia")
_GROUPS = {"sales": _SALES, "profit": _PROFIT, "ads": _ADS, "cost": _COST}

_UP = re.compile(r"subi|aument|crec|mejor|\bmás\b|\bmas\b")
_DOWN = re.compile(r"baj|cay|disminu|redu|empeor|\bmenos\b")
_LOSS = re.compile(r"perdiste|perdimos|pérdida|perdida|en rojo|números rojos|numeros rojos")
_GAIN = re.compile(r"ganancia|ganaste|ganamos|quedaron|quedó")
_ROAS = re.compile(r"roas")
_AFTER = re.compile(r"^\s*(?:de|en|por)\s+(?:\w+\s+){0,2}?(\w+)")


def _parse_number(raw: str) -> tuple[float, int]:
    """Argentine reading: "." groups thousands, "," is the decimal mark. A lone "2.50" is read as a
    decimal (a model copying the ratio in English style), "300.000" as thousands. Returns the value
    and how many decimals were written."""
    if "," in raw:
        whole, _, frac = raw.replace(".", "").partition(",")
        return float(f"{whole}.{frac}"), len(frac)
    if _THOUSANDS.match(raw):
        return float(raw.replace(".", "")), 0
    if "." in raw:
        return float(raw), len(raw.split(".")[-1])
    return float(raw), 0


def _nearest_group(before: str, after: str) -> Optional[str]:
    # "$200.000 en publicidad": the word right after "de/en/por" says what the figure is.
    follow = _AFTER.match(after)
    if follow:
        for name, pattern in _GROUPS.items():
            if pattern.search(follow.group(1)):
                return name
    best, best_end = None, -1
    for name, pattern in _GROUPS.items():
        for match in pattern.finditer(before):
            if match.end() > best_end:
                best, best_end = name, match.end()
    return best


def _direction(before: str, after: str) -> Optional[str]:
    """The up/down word closest to a percentage: "subió un 20%" or "un 20% menos"."""
    found = []
    for name, pattern in (("up", _UP), ("down", _DOWN)):
        for match in pattern.finditer(before[-40:]):
            found.append((len(before[-40:]) - match.end(), name))
        for match in pattern.finditer(after[:15]):
            found.append((match.start(), name))
    return min(found)[1] if found else None


def _facts_figures(facts: dict) -> dict:
    driver = facts.get("driver")
    return {
        "sales": {abs(facts["revenue"]), abs(facts["previous_revenue"])},
        "profit": {abs(facts["real_profit"]), abs(facts["previous_real_profit"])},
        "ads": {abs(facts["ad_spend"])},
        "cost": {driver["increase"]} if driver else set(),
        "driver": {driver["increase"]} if driver else set(),
    }


def _money_matches(value: float, expected: float, unit: int, decimals: int) -> bool:
    if unit == 1:
        return abs(value - round(expected)) < 0.5
    # "300 mil" and "1,5 millones" are written rounded to what they show, at most 1% off.
    return abs(value - expected) <= min(0.5 * 10 ** -decimals * unit, 0.01 * abs(expected)) + 1e-6


def _check_money(value, unit, decimals, group, sentence_lower, facts) -> Optional[str]:
    figures = _facts_figures(facts)
    everything = set().union(figures["sales"], figures["profit"], figures["ads"], figures["driver"])
    if not any(_money_matches(value, e, unit, decimals) for e in everything):
        return "unknown_figure"
    if group:
        allowed = figures[group] | figures["driver"]
        if not any(_money_matches(value, e, unit, decimals) for e in allowed):
            return "wrong_metric"

    # A loss said as a gain, or a gain said as a loss.
    signed = [
        (abs(facts["real_profit"]), facts["real_profit"] >= 0),
        (abs(facts["previous_real_profit"]), facts["previous_real_profit"] >= 0),
    ]
    matching = [positive for amount, positive in signed if _money_matches(value, amount, unit, decimals)]
    if matching and group in (None, "profit"):
        says_loss = bool(_LOSS.search(sentence_lower))
        if says_loss and not any(not positive for positive in matching):
            return "wrong_sign"
        if not says_loss and _GAIN.search(sentence_lower) and not any(matching):
            return "wrong_sign"
    return None


def _check_percentage(value: float, group, before: str, after: str, facts: dict) -> Optional[str]:
    changes = {"sales": facts.get("revenue_change"), "profit": facts.get("profit_change")}
    candidates = {name: c for name, c in changes.items() if c is not None and round(abs(c)) == round(value)}
    if not candidates:
        return "unknown_figure"
    if group in ("cost", "ads"):
        return "wrong_metric"  # the facts hold no percentage for a cost line
    if group in changes:
        candidates = {name: c for name, c in candidates.items() if name == group}
        if not candidates:
            return "wrong_metric"

    direction = _direction(before, after)
    if direction and round(value) != 0:
        if not any((c >= 0) == (direction == "up") for c in candidates.values()):
            return "wrong_direction"
    return None


def check_figures(text: str, facts: dict) -> Optional[str]:
    """None if every figure in the text agrees with the facts, else a short code:
    unknown_figure (not in the data), wrong_metric (a real figure said about something else),
    wrong_sign (a loss told as profit or the reverse), wrong_direction (said to rise when it fell)."""
    figures = _facts_figures(facts)
    ratio = facts.get("true_roas")

    for sentence in _SENTENCES.split(text):
        lower = sentence.lower()
        for match in _FIGURE.finditer(sentence):
            before, after = lower[: match.start()], lower[match.end() :]
            value, decimals = _parse_number(match.group("num"))
            unit = _UNITS.get((match.group("unit") or "").lower(), 1)
            value *= unit
            group = _nearest_group(before[-80:], after)

            if match.group("pct"):
                problem = _check_percentage(value, group, before, after, facts)
            elif match.group("cur") or match.group("word") or match.group("unit"):
                problem = _check_money(value, unit, decimals, group, lower, facts)
            elif _ROAS.search(before[-25:]):
                known = ratio is not None and abs(value - round(abs(ratio), 2)) < 1e-9
                problem = None if known else "unknown_figure"
            else:
                problem = _check_bare(value, decimals, facts, figures, ratio)
            if problem:
                return problem
    return None


def _check_bare(value: float, decimals: int, facts: dict, figures: dict, ratio) -> Optional[str]:
    """A number with no sign of what it is ("300000", "7"): fine if it is the window or any known figure."""
    if value in _WINDOW_DAYS:
        return None
    if any(_money_matches(value, e, 1, decimals) for group in figures.values() for e in group):
        return None
    if any(c is not None and round(abs(c)) == round(value) for c in (facts.get("revenue_change"), facts.get("profit_change"))):
        return None
    if ratio is not None and abs(value - round(abs(ratio), 2)) < 1e-9:
        return None
    return "unknown_figure"


def uses_only_known_figures(text: str, facts: dict) -> bool:
    return check_figures(text, facts) is None
