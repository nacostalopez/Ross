#!/usr/bin/env python
"""Baseline for "Ross explica tu semana", the starting point for swapping the AI provider.

Run from backend/ (it imports the app, but needs no database):
    cd backend && python ../scripts/narrative_baseline.py            # offline: template + validator
    cd backend && python ../scripts/narrative_baseline.py --live     # also calls Claude (needs ANTHROPIC_API_KEY)
    cd backend && python ../scripts/narrative_baseline.py --live --repeat 3 --out baseline.md

Offline, it runs the fixed template and the figure check over a set of synthetic stores (none of it
is real data) and probes how the check treats formats the plan has to get right. With --live it also
asks Claude for each case and records latency, how often the text survives the check, and why not.
Whatever a new provider does later is measured against this output.
"""
import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import settings  # noqa: E402
from app.services import weekly_narrative as wn  # noqa: E402


def facts(**o):
    base = {
        "currency": "ARS", "revenue": 1_500_000.0, "real_profit": 300_000.0, "ad_spend": 200_000.0, "true_roas": 2.5,
        "previous_revenue": 1_200_000.0, "previous_real_profit": 250_000.0, "revenue_change": 25.0,
        "profit_change": 20.0, "driver": None, "has_data": True,
    }
    base.update(o)
    return base


# Synthetic stores, one per situation the text has to handle.
CASES = {
    "crece": facts(),
    "crece-con-costo-que-sube": facts(driver={"label": "las comisiones", "increase": 45_000.0}),
    "cae-fuerte": facts(real_profit=120_000.0, profit_change=-52.0, revenue_change=-18.0, true_roas=0.6),
    "pierde-plata": facts(real_profit=-80_000.0, profit_change=None, true_roas=-0.4),
    "estable": facts(profit_change=0.4, revenue_change=1.0),
    "sin-semana-anterior": facts(previous_revenue=0.0, previous_real_profit=0.0, revenue_change=None, profit_change=None),
    "sin-publicidad": facts(ad_spend=0.0, true_roas=None),
    "dolares": facts(currency="USD", revenue=18_400.0, real_profit=3_150.0, ad_spend=2_300.0, true_roas=1.9),
    "cifras-grandes": facts(revenue=48_250_000.0, real_profit=9_800_000.0, ad_spend=6_100_000.0, previous_revenue=41_000_000.0),
    "cifras-chicas": facts(revenue=4_300.0, real_profit=900.0, ad_spend=700.0, true_roas=1.3),
    "sin-datos": facts(has_data=False, revenue=0.0, real_profit=0.0, ad_spend=0.0, true_roas=None,
                       previous_revenue=0.0, previous_real_profit=0.0, revenue_change=None, profit_change=None),
}

# How the figure check treats texts a model could plausibly write. "expected" is what the plan needs.
PROBE_FACTS = facts(driver={"label": "las comisiones", "increase": 45_000.0})
PROBES = [
    ("Copia exacta de una cifra", "Te quedaron $300.000 de ganancia real.", True),
    ("Cifra inventada", "Te quedaron $310.000 de ganancia real.", False),
    ("Porcentaje que está en los datos", "La ganancia real subió un 20%.", True),
    ("Cifra real atribuida a otra magnitud (25 es el cambio de ventas)", "La ganancia real subió un 25%.", False),
    ("Porcentaje inventado", "La ganancia real subió un 33%.", False),
    ("Monto sin separador de miles", "Ganaste 300000 pesos.", True),
    ("Monto abreviado", "Ganaste 300 mil pesos.", True),
    ("Monto en millones", "Vendiste 1,5 millones.", True),
    ("Cifra negativa mostrada como positiva", "Perdiste $300.000 esta semana.", False),
    ("Sentido contrario al dato", "La ganancia real bajó un 20%.", False),
    ("Cifra de otra magnitud (20 contra 20.000)", "Gastaste $20.000 en publicidad.", False),
    ("True ROAS con coma", "Tu True ROAS fue de 2,50.", True),
    ("True ROAS con punto", "Tu True ROAS fue de 2.50.", True),
]


def words(text):
    return len(text.split())


def offline():
    rows = []
    for name, f in CASES.items():
        t0 = time.perf_counter()
        text = wn.template_text(f)
        mood = wn.mood_for(f)
        shown = wn.display_facts(f)
        lines = wn._prompt_lines(f)
        dt = (time.perf_counter() - t0) * 1000
        rows.append((name, mood, words(text), wn._uses_only_known_figures(text, lines), len(shown), dt, text))
    return rows


def probes():
    lines = wn._prompt_lines(PROBE_FACTS)
    out = []
    for label, text, correct in PROBES:
        accepted = wn._uses_only_known_figures(text, lines)
        out.append((label, text, accepted, correct, "ok" if accepted == correct else "REVISAR"))
    return out


class Reasons(logging.Handler):
    """Collects why ai_text() gave up; it only returns None, the reason goes to the log."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def emit(self, record):
        self.seen.append(record.getMessage())


def live(repeat):
    if not settings.anthropic_api_key:
        return None
    handler = Reasons()
    logging.getLogger("ross.narrative").addHandler(handler)
    results = []
    for name, f in CASES.items():
        if not f["has_data"]:
            continue
        for _ in range(repeat):
            handler.seen.clear()
            t0 = time.perf_counter()
            text = wn.ai_text(f)
            dt = time.perf_counter() - t0
            results.append((name, dt, text, list(handler.seen)))
    return results


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(round((p / 100) * (len(values) - 1))))]


def report(repeat, do_live):
    out = ["# Línea base de la narrativa semanal", ""]
    out.append(f"Modelo configurado: `{settings.narrative_model}`. Clave de Anthropic: {'cargada' if settings.anthropic_api_key else 'no cargada'}.")
    out += ["", "## 1. Plantilla (sin IA) sobre casos sintéticos", "",
            "| Caso | Ánimo | Palabras | Pasa su propio chequeo | Datos mostrados | ms |", "|---|---|---|---|---|---|"]
    rows = offline()
    for name, mood, w, ok, n, dt, _ in rows:
        out.append(f"| {name} | {mood} | {w} | {'sí' if ok else 'NO'} | {n} | {dt:.2f} |")
    bad = [r for r in rows if not r[3]]
    out += ["", f"La plantilla pasa su propio chequeo en {len(rows) - len(bad)} de {len(rows)} casos."]
    if bad:
        out.append("Casos que la plantilla no pasa: " + ", ".join(r[0] for r in bad) + ".")
    out += ["", "Textos de la plantilla:", ""]
    out += [f"- **{r[0]}**: {r[6]}" for r in rows]

    out += ["", "## 2. Cómo trata el chequeo de cifras distintos formatos", "",
            "| Situación | Texto | Lo acepta | Debería aceptarlo | Resultado |", "|---|---|---|---|---|"]
    pr = probes()
    for label, text, accepted, correct, verdict in pr:
        out.append(f"| {label} | {text} | {'sí' if accepted else 'no'} | {'sí' if correct else 'no'} | {verdict} |")
    n_bad = sum(1 for p in pr if p[4] != "ok")
    out += ["", f"El chequeo coincide con lo esperado en {len(pr) - n_bad} de {len(pr)} situaciones. Las marcadas REVISAR son lo que hay que resolver en la fase de validación."]

    out += ["", "## 3. Latencia y calidad con Claude", ""]
    if not do_live:
        out.append("No se corrió (falta `--live`).")
    else:
        res = live(repeat)
        if res is None:
            out.append("No se corrió: no hay `ANTHROPIC_API_KEY` cargada en este entorno. Ejecutarlo con la clave para completar esta sección.")
        else:
            lat = [r[1] for r in res]
            kept = [r for r in res if r[2]]
            out.append(f"Llamadas: {len(res)}. Texto aceptado: {len(kept)} ({len(kept) / len(res) * 100:.0f}%).")
            out.append(f"Latencia: mediana {statistics.median(lat):.1f} s, p95 {pct(lat, 95):.1f} s, máxima {max(lat):.1f} s.")
            wc = [words(r[2]) for r in kept]
            if wc:
                out.append(f"Palabras por texto aceptado: mediana {statistics.median(wc):.0f}, máximo {max(wc)} (el prompt pide hasta 60).")
            reasons = {}
            for r in res:
                for m in r[3]:
                    reasons[m] = reasons.get(m, 0) + 1
            out.append("Motivos de descarte: " + (", ".join(f"{k} ×{v}" for k, v in reasons.items()) or "ninguno") + ".")
            out += ["", "| Caso | s | Aceptado | Texto |", "|---|---|---|---|"]
            for name, dt, text, _ in res:
                out.append(f"| {name} | {dt:.1f} | {'sí' if text else 'no'} | {(text or '—').replace('|', '/')} |")
    return "\n".join(out) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="also call Claude for each case")
    ap.add_argument("--repeat", type=int, default=1, help="calls per case with --live")
    ap.add_argument("--out", help="write the report to this file as well")
    args = ap.parse_args()
    text = report(args.repeat, args.live)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
