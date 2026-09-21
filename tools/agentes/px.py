"""Minimal pixel-art engine: role grids -> SVG paths, plus animated parts as frame strips.

Adapted from the sprite generator built for TERXPERIENCE_APP. Kept dependency-free
(Python 3 standard library only) on purpose: exporting must not need a build step.

A `Grid` is a matrix of one-character *roles* ("K" outline, "W" white, ...). '.' is
transparent. Roles are mapped to colors by `palette.py`, never here.
"""


class Grid:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.c = [["."] * w for _ in range(h)]

    def copy(self):
        g = Grid(self.w, self.h)
        g.c = [row[:] for row in self.c]
        return g

    def px(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.c[y][x] = c

    def get(self, x, y):
        return self.c[y][x] if 0 <= x < self.w and 0 <= y < self.h else "."

    def rect(self, x, y, w, h, c):
        for j in range(h):
            for i in range(w):
                self.px(x + i, y + j, c)

    def frame(self, x, y, w, h, c):
        self.rect(x, y, w, 1, c)
        self.rect(x, y + h - 1, w, 1, c)
        self.rect(x, y, 1, h, c)
        self.rect(x + w - 1, y, 1, h, c)

    def hl(self, x, y, w, c):
        self.rect(x, y, w, 1, c)

    def vl(self, x, y, h, c):
        self.rect(x, y, 1, h, c)

    def art(self, rows, x, y, mapping=None):
        """Stamp rows of characters at (x, y); ' ' and '.' are transparent."""
        mapping = mapping or {}
        for j, row in enumerate(rows):
            for i, ch in enumerate(row):
                if ch in " .":
                    continue
                self.px(x + i, y + j, mapping.get(ch, ch))

    def blit(self, other, x=0, y=0):
        for j in range(other.h):
            for i in range(other.w):
                if other.c[j][i] != ".":
                    self.px(x + i, y + j, other.c[j][i])

    def line(self, x0, y0, x1, y1, c):
        for (x, y) in bresenham(x0, y0, x1, y1):
            self.px(x, y, c)

    def disc(self, cx, cy, r, c):
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r + r * 0.4:
                    self.px(x, y, c)

    def outlined(self, cells, fill, outline="K"):
        """Fill `cells` and draw a 1px outline around them."""
        cells = set(cells)
        for (x, y) in cells:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if (x + dx, y + dy) not in cells:
                        self.px(x + dx, y + dy, outline)
        for (x, y) in cells:
            self.px(x, y, fill)

    def bbox(self):
        xs = [i for j in range(self.h) for i in range(self.w) if self.c[j][i] != "."]
        ys = [j for j in range(self.h) for i in range(self.w) if self.c[j][i] != "."]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def crop(self, x, y, w, h):
        g = Grid(w, h)
        for j in range(h):
            for i in range(w):
                g.c[j][i] = self.get(x + i, y + j)
        return g

    def roles(self):
        return {ch for row in self.c for ch in row if ch != "."}


def bresenham(x0, y0, x1, y1):
    pts = []
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy
    return pts


def thick(x0, y0, x1, y1, t=2):
    cells = set()
    for (x, y) in bresenham(x0, y0, x1, y1):
        for i in range(t):
            for j in range(t):
                cells.add((x + i, y + j))
    return cells


def paths_for(g, x_offset=0):
    """{role: 'M..'} with horizontal runs merged vertically (few, short paths)."""
    per = {}
    for y in range(g.h):
        x = 0
        while x < g.w:
            c = g.c[y][x]
            if c == ".":
                x += 1
                continue
            x0 = x
            while x < g.w and g.c[y][x] == c:
                x += 1
            per.setdefault(c, {}).setdefault((x0, x - x0), []).append(y)
    out = {}
    for c, runs in per.items():
        d = []
        for (x0, w), ys in runs.items():
            ys.sort()
            start = prev = ys[0]
            for y in ys[1:] + [None]:
                if y is not None and y == prev + 1:
                    prev = y
                    continue
                d.append("M%d %dh%dv%dh-%dz" % (x0 + x_offset, start, w, prev - start + 1, w))
                if y is not None:
                    start = prev = y
        out[c] = "".join(d)
    return out


def _layer(g, x_offset=0):
    """[[role_code, path], ...]; role_code is ord(role), used as the CSS class `c<code>`."""
    return [[ord(c), d] for c, d in sorted(paths_for(g, x_offset).items())]


class Part:
    """An animated part: a list of same-size frames swept with CSS steps()."""

    def __init__(self, frames, seconds=1.0):
        self.frames, self.seconds = frames, seconds


class Scene:
    def __init__(self, bg, parts, w, h):
        self.bg, self.parts, self.w, self.h = bg, parts, w, h

    def roles(self):
        used = self.bg.roles()
        for part in self.parts:
            for frame in part.frames:
                used |= frame.roles()
        return used

    def to_data(self):
        """JSON-ready dict: a static background layer plus animated parts.

        Each part is cropped to the bounding box of all its frames and stored as one
        horizontal strip (frame i is shifted right by i * width); the runtime animates it
        with translateX + steps(n), so no JavaScript runs per frame.
        """
        parts = []
        for part in self.parts:
            boxes = [f.bbox() for f in part.frames if f.bbox()]
            if not boxes:
                continue
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
            w, h, n = x1 - x0 + 1, y1 - y0 + 1, len(part.frames)
            paths = []
            for i, frame in enumerate(part.frames):
                paths += _layer(frame.crop(x0, y0, w, h), x_offset=i * w)
            parts.append({
                "l": round(100.0 * x0 / self.w, 3), "t": round(100.0 * y0 / self.h, 3),
                "w": round(100.0 * w / self.w, 3), "h": round(100.0 * h / self.h, 3),
                "n": n, "s": part.seconds, "vw": w * n, "vh": h, "p": paths,
            })
        return {"w": self.w, "h": self.h, "bg": _layer(self.bg), "parts": parts}


def scene_from_frames(frames, seconds):
    """Split full frames into a static background (cells equal in every frame) and one
    animated part holding only the cells that change."""
    w, h = frames[0].w, frames[0].h
    bg = Grid(w, h)
    diffs = [Grid(w, h) for _ in frames]
    for y in range(h):
        for x in range(w):
            cells = [f.get(x, y) for f in frames]
            if all(c == cells[0] for c in cells):
                if cells[0] != ".":
                    bg.px(x, y, cells[0])
            else:
                for k, c in enumerate(cells):
                    if c != ".":
                        diffs[k].px(x, y, c)
    return Scene(bg, [Part(diffs, seconds)], w, h)
