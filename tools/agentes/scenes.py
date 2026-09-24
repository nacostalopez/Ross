"""Scenes and portraits of the Ross agents.

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

# Ross, the app's mascot: the same character at the login and across the app (toast, loading, True ROAS
# mood). Name and look are deliberately gender-neutral: short hair, suit and tie, no pronoun anywhere.
ROSS = {**ROLES["owner"], **VARIANTS[0]}

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


PROFILE_AGENT = {**ROLES["viewer"], **VARIANTS[2], "acc": []}
PROFILE_FRAMES = 4
PROFILE_SECONDS = 1.6


def _profile_frame(k):
    """A person holds up their ID card on a lanyard; it sways, then the last line turns into a
    green check (verified)."""
    g = _module_base()
    x0 = 19 + (0, 1, 0, -1)[k]
    g.rect(x0, 7, 18, 13, "K")
    g.rect(x0 + 1, 8, 16, 11, "W")
    g.rect(x0 + 1, 8, 16, 3, "B")
    g.hl(x0 + 3, 9, 5, "K")
    g.rect(x0 + 3, 12, 6, 6, "b")  # photo
    g.rect(x0 + 5, 13, 2, 2, "a")
    g.rect(x0 + 4, 16, 4, 2, "S")
    g.hl(x0 + 10, 12, 6, "M")
    g.hl(x0 + 10, 14, 4, "M")
    g.hl(x0 + 10, 16, 6, "G" if k >= 2 else "M")
    g.line(x0 + 5, 7, 27, 3, "M")  # lanyard
    g.line(x0 + 12, 7, 28, 3, "M")
    g.px(27, 3, "K")
    g.px(28, 3, "K")
    _, right = _module_agent(g, PROFILE_AGENT)
    arm(g, right[0], right[1], 17, 13)
    return g


def profile():
    return scene_from_frames([_profile_frame(k) for k in range(PROFILE_FRAMES)], PROFILE_SECONDS)


DASHBOARD_AGENT = {**ROLES["admin"], **VARIANTS[0]}
DASHBOARD_FRAMES = 4
DASHBOARD_SECONDS = 1.6
_BARS = (3, 5, 4, 7, 9)              # bar heights once fully grown
_BAR_GROWTH = (0.5, 0.75, 1.0, 1.0)  # how much of each bar is drawn per frame


def _dashboard_frame(k):
    """An analyst points at a board whose bars grow, the last one sparkling at the end."""
    g = _module_base()
    x, y, w, h = 19, 4, 20, 16
    g.rect(x, y, w, h, "K")
    g.rect(x + 1, y + 1, w - 2, h - 2, "D")
    for gy in (y + 5, y + 9, y + 13):
        for gx in range(x + 2, x + w - 2, 2):
            g.px(gx, gy, "N")  # dotted grid
    g.px(x + 3, y + 3, "B")
    g.hl(x + 5, y + 3, 6, "M")
    for i, full in enumerate(_BARS):
        height = max(1, round(full * _BAR_GROWTH[k]))
        bx, top = x + 3 + i * 3, y + h - 2 - height
        g.rect(bx, top, 2, height, "G")
        g.hl(bx, top, 2, "l")
        if k == 3 and i == len(_BARS) - 1:
            g.px(bx, top - 1, "W")
    g.rect(x + 8, y + h, 4, 4, "K")  # stand
    g.rect(x + 4, MFLOOR - 2, 12, 2, "K")
    _, right = _module_agent(g, DASHBOARD_AGENT)
    arm(g, right[0], right[1], 16, 15)
    return g


def dashboard():
    return scene_from_frames([_dashboard_frame(k) for k in range(DASHBOARD_FRAMES)], DASHBOARD_SECONDS)


# ----------------------------------------------------------------------------------------------
# Ross at the login (head and chest, 22x15): the welcome agent peeks out from behind the login card and
# reacts to what the person is doing. Shown by frontend/agentes/mascota.js.

LOGIN_W, LOGIN_H = 22, 15
LOGIN_AGENT = ROSS


def _login_base(dx=0):
    g = Grid(LOGIN_W, LOGIN_H)
    agent(g, 5 + dx, 0, **LOGIN_AGENT)
    left, right = shoulders(5 + dx, 0)
    return g, left, right


def _login_frames(fn, n):
    return [fn(k) for k in range(n)]


def _login_rest_frame(k):
    """Rest: hands on the card's edge, a wave on the third frame, a blink on the fourth."""
    g, left, right = _login_base()
    arm(g, left[0], left[1], 1, 13)
    arm(g, right[0], right[1], 18, 6 if k == 2 else 13)
    if k == 3:
        g.px(9, 5, LOGIN_AGENT["shade"])
        g.px(12, 5, LOGIN_AGENT["shade"])
    return g


def _login_invite_frame(k):
    """First visit: points down at the "Crear cuenta" tab, the hand bobbing."""
    g, left, right = _login_base()
    arm(g, left[0], left[1], 1, 13)
    arm(g, right[0], right[1], 19, (12, 13, 12, 11)[k])
    return g


def _login_shy_frame(k):
    """Password field focused: both hands over the eyes; the right one slips on the second frame."""
    g, _, _ = _login_base()
    skin, sleeve = LOGIN_AGENT["skin"], LOGIN_AGENT["suit"]
    g.outlined({(x, y) for x in (6, 7) for y in range(8, 13)}, sleeve)
    g.outlined({(x, y) for x in (14, 15) for y in range(8, 13)}, sleeve)
    g.outlined({(x, y) for x in (8, 9, 10) for y in range(4, 8)}, skin)
    right_x = (11, 12, 13) if k == 0 else (12, 13, 14)
    g.outlined({(x, y) for x in right_x for y in range(4, 8)}, skin)
    return g


def _login_error_frame(k):
    """Login or registration failed: shakes its head, an alert drop beside it."""
    dx = (0, -1, 1, 0)[k]
    g, left, right = _login_base(dx)
    arm(g, left[0], left[1], 1 + dx, 13)
    arm(g, right[0], right[1], 18 + dx, 13)
    for y in (1, 2, 4):
        g.px(17, y, "R")
    return g


def _login_worried_frame(k):
    """Weak password (or an expired session): hands on the cheeks, a sweat drop running down."""
    g, left, right = _login_base()
    arm(g, left[0], left[1], 3, 7)
    arm(g, right[0], right[1], 16, 7)
    dy = (0, 1, 2, 3)[k]
    g.px(18, 2 + dy, "B")
    g.px(18, 3 + dy, "B")
    return g


def _login_thumbs_frame(k):
    """Strong password: thumbs up and a sparkle."""
    g, left, right = _login_base()
    arm(g, left[0], left[1], 1, 13)
    arm(g, right[0], right[1], 18, 6)
    g.outlined({(18, 4), (18, 5)}, LOGIN_AGENT["skin"])
    sx, sy = ((2, 3), (4, 1))[k % 2]
    for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
        g.px(sx + dx, sy + dy, "Y")
    return g


def _confetti(g, k, w, h):
    base = [(2, 0), (7, 1), (12, 0), (17, 1), (20, 0), (4, 4), (15, 3), (9, 5)]
    for i, (x, y) in enumerate(base):
        g.px(x % w, (y + 3 * k + i) % (h - 3), "YBGR"[i % 4])


def _login_celebrate_frame(k):
    """Account created: both arms up under falling confetti."""
    g, left, right = _login_base()
    arm(g, left[0], left[1], 1, 3)
    arm(g, right[0], right[1], 19, 3)
    _confetti(g, k, LOGIN_W, LOGIN_H)
    return g


def login_rest():
    return scene_from_frames(_login_frames(_login_rest_frame, 4), 3.2)


def login_invite():
    return scene_from_frames(_login_frames(_login_invite_frame, 4), 1.2)


def login_shy():
    return scene_from_frames(_login_frames(_login_shy_frame, 2), 1.2)


def login_error():
    return scene_from_frames(_login_frames(_login_error_frame, 4), 0.8)


def login_worried():
    return scene_from_frames(_login_frames(_login_worried_frame, 4), 1.2)


def login_thumbs():
    return scene_from_frames(_login_frames(_login_thumbs_frame, 4), 1.2)


def login_celebrate():
    return scene_from_frames(_login_frames(_login_celebrate_frame, 4), 1.0)


# ----------------------------------------------------------------------------------------------
# The app's mascot (full body, 26x23): the agent of the login hero proposal, now the voice of the
# app's states (loading, error) and the mood of the True ROAS card.

MASCOT_W, MASCOT_H = 26, 23
MASCOT_AGENT = ROSS


def _mascot_base():
    g = Grid(MASCOT_W, MASCOT_H)
    agent(g, 11, 2, **MASCOT_AGENT)
    left, right = shoulders(11, 2)
    return g, left, right


def _mascot_loading_frame(k):
    """Loading: types on a laptop while three dots count."""
    g, _, _ = _mascot_base()
    g.rect(7, 14, 12, 7, "K")
    g.rect(8, 15, 10, 5, "M")
    g.rect(12, 17, 2, 1, "B")
    g.rect(6, 21, 14, 1, "K")
    g.rect(9 + (k % 2) * 6, 20, 2, 1, MASCOT_AGENT["skin"])
    for i in range(3):
        g.px(12 + i * 3, 0, "B" if i == k % 3 else "M")
    return g


def _mascot_error_frame(k):
    """Error: holds an unplugged cable that sparks, an alert mark above the head."""
    g, left, right = _mascot_base()
    arm(g, left[0], left[1], 10, 19)
    arm(g, right[0], right[1], 22, 15)
    g.vl(23, 17, 4, "G")
    g.rect(22, 21, 3, 2, "L")
    if k % 2 == 0:
        for x, y in ((21, 20), (25, 20), (23, 19)):
            g.px(x, y, "Y")
    for y in (1, 2, 4):
        g.px(18, y, "R")
    return g


def _mascot_happy_frame(k):
    """Good result: both arms up under confetti."""
    g, left, right = _mascot_base()
    arm(g, left[0], left[1], 6, 4)
    arm(g, right[0], right[1], 24, 4)
    _confetti(g, k, MASCOT_W, MASCOT_H)
    return g


def _mascot_worried_frame(k):
    """Result under the minimum: hands on the head and a sweat drop."""
    g, left, right = _mascot_base()
    arm(g, left[0], left[1], 8, 8)
    arm(g, right[0], right[1], 22, 8)
    dy = (0, 1, 2, 3)[k]
    g.px(24, 3 + dy, "B")
    g.px(24, 4 + dy, "B")
    return g


def mascot_loading():
    return scene_from_frames([_mascot_loading_frame(k) for k in range(4)], 1.2)


def mascot_error():
    return scene_from_frames([_mascot_error_frame(k) for k in range(4)], 0.8)


def mascot_happy():
    return scene_from_frames([_mascot_happy_frame(k) for k in range(4)], 1.0)


def mascot_worried():
    return scene_from_frames([_mascot_worried_frame(k) for k in range(4)], 1.2)
