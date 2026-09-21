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


def test_connectors_scene_size():
    data = scenes.connectors().to_data()
    assert (data["w"], data["h"]) == (scenes.W, scenes.H)


def _expected_size(name):
    """Scene size by family: connectors, login welcome agent, app mascot, or a module scene."""
    if name == "conectores":
        return scenes.W, scenes.H
    if name.startswith("login-"):
        return scenes.LOGIN_W, scenes.LOGIN_H
    if name.startswith("mascota-"):
        return scenes.MASCOT_W, scenes.MASCOT_H
    return scenes.MW, scenes.MH


def test_every_scene_has_the_size_of_its_family():
    for name, fn in export.SCENES.items():
        data = fn().to_data()
        assert (data["w"], data["h"]) == _expected_size(name), name


def test_scene_names_are_safe_file_names():
    """The runtime builds `escenas/<name>.json` from a data attribute."""
    for name in export.SCENES:
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", name), name


def test_every_scene_is_one_well_formed_animated_strip():
    for name, fn in export.SCENES.items():
        data = fn().to_data()
        assert len(data["parts"]) == 1, name
        part = data["parts"][0]
        assert part["n"] >= 2, name  # a scene that does not move should not be a scene
        assert part["vw"] % part["n"] == 0, name  # a strip of n equal frames
        # the animated box sits inside the scene
        assert part["l"] >= 0 and part["l"] + part["w"] <= 100.001, name
        assert part["t"] >= 0 and part["t"] + part["h"] <= 100.001, name


def test_every_role_and_variant_has_a_chip_and_a_bust():
    portraits = export.build_portraits()
    for role in scenes.ROLES:
        for variant in range(portraits["variants"]):
            key = "%s-%d" % (role, variant)
            assert key in portraits["chips"] and key in portraits["busts"]
    assert set(portraits["heads"]) == set(export.HEAD_MODULES)


def _layers(data):
    return [data["bg"]] + [part["p"] for part in data["parts"]]


def test_layers_are_lists_of_whole_number_rectangles():
    """The runtime turns these numbers into markup: they must be plain non-negative integers, four per
    rectangle, with a real (non-empty) width and height."""
    for data in _all_drawings():
        for layer in _layers(data):
            for code, flat in layer:
                assert isinstance(code, int)
                assert flat and len(flat) % 4 == 0
                assert all(isinstance(n, int) and n >= 0 for n in flat)
                assert all(flat[i] > 0 and flat[i + 1] > 0 for i in range(2, len(flat), 4)), (chr(code), flat[:8])


def _decode(flat):
    """What `rects()` in frontend/agentes/agentes.js does, as a set of covered (x, y) cells."""
    cells, px, py = set(), 0, 0
    for i in range(0, len(flat), 4):
        dy = flat[i + 1]
        x = (0 if dy else px) + flat[i]
        y = py + dy
        w, h = flat[i + 2], flat[i + 3]
        for j in range(h):
            for k in range(w):
                assert (x + k, y + j) not in cells, "two rectangles cover the same cell"
                cells.add((x + k, y + j))
        px, py = x, y
    return cells


def _cells_of(grid, x_offset=0):
    return {(x + x_offset, y): grid.get(x, y) for y in range(grid.h) for x in range(grid.w) if grid.get(x, y) != "."}


def test_rectangles_draw_exactly_the_grid():
    """Encoding then decoding gives back every cell of the drawing with its own role, nothing more."""
    for name, fn in export.SCENES.items():
        scene = fn()
        data = scene.to_data()
        drawn = {}
        for code, flat in data["bg"]:
            for cell in _decode(flat):
                assert cell not in drawn, name
                drawn[cell] = chr(code)
        assert drawn == _cells_of(scene.bg), name
        for part, spec in zip(scene.parts, data["parts"]):
            boxes = [f.bbox() for f in part.frames if f.bbox()]
            x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            w, h = max(b[2] for b in boxes) - x0 + 1, max(b[3] for b in boxes) - y0 + 1
            expected = {}
            for i, frame in enumerate(part.frames):
                expected.update(_cells_of(frame.crop(x0, y0, w, h), i * w))
            drawn = {}
            for code, flat in spec["p"]:
                for cell in _decode(flat):
                    assert cell not in drawn, name
                    drawn[cell] = chr(code)
            assert drawn == expected, name


def test_exported_json_is_valid():
    for rel in ("retratos.json", "escenas/conectores.json"):
        with open(os.path.join(export.DEFAULT_OUT, *rel.split("/")), encoding="utf-8") as f:
            json.load(f)
