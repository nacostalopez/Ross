"""The base agent: a 12x21 px body (head + torso) with per-role uniforms and accessories.

Template roles: 1 skin, 2 skin shade, 3 hair, 4/5 main color and shade of the uniform,
6 accent, plus the shared palette roles (K outline, W white, L light, S/T suit).
"""
from px import thick

W, H = 12, 21

HEAD = [
    "...KKKKKK...",
    "..K333333K..",
    ".K33333333K.",
    ".K33111133K.",
    ".K11111111K.",
    ".K11K11K11K.",
    ".K11111111K.",
    ".K11122111K.",
    "..K111111K..",
    "...KKKKKK...",
]

TORSO = {
    # suit with tie: Owner and Admin
    "suit": [
        "..K4WWWW4K..",
        "..K4W66W4K..",
        "..K456654K..",
        "..K464444K..",
        "..K444444K..",
        "..KKKKKKKK..",
        "..K44KK44K..",
        "..K44KK44K..",
        "..K44KK44K..",
        "..K44KK44K..",
        ".KKKKKKKKKK.",
    ],
    # open white coat over a dark shirt: technicians
    "coat": [
        "..KWLSSLWK..",
        "..KWWSSWWK..",
        "..KW6SSWWK..",
        "..KWWSSWWK..",
        "..KWLSSLWK..",
        "..KWWSSWWK..",
        "..KWWSSWWK..",
        "..KLLLLLLK..",
        "..KSSKKSSK..",
        "..KSSKKSSK..",
        ".KKKKKKKKKK.",
    ],
    # light shirt with a pocket: Viewer
    "shirt": [
        "..KWWWWWWK..",
        "..KWLWWLWK..",
        "..KWWLLWWK..",
        "..KWWWWWWK..",
        "..KW66WWWK..",
        "..KWWWWWWK..",
        "..K44KK44K..",
        "..K44KK44K..",
        "..K44KK44K..",
        "..K44KK44K..",
        ".KKKKKKKKKK.",
    ],
}

# Accessories as (rows, dy) relative to the head (y=0). H/h hat, L lens, 3 hair, M headset.
ACCESSORIES = {
    "hardhat": (["...KKKKKK...",
                 "..KHHHHHHK..",
                 ".KHHHHHHHHK.",
                 "KhhhhhhhhhhK"], -1),
    "goggles": ([".K1LLKKLL1K."], 5),
    "headset": (["..MMMMMMMM..",
                 "..M......M..",
                 ".M........M.",
                 ".M........M.",
                 "MM........MM",
                 "MM........MM",
                 "MM........MM"], -1),
}

# Sleeve/hand colors of the last agent drawn; arm() uses them by default.
_CURRENT = {"sleeve": "S", "hand": "a"}


def agent(g, x, y, skin="a", shade="A", hair="h", suit="S", lapel="T", accent="B",
          acc=None, hat=("Y", "y"), lens="B", outfit="suit", cloth=("S", "T")):
    mapping = {"1": skin, "2": shade, "3": hair, "6": accent}
    if outfit == "suit":
        mapping.update({"4": suit, "5": lapel})
        sleeve = suit
    elif outfit == "coat":
        sleeve = "W"
    else:  # "shirt"
        mapping.update({"4": cloth[0], "5": cloth[1]})
        sleeve = "W"
    g.art(HEAD + TORSO[outfit], x, y, mapping)
    for name in acc or []:
        rows, dy = ACCESSORIES[name]
        g.art(rows, x, y + dy, {"H": hat[0], "h": hat[1], "3": hair, "L": lens, "1": skin})
    _CURRENT["sleeve"], _CURRENT["hand"] = sleeve, skin
    return dict(_CURRENT)


def arm(g, sx, sy, hx, hy, sleeve=None, hand=None):
    """A 2px-thick outlined arm from the shoulder (sx, sy) to a 2x2 hand at (hx, hy)."""
    sleeve = sleeve or _CURRENT["sleeve"]
    hand = hand or _CURRENT["hand"]
    g.outlined(thick(sx, sy, hx, hy, 2), sleeve)
    g.rect(hx, hy, 2, 2, hand)


def shoulders(x, y):
    """Left and right shoulder points (as seen on screen) for an agent drawn at (x, y)."""
    return (x + 1, y + 11), (x + 9, y + 11)
