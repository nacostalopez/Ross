"""Scenes and portraits of the Aramal agents.

Scenes have no background of their own: the agent and its props stand on the app's panel,
so the same drawing works in the light and dark themes. No text is ever drawn inside a
sprite (the pixel font cannot render accents, comma or "$"); any label lives in HTML.
"""
from agent import agent, arm, shoulders
from px import Grid, scene_from_frames

# Skin/hair combinations; each person gets one according to their id.
VARIANTS = [
    dict(skin="a", shade="A", hair="h"),
    dict(skin="c", shade="C", hair="K"),
    dict(skin="d", shade="E", hair="K"),
    dict(skin="A", shade="C", hair="j"),
]

# Uniform per account role (the API's `role` values).
ROLES = {
    "owner": dict(outfit="suit", suit="S", lapel="T", accent="B"),
    "admin": dict(outfit="suit", suit="b", lapel="B", accent="Y", acc=["headset"]),
    "viewer": dict(outfit="shirt", cloth=("S", "T"), accent="G", acc=["goggles"], lens="B"),
}

# The module agent for "Tiendas y conectores": a technician with a hard hat and goggles.
TECHNICIAN = dict(outfit="coat", acc=["hardhat", "goggles"], hat=("Y", "y"), lens="B", accent="B", **VARIANTS[1])

STORE = [
    "KKKKKKKKKKKK",
    "KBWBWBWBWBWK",
    "KWBWBWBWBWBK",
    ".KKKKKKKKKK.",
    ".KLLLLLLLLK.",
    ".KLKKKLbbbK.",
    ".KLKwKLbbbK.",
    ".KLKwKLbbbK.",
    ".KLKKKLbbbK.",
    ".KLLLLLbbYK.",
    ".KLLLLLbbbK.",
    "KKKKKKKKKKKK",
]

HUB = [
    "KKKKKKKKKKKKK",
    "KNNNNNNNNNNNK",
    "KDDDNNNNNGGNK",
    "KNNNNNNNNNNNK",
    "KDDDNNNNNBBNK",
    "KNNNNNNNNNNNK",
    "KDDDNNNNNYYNK",
    "KNNNNNNNNNNNK",
    "KKKKKKKKKKKKK",
    ".KK.......KK.",
    ".KK.......KK.",
    ".KK.......KK.",
]

W, H, FLOOR = 48, 30, 28
CONNECTORS_FRAMES = 4
CONNECTORS_SECONDS = 1.6


def _connectors_frame(k):
    """One frame of the connectors scene: the technician plugs a cable, the port LED lights."""
    g = Grid(W, H)
    g.hl(0, FLOOR, W, "x")
    g.art(STORE, 1, FLOOR - len(STORE) + 1)
    hub_x, hub_y = 34, FLOOR - len(HUB) + 1
    g.art(HUB, hub_x, hub_y)
    ax, ay = 18, 7
    plug_x = 32 + (0, -1, -2, -1)[k % CONNECTORS_FRAMES]
    port_y = hub_y + 2
    # cable from the store door along the floor and up to the plug
    g.hl(13, 26, plug_x - 13 + 1, "G")
    g.vl(plug_x, port_y + 1, 26 - port_y, "G")
    agent(g, ax, ay, **TECHNICIAN)
    left, right = shoulders(ax, ay)
    arm(g, right[0], right[1], plug_x - 2, port_y - 1)
    g.rect(plug_x - 1, port_y - 1, 3, 2, "L")
    g.px(plug_x + 2, port_y, "K")
    g.px(plug_x - 1, port_y - 1, "K")
    # the port LED lights while the plug is in
    g.rect(hub_x + 9, hub_y + 2, 2, 1, "l" if k % CONNECTORS_FRAMES in (0, 3) else "e")
    arm(g, left[0], left[1], left[0] - 1, left[1] + 5)
    return g


def connectors():
    return scene_from_frames([_connectors_frame(k) for k in range(CONNECTORS_FRAMES)], CONNECTORS_SECONDS)


def _person(params, x, y, size):
    g = Grid(*size)
    agent(g, x, y, **params)
    return g


def head(params):
    """Head only (12x11, hat/headset rows included): a compact module marker for titles."""
    return _person(params, 0, 1, (12, 11))


def chip(role, variant):
    """Head and chest (12x18): the role's uniform is visible, sized for the topbar."""
    return _person({**ROLES[role], **VARIANTS[variant]}, 0, 1, (12, 18))


def bust(role, variant):
    """Framed 16x22 portrait with a plate, sized for the Perfil header."""
    params = {**ROLES[role], **VARIANTS[variant]}
    g = Grid(16, 22)
    g.rect(0, 0, 16, 22, "K")
    g.rect(1, 1, 14, 20, "N")
    for y in range(4, 20, 4):
        for x in range(3, 15, 4):
            g.px(x, y, "D")
    body = Grid(16, 22)
    agent(body, 2, 5, **params)
    for x in range(16):
        body.c[21][x] = "."  # the 22nd row is the frame
    g.blit(body)
    if role == "owner":
        g.px(10, 17, "Y")  # gold pin
    if role == "viewer":
        g.art(["KKKKKKKKKK", "KDDDDDDDDK", "KDGDBBBBDK", "KDDDDDDDDK", "KKKKKKKKKK"], 3, 16)  # tablet
    g.frame(0, 0, 16, 22, "K")
    return g


# ----------------------------------------------------------------------------------------------
# Module scenes (40x26): an agent and one prop on a floor line, used beside modal titles and in
# empty states. Each builds N full frames; scene_from_frames keeps the static part as background.

MW, MH, MFLOOR = 40, 26, 25


def _module_base():
    g = Grid(MW, MH)
    g.hl(0, MFLOOR, MW, "x")
    return g


def _module_agent(g, params, x=3, y=4):
    """Draw the agent at the left of a module scene; returns its (left, right) shoulders."""
    agent(g, x, y, **params)
    return shoulders(x, y)


ALERTS_AGENT = dict(outfit="coat", acc=["headset"], accent="R", **VARIANTS[2])
ALERTS_FRAMES = 4
ALERTS_SECONDS = 1.2

# Rays around the siren: two alternating patterns while it is lit.
_SIREN_RAYS = (
    [(23, 6), (35, 6), (29, 3), (24, 11), (34, 11), (22, 4), (36, 4)],
    [(22, 7), (36, 7), (27, 3), (31, 3), (23, 11), (35, 11)],
)


def _alerts_frame(k):
    """The watcher stands by a siren that lights up when CAC passes the threshold."""
    g = _module_base()
    g.rect(24, 22, 10, 3, "N")
    g.hl(24, 22, 10, "M")
    g.frame(24, 22, 10, 3, "K")
    g.rect(28, 13, 2, 9, "K")
    if k < 2:  # lit, rays alternating
        g.art([".KKKKKK.", "KRRPRRRK", "KRRPRRRK", "KRRRRRRK", "KrrrrrrK", "KKKKKKKK"], 25, 8)
        for x, y in _SIREN_RAYS[k]:
            g.px(x, y, "Y")
    else:      # dark
        g.art([".KKKKKK.", "KrrrrrrK", "KrrrrrrK", "KrrrrrrK", "KrrrrrrK", "KKKKKKKK"], 25, 8)
    _, right = _module_agent(g, ALERTS_AGENT)
    arm(g, right[0], right[1], 16, 16)
    return g


def alerts():
    return scene_from_frames([_alerts_frame(k) for k in range(ALERTS_FRAMES)], ALERTS_SECONDS)


REPORTS_AGENT = {**ROLES["owner"], **VARIANTS[3]}
REPORTS_FRAMES = 4
REPORTS_SECONDS = 1.6

# The stamp: a handle over a base, 4x3. It comes down onto the envelope, lifts and leaves a seal.
_STAMP = [".NN.", ".NN.", "MMMM"]
_STAMP_Y = (7, 10, 13, 9)  # top of the stamp in each frame: up, going down, pressed, lifted


def _reports_frame(k):
    """The owner stamps the weekly-summary envelope; the seal shows once the stamp lifts."""
    g = _module_base()
    g.rect(17, 12, 16, 12, "K")
    g.rect(18, 13, 14, 10, "W")
    g.hl(18, 13, 14, "L")
    for i in range(6):  # the flap's V
        g.px(18 + i, 14 + i, "L")
        g.px(31 - i, 14 + i, "L")
    g.rect(21, 9, 12, 4, "K")  # the sheet peeking out
    g.rect(22, 10, 10, 3, "W")
    g.hl(23, 11, 5, "M")
    if k == 3:
        g.disc(24, 18, 2, "y")
        g.disc(24, 18, 1, "Y")
        g.px(23, 17, "W")
    _, right = _module_agent(g, REPORTS_AGENT)
    stamp_y = _STAMP_Y[k]
    arm(g, right[0], right[1], 20, stamp_y)
    g.art(_STAMP, 22, stamp_y)
    return g


def reports():
    return scene_from_frames([_reports_frame(k) for k in range(REPORTS_FRAMES)], REPORTS_SECONDS)


# One agent per role, side by side. Left to right: x position, role, skin/hair variant.
TEAM_MEMBERS = [(0, "owner", 0), (14, "admin", 2), (28, "viewer", 1)]
TEAM_FRAMES = 4
TEAM_SECONDS = 2.0


def _team_frame(k):
    """Frame 0: everybody at rest; frames 1..3: each member in turn waves."""
    g = _module_base()
    for i, (x, role, variant) in enumerate(TEAM_MEMBERS):
        agent(g, x, 4, **{**ROLES[role], **VARIANTS[variant]})
        if k == i + 1:
            _, right = shoulders(x, 4)
            arm(g, right[0], right[1], x + 10, 8)
    return g


def team():
    return scene_from_frames([_team_frame(k) for k in range(TEAM_FRAMES)], TEAM_SECONDS)


AUDIT_AGENT = dict(outfit="coat", acc=["goggles"], lens="Y", accent="Y", **VARIANTS[0])
AUDIT_FRAMES = 4
AUDIT_SECONDS = 1.6
_LEDGER_ROWS = (16, 18, 20, 22)  # y of each entry in the register


def _audit_frame(k):
    """The auditor reads a register under a magnifier; row k is highlighted, and the
    third entry is flagged."""
    g = _module_base()
    g.rect(21, 14, 17, 10, "K")
    g.rect(22, 15, 15, 8, "W")
    for i, y in enumerate(_LEDGER_ROWS):
        g.hl(23, y, 13, "B" if i == k else "L")
    if k == 2:
        g.rect(34, 20, 2, 1, "R")
    lens = {(x, y) for y in range(3, 15) for x in range(21, 33) if (x - 26.5) ** 2 + (y - 8.5) ** 2 <= 30}
    g.outlined(lens, "w")
    g.px(24, 6, "W")
    g.px(25, 5, "W")
    g.px(24, 7, "W")
    g.line(31, 13, 36, 19, "K")
    g.line(32, 13, 37, 19, "y")
    g.line(31, 14, 36, 20, "K")
    _, right = _module_agent(g, AUDIT_AGENT)
    arm(g, right[0], right[1], 16, 13)
    return g


def audit():
    return scene_from_frames([_audit_frame(k) for k in range(AUDIT_FRAMES)], AUDIT_SECONDS)
