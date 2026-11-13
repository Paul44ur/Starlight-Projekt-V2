# Plan: the Night Sky look (UI 2)

The user chose a new look for every page: **Night Sky**, with three habits taken from the
"Workbench" direction. The three directions were drawn on a design canvas (Native, Night Sky,
Workbench; 2026-11-11). This plan turns the chosen one into blocks that helper models build,
one block per commit. `docs/plan-ui.md` (U1-U10) is the look this replaces; its structure
(tokens in `style.css`, shared blocks, German, screenshots) stays.

## Status

| Block | What | Needs | State |
|---|---|---|---|
| V1 | Foundation: tokens (night and day), the heading font, shared components | – | **done** (6500345) |
| V2 | The chat page | V1 | **done** (01a7407) |
| V3 | The Settings page | V1 | **done** (e3991ce) |
| V4 | The Notes page (editor, graph, boards) | V1 | **done** (01a7407) |
| V5 | Memory, Activity, Automations, Kill, Pair | V1 | **done** (01a7407) |
| V6 | The launcher window and the terminal's colours | V1 | **done** (604d7b6) |
| V7 | Review: phone, German, contrast, screenshots of every page, docs | V2-V6 | open |

## 1. The philosophy

1. **One light.** Gold (`--primary`) is only for the star and the ONE main action of a view
   (New conversation, Send, Save). Everything else is quiet. Links, selection, focus,
   switches and progress use violet (`--accent`).
2. **A calm night, and an equal day.** Dark is the default. Every token has a day value that
   is designed as carefully, not inverted.
3. **Room to breathe.** Rounded cards (16 px), 20 px inside, controls 44 px high on touch
   and 40 px with a mouse, few lines. Space separates things, not borders.
4. **Keys are shown** (from Workbench). An action with a shortcut shows it in a `<kbd>` next
   to it on wide screens (Send: Enter; search: Ctrl K; Kill switch: Ctrl Alt J).
5. **Numbers in mono** (from Workbench). Times, tokens, sizes, percentages and counts use the
   mono font with tabular figures (`.num`), so they line up.
6. **Details on demand** (from Workbench). On wide screens (1200 px and more) the chat shows a
   details pane: this answer, the context, this PC. Smaller screens do not.

## 2. Tokens (`style.css`)

Keep every existing token name (pages use them); change their values, and add the new ones.

| Token | Night (default) | Day |
|---|---|---|
| `--bg` | `#0B0E1A` | `#F5F6FB` |
| `--panel` | `#12162A` | `#FFFFFF` |
| `--panel2` | `#1A1F38` | `#ECEEF7` |
| `--border` | `#262C4A` | `#D8DBEA` |
| `--text` | `#E8EAF6` | `#14172B` |
| `--dim` | `#9AA1C4` | `#4E5578` |
| `--accent` (violet) | `#A99BFF` | `#5543D1` |
| `--on-accent` | `#0B0E1A` | `#FFFFFF` |
| `--you` (your messages) | `#1A1F38` | `#ECEAFD` |
| `--ok` | `#6EE7B7` | `#157A4B` |
| `--warn` | `#FDBA74` | `#A3510B` |
| `--bad` (red text) | `#FF8A80` | `#C0262D` |
| `--danger` (filled red) | `#B42318` | `#B42318` |
| new `--primary` (gold) | `#F2C14E` | `#F2C14E` |
| new `--on-primary` | `#1A1404` | `#1A1404` |
| new `--primary-edge` | `transparent` | `#B8860B` |
| new `--font-display` | `"Space Grotesk", var(--font-body)` | same |
| new `--font-body` | `"Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif` | same |
| new `--font-mono` | `ui-monospace, "Cascadia Mono", Consolas, monospace` | same |
| new `--r-4` | `16px` | same |

All text colours pass 4.5:1 on `--bg`, `--panel` and `--panel2` in both looks (checked). The
gold button on a day page needs its `--primary-edge` border (3:1 against white).
`--accent-tint`, the shadows and the code colours (`--hl-*`) are derived again for the new
values. High contrast (`prefers-contrast: more`), forced colours and reduced motion keep
their rules.

**The heading font.** Space Grotesk (OFL-1.1), the variable woff2 from the npm package
`@fontsource-variable/space-grotesk`, latin and latin-ext only, served from Starlight itself
(never from the internet: nothing leaves the PC). It is kept like the other bundles: a
folder `bundles/fonts` with a README and a build script, the file and its licence under
`static/vendor/`, a `.sha256` file and a test that checks it. Headings (`h1`, `h2`, page and
card titles, the brand word) use `--font-display`; everything else `--font-body`.

## 3. Shared components (`style.css`, `common.js`)

- **Buttons:** pill-shaped (`--r-pill`). `button.primary` is gold with `--on-primary` text
  and the `--primary-edge` border. Plain buttons: `--panel2` with a `--border` line.
  `button.danger` stays filled red; `button.danger-outline` is `--bad` text and line.
- **Fields** (`input`, `select`, `textarea`): `--panel2`, `--border`, radius 12, 44/40 px.
- **Cards** (`.card`): `--panel`, radius 16, padding 20, a 1px `--border` line.
- **Switches:** on is `--accent`, never gold.
- **Focus:** a 2px `--accent` ring, 2px apart, on every control (`:focus-visible`).
- **Questions** (`dialog.ask`), the quick switcher (`#quick`) and the shortcuts window:
  `--panel2`, radius 18, the lifted shadow; their buttons as above.
- **New helpers:** `.num` (mono, `font-variant-numeric: tabular-nums`), a shared `kbd` style
  (now only in the shortcuts window), and `.section-label` (12 px, upper case, 0.8px letter
  spacing, `--dim`).
- **The star:** the brand mark is the gold four-point star. `common.js` gets an icon
  `star` (filled, `--primary`), used in each page's brand row.

## 4. The pages

**Chat (V2).** As drawn on the canvas ("2 Night Sky · Chat"):
- The sidebar is a card inset 12 px on the night ground. Brand row: the star and "Starlight"
  in the display font. "New conversation" is the gold pill; "Private conversation" plain.
  A "Search or jump to… Ctrl K" button opens the quick switcher. Page links and conversations
  are 44 px rows, radius 12; the open one has `--panel2` behind it and a small `--accent` dot.
- Top bar: the conversation's name in the display font with its menu; on the right the
  context as a thin `--accent` bar with its percentage (`.num`), and the Kill switch as a
  `--bad` outline pill with `Ctrl Alt J` on wide screens.
- Messages: yours in a `--you` card on the right (radius 18 18 6 18). Starlight's answers sit
  on the ground with no card, after a small gold star. The tool steps fold into one chip
  ("Checked the server and read the log ▾") that opens the existing timeline. Stats in `.num`.
- The message box floats as a pill (radius 26) with a shadow; Send is a round gold button
  (Enter on wide screens).
- The existing right panel (`#side`: This PC, Context) becomes the details pane (philosophy
  6): This answer (tools, time, tokens), Context (bar, "8.8k of 32.8k"), This PC (model,
  kill switch, practice mode). Only from 1200 px.

**Settings (V3).** One centred column (max 760 px) of cards; each card title in the display
font. The safety level becomes three large choice cards (Careful, Balanced, Relaxed; the
chosen one has a 2px `--accent` line). Every existing card, switch and behaviour stays;
only the look changes.

**Notes (V4).** The tokens carry most of it. The sidebar, list and header follow the chat's
sidebar. Titles in the display font. The editor (CodeMirror), the graph (d3) and boards get
their colours from the tokens in both looks (check every place that has its own colour).

**Memory, Activity, Automations, Kill, Pair (V5).** Cards, titles and lists in the new
look; numbers in `.num`. The Kill page keeps its own red identity.

**The launcher window and the terminal (V6).** The tkinter window takes the night and day
values (`Palette` in `launcher_gui.py`), the gold primary button and the star. Tk cannot
load the woff2 font: headings use "Segoe UI Semibold". The terminal chat's colours
(`starlight/termstyle.py`) take the new `--accent` (tests compare them with `style.css`).

## 5. Rules for every block

- Read `docs/guide-for-helpers.md` first. One block, one commit; the lead reviews and merges.
- No behaviour changes in V2-V6: the same ids, texts, keys and server calls. Existing tests
  pass unchanged, except tests that check a colour or a class that this plan changes: update
  those to the new values and say so.
- Colours only through tokens. A page that needs a new value asks for a token in V1's file
  (report it; do not add one yourself in V2-V6).
- New texts (few: "Search or jump to…", the details pane's labels) need German entries, in
  ONE block per page at the place the brief names.
- Screenshots of every page touched: night, day, German, and 360 px wide.
