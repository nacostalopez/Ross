"""Export the Aramal agents to frontend/agentes/ and enforce the weight budget.

    python tools/agentes/export.py            # write files, print gzip sizes, fail if over budget
    python tools/agentes/export.py OUT_DIR    # write somewhere else

Writes:
    paleta.css                light colors as the base, dark overrides
    escenas/<name>.json       one animated scene per file; the frontend fetches it on demand
    retratos.json             module heads, topbar chips and Perfil busts per role and variant

Everything else in frontend/agentes/ (agentes.js, agentes.css) is hand-written.
"""
import gzip
import json
import os
import sys

import palette
import scenes

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.abspath(os.path.join(HERE, "..", "..", "frontend", "agentes"))

# Weight budget, gzip bytes: what the browser actually downloads.
SCENE_BUDGET = 4 * 1024   # per scene (loaded one at a time, only where it is used)
TOTAL_BUDGET = 25 * 1024  # the whole agentes/ folder, hand-written files included

SCENES = {"conectores": scenes.connectors, "alertas": scenes.alerts, "reportes": scenes.reports, "equipo": scenes.team}
HEAD_MODULES = {"conectores": scenes.TECHNICIAN}


def dump(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def build_portraits():
    chips, busts = {}, {}
    for role in scenes.ROLES:
        for variant in range(len(scenes.VARIANTS)):
            key = "%s-%d" % (role, variant)
            chips[key] = _plain(scenes.chip(role, variant))
            busts[key] = _plain(scenes.bust(role, variant))
    return {
        "variants": len(scenes.VARIANTS),
        "heads": {name: _plain(scenes.head(params)) for name, params in HEAD_MODULES.items()},
        "chips": chips,
        "busts": busts,
    }


def _plain(grid):
    """A single static layer, in the same shape the runtime uses for scenes."""
    from px import Scene
    return Scene(grid, [], grid.w, grid.h).to_data()


def build_files():
    """{relative path: text} for every generated file."""
    files = {"paleta.css": palette.css()}
    for name, fn in SCENES.items():
        files["escenas/%s.json" % name] = dump(fn().to_data()) + "\n"
    files["retratos.json"] = dump(build_portraits()) + "\n"
    return files


def gzip_size(data):
    return len(gzip.compress(data, 9, mtime=0))


def weights(out_dir):
    """[(relative path, raw bytes, gzip bytes)] for every file under out_dir."""
    rows = []
    for root, _, names in os.walk(out_dir):
        for name in sorted(names):
            path = os.path.join(root, name)
            with open(path, "rb") as f:
                data = f.read()
            rows.append((os.path.relpath(path, out_dir).replace(os.sep, "/"), len(data), gzip_size(data)))
    return sorted(rows)


def over_budget(rows):
    """Human-readable problems; empty when everything fits."""
    problems = []
    for rel, _, gz in rows:
        if rel.startswith("escenas/") and gz > SCENE_BUDGET:
            problems.append("%s is %d B gzip (budget %d B per scene)" % (rel, gz, SCENE_BUDGET))
    total = sum(gz for _, _, gz in rows)
    if total > TOTAL_BUDGET:
        problems.append("agentes/ is %d B gzip in total (budget %d B)" % (total, TOTAL_BUDGET))
    return problems


def main(argv):
    out_dir = os.path.abspath(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    for rel, text in build_files().items():
        path = os.path.join(out_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    rows = weights(out_dir)
    for rel, raw, gz in rows:
        print("%-24s %7d B raw  %6d B gzip" % (rel, raw, gz))
    total = sum(gz for _, _, gz in rows)
    print("%-24s %7s      %6d B gzip  (budget %d B)" % ("TOTAL", "", total, TOTAL_BUDGET))

    problems = over_budget(rows)
    for problem in problems:
        print("OVER BUDGET:", problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
