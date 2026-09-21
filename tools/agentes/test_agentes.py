"""Checks for the agents generator. Run from the repo root: pytest tools/agentes"""
import json
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export  # noqa: E402
import palette  # noqa: E402
import scenes  # noqa: E402


def _roles_in(data):
    """Every role code (ord of the role character) used by a scene/portrait dict."""
    codes = {code for code, _ in data["bg"]}
    for part in data["parts"]:
        codes |= {code for code, _ in part["p"]}
    return codes


def _all_drawings():
    drawings = [scene().to_data() for scene in export.SCENES.values()]
    portraits = export.build_portraits()
    for group in ("heads", "chips", "busts"):
        drawings += list(portraits[group].values())
    return drawings


def test_every_role_drawn_has_a_light_color():
    known = {ord(role) for role in palette.LIGHT}
    for data in _all_drawings():
        assert _roles_in(data) <= known, sorted(chr(c) for c in _roles_in(data) - known)


def test_dark_only_overrides_known_roles():
    assert set(palette.DARK) <= set(palette.LIGHT)


def test_generated_files_are_committed_and_current():
    """frontend/agentes/ must match what the generator produces (nobody edited it by hand,
    and nobody changed a drawing without re-exporting)."""
    for rel, text in export.build_files().items():
        path = os.path.join(export.DEFAULT_OUT, *rel.split("/"))
        assert os.path.exists(path), "%s is missing: run tools/agentes/export.py" % rel
        with open(path, encoding="utf-8", newline="") as f:
            # a Windows checkout with autocrlf may have turned LF into CRLF
            assert f.read().replace("\r\n", "\n") == text, "%s is stale: run tools/agentes/export.py" % rel


def test_weight_budget():
    assert export.over_budget(export.weights(export.DEFAULT_OUT)) == []


def test_connectors_scene_shape():
    data = scenes.connectors().to_data()
    assert (data["w"], data["h"]) == (scenes.W, scenes.H)
    assert len(data["parts"]) == 1
    part = data["parts"][0]
    assert part["n"] == scenes.CONNECTORS_FRAMES
    assert part["vw"] % part["n"] == 0  # a strip of n equal frames
    # every animated box sits inside the scene
    assert part["l"] >= 0 and part["l"] + part["w"] <= 100.001
    assert part["t"] >= 0 and part["t"] + part["h"] <= 100.001


def test_every_role_and_variant_has_a_chip_and_a_bust():
    portraits = export.build_portraits()
    for role in scenes.ROLES:
        for variant in range(portraits["variants"]):
            key = "%s-%d" % (role, variant)
            assert key in portraits["chips"] and key in portraits["busts"]
    assert set(portraits["heads"]) == set(export.HEAD_MODULES)


def test_paths_are_plain_svg_path_data():
    """The runtime puts `d` into markup: it must only ever contain path commands and numbers."""
    for data in _all_drawings():
        layers = [data["bg"]] + [part["p"] for part in data["parts"]]
        for layer in layers:
            for _, d in layer:
                assert re.fullmatch(r"[MhvzZ0-9 \-]+", d), d[:60]


def test_exported_json_is_valid():
    for rel in ("retratos.json", "escenas/conectores.json"):
        with open(os.path.join(export.DEFAULT_OUT, *rel.split("/")), encoding="utf-8") as f:
            json.load(f)
