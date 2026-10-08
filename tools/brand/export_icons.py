"""Ross's brand mark: the mascot's face in pixel art, exported as every icon the site uses.

The drawing is the 16x16 grid below, one character per pixel. Run from the repo root:

    python tools/brand/export_icons.py

and it rewrites frontend/icons/ (favicons, the iPhone and Android icons, and the SVG mark the
pages show next to the name). Python 3 standard library only, like tools/agentes: the PNGs
are written by hand, since every icon is the grid scaled by a whole number, so no resampling
library is needed. Edit the grid or the colors here, never the exported files.
"""

import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "frontend", "icons")

# The mark on its blue tile. "." is the tile itself.
GRID = [
    "................",
    "................",
    "....oooooooo....",
    "...ohhhhhhhho...",
    "...ohhhhhhhho...",
    "...ohhhsshhho...",
    "...o" + "s" * 8 + "o...",
    "...ossessesso...",
    "...ossessesso...",
    "...osesssseso...",
    "...osseeeesso...",
    "...o" + "s" * 8 + "o...",
    "....oooooooo....",
    "...bbbbwwbbbb...",
    "...bbbbbbbbbb...",
    "...bbbbbbbbbb...",
]
assert all(len(row) == 16 for row in GRID), "every row of the grid is 16 pixels wide"

COLORS = {
    ".": "#2263A2",  # tile: Azul acción
    "o": "#0C1730",  # outline: Tinta
    "e": "#0C1730",  # eyes and smile: Tinta
    "h": "#5B3A29",  # hair
    "s": "#F1CBA5",  # skin
    "b": "#1A2B4A",  # shoulders: Navy
    "w": "#FFFFFF",  # collar
}
# Corner radius of the tile, in grid pixels (the same as the SVG's rx).
RADIUS = 3.5
SIZE = 16


def _rgb(hex_color):
    return tuple(int(hex_color[i : i + 2], 16) for i in (1, 3, 5))


def _inside_rounded_square(x, y, side, radius):
    """Whether point (x, y) lies on a side x side square with rounded corners."""
    cx = min(max(x, radius), side - radius)
    cy = min(max(y, radius), side - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def render(size, cell, offset, rounded, extend_body=False):
    """RGBA rows for an icon `size` px wide: the grid at `cell` px per pixel, shifted by `offset`.

    rounded: the tile has rounded corners and transparent outside (favicons, "any" icons).
    Otherwise the tile fills the whole square (the platform masks it: iPhone, maskable).
    extend_body: carry the shoulders down to the bottom edge, so a full-bleed icon doesn't show
    them cut off in mid-air.
    """
    radius = RADIUS * cell
    tile = _rgb(COLORS["."])
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            gx, gy = (px - offset) // cell, (py - offset) // cell
            role = "."
            if 0 <= gx < SIZE and 0 <= gy < SIZE:
                role = GRID[gy][gx]
            elif extend_body and gy >= SIZE and 0 <= gx < SIZE and GRID[SIZE - 1][gx] == "b":
                role = "b"
            color = _rgb(COLORS[role]) if role != "." else tile
            alpha = 255
            if rounded:
                # 4x4 supersampling of the tile's edge, so the corners are smooth.
                hits = sum(
                    _inside_rounded_square(px + (i + 0.5) / 4, py + (j + 0.5) / 4, size, radius)
                    for i in range(4)
                    for j in range(4)
                )
                alpha = round(255 * hits / 16)
            row += bytes((*color, alpha))
        rows.append(bytes(row))
    return rows


def png(rows):
    width, height = len(rows[0]) // 4, len(rows)
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def ico(pngs):
    """A .ico holding PNG images (every browser that reads .ico reads PNG entries)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, data = b"", b""
    for size, blob in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    return header + entries + data


def svg():
    """The mark as an SVG: one rect per horizontal run of each color, on the rounded tile."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" shape-rendering="crispEdges">',
        f'<rect width="{SIZE}" height="{SIZE}" rx="{RADIUS}" fill="{COLORS["."]}" shape-rendering="geometricPrecision"/>',
    ]
    for y, row in enumerate(GRID):
        x = 0
        while x < SIZE:
            role = row[x]
            run = 1
            while x + run < SIZE and row[x + run] == role:
                run += 1
            if role != ".":
                parts.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="{COLORS[role]}"/>')
            x += run
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def build():
    """Every exported file, as {name: bytes}."""
    favicons = {size: png(render(size, size // SIZE, 0, rounded=True)) for size in (16, 32, 48)}
    return {
        "favicon.svg": svg().encode(),
        "favicon-16.png": favicons[16],
        "favicon-32.png": favicons[32],
        "favicon.ico": ico(sorted(favicons.items())),
        # iPhone rounds the corners itself: a full tile, the drawing at 11 px per pixel, centered.
        "apple-touch-icon.png": png(render(180, 11, 2, rounded=False, extend_body=True)),
        "icon-192.png": png(render(192, 12, 0, rounded=True)),
        "icon-512.png": png(render(512, 32, 0, rounded=True)),
        # Android crops a maskable icon to a circle or squircle: keep the drawing inside the
        # central 80% safe zone (20 px per pixel = 320 px of 512).
        "icon-maskable-512.png": png(render(512, 20, 96, rounded=False, extend_body=True)),
    }


def outputs():
    """Every exported file, as {absolute path: bytes}. favicon.ico also goes at the site root,
    where browsers ask for it before reading any page."""
    files = {os.path.join(OUT, name): data for name, data in build().items()}
    files[os.path.join(ROOT, "frontend", "favicon.ico")] = files[os.path.join(OUT, "favicon.ico")]
    return files


def main():
    os.makedirs(OUT, exist_ok=True)
    for path, data in outputs().items():
        with open(path, "wb") as f:
            f.write(data)
        print(f"{os.path.relpath(path, ROOT):34} {len(data):>6} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
