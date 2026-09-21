# Aramal agents: sprite generator

Generates the pixel-art agents of the Aramal frontend (one animated agent per module, and one
uniform per account role) and exports them into `frontend/agentes/`. You only need to run it
when a drawing, the palette or a uniform changes: the exported files are committed.

Adapted from the generator built for TERXPERIENCE_APP (same engine, English code, Aramal's
palette and roles). Python 3 standard library only.

## Use

```bash
python tools/agentes/export.py     # rewrites frontend/agentes/ and prints gzip sizes
pytest tools/agentes               # checks the output is current, complete and within budget
```

| Output | What it is |
| --- | --- |
| `frontend/agentes/paleta.css` | Role colors: light as the base, dark overrides under `:root[data-theme="dark"]` |
| `frontend/agentes/escenas/<name>.json` | One animated scene per file; the browser fetches it only where it is used |
| `frontend/agentes/retratos.json` | Module heads, topbar chips and Perfil busts, per role and skin/hair variant |

Do not edit those by hand: the next export overwrites them, and `pytest` fails while they are
stale. `frontend/agentes/agentes.js` and `agentes.css` are hand-written (the runtime).

## How it is built

- `px.py`: the engine. A `Grid` holds one-character *roles*; `rects_for` turns it into rectangles
  (horizontal runs merged vertically). An animated part is a strip of frames that the runtime
  slides with CSS `steps(n)`, so no JavaScript runs per frame.
- What is exported per color is one flat list `[x, y, w, h, x, y, w, h, ...]`, in reading order and
  delta-coded by `delta_coded()` (each `y` relative to the previous rectangle's and, on the same row,
  each `x` too), which gzips about 28% smaller than SVG path text. The runtime rebuilds the paths in
  `rects()` of `agentes.js`: if you change the encoding, change both (`pytest` decodes the numbers
  and compares them to the grid, and the pixel output can be compared before and after in a browser).
- `palette.py`: role to color. Aramal's base theme is light, so `LIGHT` has every role and `DARK`
  only the ones that change.
- `agent.py`: the 12x21 agent with three uniforms (`suit`, `coat`, `shirt`) and accessories.
- `scenes.py`: the scenes, module heads and portraits; `ROLES` maps each API role to its uniform.
- `export.py`: writes everything and enforces the weight budget.

Ross, the mascot, is `ROSS` in `scenes.py` (owner uniform, skin/hair variant 0): the login agent and the
app mascot are the same character. Scene families and sizes: the connectors scene is 48x30; module scenes (alerts, reports, team,
audit, profile, dashboard) are 40x26; the login welcome agent (`login-*`) is head and chest, 22x15;
the app mascot (`mascota-*`) is full body, 26x23. Which scene is shown when is decided by hand-written
code in `frontend/agentes/mascota.js`, not here.

## Roles

| Role (`/auth/me`) | Uniform |
| --- | --- |
| `owner` | Navy suit, celeste tie, gold pin |
| `admin` | Blue suit, headset, gold tie |
| `viewer` | Light shirt, goggles, tablet (in the portrait) |

Each person gets one of 4 skin/hair variants, always the same one for the same user id
(`variantFor` in `agentes.js`).

## Rules

- **No text inside a sprite.** The pixel font of the original generator has no accents, no `Ñ`,
  no comma, no `$` and no parentheses, so 16 of 23 real Aramal phrases broke. Every label lives in
  HTML; a sprite may carry at most an acronym such as ROAS or CAC.
- **Sprites have no background.** They stand on the app's `--panel`, so one drawing serves both
  themes and only outlines, uniforms and whites change between them.
- **Weight budget** (gzip, enforced by `export.py`): 4 KB per scene and 25 KB for all of
  `frontend/agentes/`, hand-written files included.
- **Motion:** `prefers-reduced-motion` stops every animation, and the topbar button pauses them
  (`aramal_agents_paused` in `localStorage`).

## Adding a scene

1. Draw it in `scenes.py`. Build its frames as `Grid`s and use `scene_from_frames` (it splits the
   static background from the part that moves). Module scenes are 40x26 (see "Scene families"
   above for the others).
2. Register it in `SCENES` in `export.py` and run the export.
3. Show it with `<div class="pxa pxa-scene" style="--w:40;--h:26" data-agent-scene="<name>"></div>`
   (the `--w`/`--h` reserve its space). Inside a hidden modal or view it is drawn, and its file
   downloaded, when that section is shown; markup you insert later needs
   `Agents.hydrate(container)`. Beside a modal title use `.agents-modal-head`; beside a view
   title, `.agents-heading`.
4. Add it to `MODULE_SURFACES` in `e2e/agents.js` (how to reach it and where it is drawn) and run
   that script: both themes, desktop and 360 px, lazy download, animation, no overflow.
