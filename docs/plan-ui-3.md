# Plan: the Paper look (UI 3)

The user asked for a nicer look that does not look AI-made, and for better functions
(2026-11-15). Two directions were drawn as mockups, "Werkzeug" (a Windows tool) and "Papier"
(paper). The user chose **Paper**. It replaces Night Sky (`docs/plan-ui-2.md`); the structure
stays: tokens in `style.css`, shared blocks, German, screenshots, one block per commit.

## Status

| Block | What | Needs | State |
|---|---|---|---|
| P1 | Foundation: tokens (light and dark), fonts, buttons, fields, lines, the logo and app icons | – | **done** (56553e5) |
| P2 | The chat page: messages as a document, a plain start, a calmer side pane | P1 | open |
| P3 | The Settings page | P1 | open |
| P4 | The Notes page (editor, graph, boards) | P1 | open |
| P5 | Today, Memory, Activity, Automations, Kill, Pair, Statistics | P1 | open |
| P6 | The launcher window, the tray, the terminal's colours | P1 | open |
| F1 | Find in the open conversation (Ctrl F) | P2 | open |
| F2 | Undo instead of a question: delete and archive a conversation | P2 | open |
| F3 | Text size and density, per device | P1 | open |
| P7 | Review: phone, German, contrast, screenshots of every page, docs | all | open |

Order: P1 alone; then P2, P3, P5; then P4, P6, F3; then F1, F2; then P7.

## 1. What made it look AI-made, and what replaces it

| Before (Night Sky) | Paper |
|---|---|
| The four-point sparkle as the logo, everywhere | The word **Starlight** in a serif; the app icon is a serif "S" |
| Navy, violet links, gold main button | Warm paper and ink; one deep green for actions, links and focus |
| Pill buttons, 16 px rounded cards, soft shadows | Square buttons (radius 2 px), thin lines instead of boxes, no shadows at rest |
| Chat bubbles; answers after a gold star | A document: "DU 09:12" / "STARLIGHT 09:12 · 2,4 s" above each message |
| A centred logo, "Ask anything" and suggestion chips | A plain start: the conversation's title, recent conversations, a few things to ask as plain links |
| Upper-case letter-spaced section labels | Small italic serif labels ("Heute", "Gestern") |
| Space Grotesk headings | Serif headings and answers; a plain sans for controls |

## 2. Tokens (`style.css`)

Keep every token name (pages use them); change their values. Each text colour must pass
4.5:1 on `--bg`, `--panel` and `--panel2` in both looks (P1 checks and adjusts these values,
keeping the character).

| Token | Light (default) | Dark |
|---|---|---|
| `--bg` (sidebars, page ground) | `#F6F3EC` | `#1D1B18` |
| `--panel` (the reading surface) | `#FBF9F4` | `#23211D` |
| `--panel2` (fields, selected row) | `#EFEADF` | `#2C2924` |
| `--border` (lines) | `#DDD6C8` | `#3A362F` |
| `--text` (ink) | `#26221D` | `#ECE6DA` |
| `--dim` | `#665E53` | `#A99F8F` |
| `--accent` (actions, links, focus, switches) | `#2F5D50` | `#8DBDAA` |
| `--on-accent` | `#FBF9F4` | `#1D1B18` |
| `--primary` (the one main action) | same as `--accent` | same as `--accent` |
| `--on-primary`, `--primary-edge` | `--on-accent`, `transparent` | same |
| `--ok` / `--warn` / `--bad` | `#2E6B3F` / `#8A5A12` / `#A3361E` | `#8FC79C` / `#E0B46A` / `#F0A08A` |
| `--danger` (filled) | `#A3361E` | `#A3361E` |
| `--font-display` (headings, answers) | `Charter, "Bitstream Charter", Cambria, Georgia, serif` | same |
| `--font-body` (controls, lists) | `"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif` | same |
| `--font-mono` | as now | same |
| radius `--r-1`..`--r-4`, `--r-pill` | 2 px everywhere (a switch's knob stays round) | same |
| shadows | none at rest; only menus and dialogs keep a faint one | same |

Light is the default now (paper); dark is the equal twin. "Like your system" stays. High
contrast, forced colours and reduced motion keep their rules. No font is bundled: Windows has
Cambria and Georgia; the Space Grotesk bundle is removed with its route and tests.

## 3. Shared components

- **Buttons:** rectangles, 1 px line in `--text` (quiet) or filled `--primary` (the one main
  action). Text, not icons, where there is room ("Senden", "Kopieren").
- **Fields:** `--panel2` or white-ish, a 1 px `--border` line, radius 2.
- **Cards** become sections: a heading and a thin rule, no box. Where a box is needed (a
  dialog, a menu), `--panel` with a 1 px `--border` line.
- **Labels** (`.section-label`): small italic serif in `--dim`, normal case.
- **Lists:** rows 36 px with a mouse, 44 px on touch; the open one has `--panel2` behind it
  and a 2 px `--accent` line on the left.
- **The logo:** the word "Starlight" in `--font-display`, no mark. The `star` icon in
  `common.js` goes. App icons (`icon.svg`, PNGs, `.ico`, `ICON_PNG`): a serif "S" in
  `--on-accent` on a `--accent` square, drawn as a path (no font needed), made by
  `bundles/icons/make_icons.py`.
- Keys stay shown next to actions; numbers stay in mono (`.num`).

## 4. The pages

**Chat (P2).** As in the mockup: the sidebar on `--bg` with a thin line, the main column on
`--panel`; a top bar with the title in the serif, "12 Nachrichten · Kontext 27 %" in `--dim`,
"Teilen" and the kill switch as outlined buttons. Messages are a document 680 px wide: a
small upper-case line with who and when, the text under it; Starlight's answers in the serif;
tool steps as one italic dim line that opens the timeline; Kopieren, Neu erzeugen, Abzweigen
as plain text links. The message box is a plain bordered box with "Senden"; the attach,
photo, speak and talk buttons as small text-and-icon controls under it. The start of a new
conversation: no logo; "Neues Gespräch" as the title, then "Zuletzt" with the last three
conversations and "Du kannst fragen:" with the starter suggestions as plain links.
**Calmer side pane:** shows only what has a value (this answer, context, model); servers,
logins and secrets move behind a "Zugänge in den Einstellungen" link.

**Settings (P3), Notes (P4), other pages (P5).** Sections with serif headings and rules in one
column; the tokens carry most of it. The editor, graph and boards take their colours from
the tokens in both looks. The Kill page keeps its own red identity.

**Launcher and terminal (P6).** The tkinter window takes the Paper values (`Palette` in
`launcher_gui.py`), square buttons, "Cambria" for headings; the tray icon and the window icon
are the new "S". The terminal chat's colours take the new accent.

## 5. The functions

**F1. Find in the conversation.** Ctrl F (and a "Suchen" control in the top bar) opens a find
bar over the messages: every hit is marked, Enter and Shift Enter go to the next and the
previous one, "3 von 11" shows where you are, Esc closes it. Only the page's text is searched
(the conversation as shown); nothing goes to the server.

**F2. Undo instead of a question.** Deleting or archiving one conversation happens at once and
shows "Gelöscht. Rückgängig" for 10 seconds; Undo puts it back as it was. The server keeps a
deleted conversation in a waste folder until the time is up (and on the next start it empties
what is older than a day), so nothing is lost if the page closes. Deleting several things,
and everything else that asks today, still asks.

**F3. Text size and density.** Settings, "Darstellung": text size (klein, normal, groß) and
lists (kompakt, luftig). Kept per device in the browser; every page follows it.

## 6. Rules for every block

- Read `docs/guide-for-helpers.md` first (P1 brings its look section up to date).
- No behaviour changes in P1-P6 except those named here: the same ids, keys and server calls.
  Tests that check a colour, a class or a text this plan changes are updated, and the report
  names them.
- Colours only through tokens. New texts need German, in ONE block per block at the place the
  brief names.
- Screenshots of every page touched: light, dark, German, and 360 px wide.
