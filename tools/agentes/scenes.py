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
