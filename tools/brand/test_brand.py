"""Checks for the brand icons. Run from the repo root: pytest tools/brand"""
import os
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_icons  # noqa: E402


def test_exported_icons_match_the_grid():
    """The committed icons are what the generator makes now: nobody edited one by hand or
    changed the grid without exporting."""
    stale = []
    for path, data in export_icons.outputs().items():
        if not os.path.exists(path) or open(path, "rb").read() != data:
            stale.append(os.path.relpath(path, export_icons.ROOT))
    assert not stale, f"Run python tools/brand/export_icons.py: {stale}"


def test_every_grid_role_has_a_color():
    used = {role for row in export_icons.GRID for role in row}
    assert used <= set(export_icons.COLORS)


def test_pngs_have_the_sizes_their_names_and_the_manifest_promise():
    expected = {
        "favicon-16.png": 16,
        "favicon-32.png": 32,
        "apple-touch-icon.png": 180,
        "icon-192.png": 192,
        "icon-512.png": 512,
        "icon-maskable-512.png": 512,
    }
    files = export_icons.build()
    for name, size in expected.items():
        width, height = struct.unpack(">II", files[name][16:24])
        assert (width, height) == (size, size), name
