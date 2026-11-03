# Plan: polish the UI of every interface

A memo for whoever does this work, most likely a future Claude session. It holds the goal,
the honest state of each interface today, and the work split into blocks. Keep the **Status**
table current as blocks land. The user is German, non-technical, on Windows 11, and uses the
launcher (`Starlight.exe`) and the web chat the most; the phone is secondary.

## Goal and ground rules
- **Goal:** make every interface look and feel polished and calm — not a redesign, a refinement.
  Starlight already works and is accessible; this raises it from "clean and functional" to
  "considered and pleasant", the same across the launcher window, the web pages and the terminal.
- **Keep what is good.** The accessibility is strong (focus rings, `prefers-contrast`,
  `forced-colors`, `prefers-reduced-motion`, skip links, ARIA roles, German parity). No block
  may regress any of it. Behaviour, wording meaning and the consent/permission gates stay
  exactly as they are — this is looks, not logic.
- **No new runtime dependencies on the web.** Starlight ships vanilla CSS and JS; the only
  bundles are the editor, graph and Mermaid (built in `bundles/`). Do not add a web framework
  or a CSS library. The launcher is standard-library tkinter; keep it so.
- **One look, defined once.** Shared tokens and utilities live in `starlight/web/static/style.css`;
  pages stop re-inventing the same spacing, radius and colour inline.
- **Each block ships on its own:** the change, light and dark screenshots (German too where text
  shows), the existing tests still green, a short note in the docs, then commit and push. German
  entries for any new text (`starlight/web/static/i18n.js`, `launcher` `tr`).

## What we have now (honest assessment)
- **Design system (`style.css`, 153 lines).** Good bones: dark/light tokens, `--accent` blue,
  panel/border/text/dim, status colours, a code palette, `.card`, `.pill`, `.row`, `.toast`,
  table styles, a strong accessibility section. Gaps: no spacing/size/type **scale** (magic
  numbers everywhere), one elevation, no motion tokens, button/pill variants are ad hoc, and
  each page carries its own `<style>` block that drifts from the system.
- **Web chat (`chat.html`, 1154 lines, ~158 lines of inline CSS).** The flagship: a left
  conversation drawer (`#convs`), the header, the `#log` (`role="log"`), a composer
  (`#input` textarea + `#send`), a right side panel (`#side`). Messages are `.msg` / `.msg.you`
  with little structure — room for clearer roles, rhythm, a streaming cursor, code-copy, a
  tidy scroll-to-bottom, and a calmer composer.
- **Form/report pages.** `settings.html` (534), `automations.html` (399), `memory.html` (174),
  `activity.html` (128), `kill.html` (52), `pair.html` (43). Each has its own inline `<style>`.
  They are clear but dense; cards, tables and empty states could be more consistent and easier
  to scan.
- **Notes page.** Newest and most polished (its own `notes.css` and six scripts). Needs only a
  light pass for consistency with the refreshed system.
- **The launcher window (`launcher/launcher_gui.py`, 2498 lines).** Themed tkinter (clam, custom
  colours and fonts, DPI scaling). The first thing the user sees. Already decent; spacing,
  type and the accent can be brought in line with the web, and the wizard/status/progress made
  clearer.
- **The terminal chat (`starlight/cli.py`, 1111 lines).** Hand-rolled ANSI, no `rich`. Plain
  but functional. Colours, light Markdown rendering, a streaming spinner and a status line can
  be refined to match the web accent.

## Status

Work goes block by block. One block is one session and one commit; mark it **done** here (with
its commit) when it is pushed, and move **next** on. Order is by who sees it most: foundation
first, then the launcher (first impression), then the chat (daily driver).

| Block | What | Size | Needs | State |
|---|---|---|---|---|
| U1 | Design-system foundation (scales, tokens, shared utilities) | M | – | **done** (3771c6d) |
| U2 | The launcher window (tkinter polish) | M | U1 (tokens as reference) | **done** (d5bac3c) |
| U3 | The web chat page (messages, composer, streaming) | L | U1 | **done** (f5eb064) |
| U4 | Settings page (cards, forms, the permission UI) | M | U1 | **done** (1af5cf7) |
| U5 | Automations, Memory, Activity, Kill, Pair | M | U1 | **done** (0f9b0c1) |
| U6 | Notes page: light consistency pass | S | U1 | **done** (72fddd5) |
| U7 | Responsive / phone pass (chat, settings, memory) | M | U3, U4 | **done** |
| U8 | Micro-interactions and feedback (toasts, loading, focus, shortcuts) | M | U1 | **next** |
| U9 | Icons and brand (one icon set, logo, favicons, kill/pair screens) | S–M | U1 | open |
| U10 | Terminal chat refinement (ANSI theme, Markdown, spinner) | M | – | open |

Last updated: 2026-11-03. U1 built: `style.css` now carries scales (spacing `--s-*`, radius
`--r-*`, type `--fs-*`), elevation (`--shadow-1/2`), motion (`--ease`, `--dur*`) and an
`--accent-tint`; refined button states with `.primary`/`.quiet` variants, input focus, a soft
card shadow, and `.stack`/`.cluster`/`.muted`/`.truncate` utilities. No class renamed; the
accessibility block and the guarded strings are unchanged. Verified in both themes.
U2 built: one palette for the window and the pages (light `--accent` #2563eb, new
`--on-accent` and `--danger`; white on the old light-blue or light-red fills failed WCAG AA
at 2.4:1 and 3.4:1); the status page's main buttons large, its tools under **Tools** and
outlined; hover shades; a thin scrollbar; a focus ring only after Tab. The status page was
English after its first update even on a German Windows: its texts now go through `tr()`
and `trf()`, with German lines. `tests/test_launcher_look.py` guards all of it.
U3 built: messages in the 900 px column of the message box, softer bubbles, "Starlight" with
its logo (your name only for screen readers); older answers show Copy and Branch in a small
bar when pointed at; a caret while an answer streams; a ↓ button back to the newest message
(a dot when something new came); the page stays at the newest message when the box grows or
the window shrinks, unless you scrolled up (it used to slide out of view, and a final answer
longer than what streamed was not followed); the message box is one rounded field, one line
at rest, with Send as the main button; a calm start for a new or private conversation (its
texts had no German); light `--bad` darkened for readable red text. `tests/test_chat_look.py`.
U4 built: a number box (Safety limits) was white with light text in the dark look: every
text-like field now shares one style (`style.css`). In plain words shows each group's tools as
chips in its colour; "1 tool"; a module that is off says "off"; the pairing code is the main
button and Remove a red outline (`button.danger-outline`); the segmented buttons' focus ring
sits inside (their box clipped it); the Reset column fits "Zurücksetzen". German: `i18n.js`
now matches a sentence that wraps in a page's source (ten existing translations on Activity,
Memory and Settings never applied), and the module counts and summary have German.
`tests/test_settings_look.py`.
U5 built: a text box made without a type got no style (white, its text unreadable in the dark
look): the pairing code on Pair, and an automation's condition value; `style.css` now styles
them too, at no weight (`:where`), so a page's own style still wins (the note title). Check
boxes and radio buttons in the accent. Delete in lists (memories, skills, automations) is a
red outline; Import a skill is a real button (the keyboard could not reach the hidden file
box); Export sizes like the buttons; the approval waiting for you has an amber edge; Pair is
a card with the code in a monospaced box and Pair as the main button; Kill uses --danger.
`tests/test_pages_look.py`.
U6 built: notes.css takes its corners (27 of them, nine different sizes) from the shared
radius scale, its lifted shadows from --shadow-2 and its motion from --dur; only the small
marks keep 3 or 4 px. A screenshot diff showed no visible change beyond a pixel at a corner.
A ticked task in the editor was blue, green everywhere else; it is green now too. Checked the
three panes and the editor's toolbar: they hold up. `tests/test_notes_look.py` keeps it so.
Left for later: "mentioned without a link" cuts its snippet inside the highlighted word.
U7 built: measured on emulated phones (Galaxy S8, 360 px; Pixel 7, 412 px; touch): no page
scrolled sideways, but almost every control was too small for a finger (buttons 37 px, icons
30, the Runs / Asks first / Blocked buttons 25, switches 22, Reset 18). On a touch screen
(`pointer: coarse`) every control is now at least 44 px, at no weight (`:where`), so a page can
keep a chip's button smaller on purpose; the mouse look is unchanged. The top bar's links get a
taller place to tap without the bar growing; Settings' switches are bigger with a 44 px tap
area; the message box's four buttons are 36 px wide (four at 44 would squeeze the text), and on
a narrow phone Send is ➤ (Stop ■), its word kept for screen readers. The chat's bar stays one
row. `tests/test_phone_look.py`.

## Blocks

### U1 · Design-system foundation · M
The one change that improves every page at once: define the system, then have pages use it.
- **Add to `style.css`:**
  - A **spacing scale** (`--s-1..--s-6`, e.g. 4/8/12/16/24/32) and use it in the shared rules.
  - A **radius scale** (`--r-1`, `--r-2`, `--r-pill`) and a **type scale** (`--fs-xs..--fs-xl`,
    with line-heights) so headings and small text are consistent.
  - **Elevation** (`--shadow-1`, `--shadow-2`) and **motion** tokens (`--ease`, `--dur-fast`,
    `--dur`, all behind `prefers-reduced-motion`).
  - Refined **state styles** for button/input/pill: a real hover, active and disabled, a soft
    `:active` press, and a filled vs. quiet button variant named once (`.btn`, `.btn-quiet`,
    keeping `.ok`/`.danger`).
  - A small **utility set** (`.stack`, `.cluster`, `.muted`, `.truncate`) to replace repeated
    inline flexbox and spacing.
- **Tune the palette lightly:** keep the identity (dark `#0f1115`, accent `#6ea8fe`); check
  text-on-panel contrast meets WCAG AA in both themes; add a subtle accent hover tint token.
- **Do not** change class names pages already depend on; add, don't rename. Pages keep working
  untouched; later blocks swap their inline values for the tokens.
- **Tests:** a visual check (a representative page screenshotted light and dark, before/after
  reviewed by eye); every existing Playwright page test stays green; confirm the a11y media
  queries still apply (contrast, forced-colors, reduced-motion).

### U2 · The launcher window · M
The first thing the user sees when they start `Starlight.exe`.
- Bring the window's colours, accent and fonts in line with the web tokens (same blue, same
  dark/light feel); tidy the padding and the type sizes via the existing `px()` DPI scaling.
- Make the **wizard** steps clearer (what is happening, what is next), the **progress bar** and
  the **status** states (off/loading/ready/restarting/down/paused) easy to read at a glance, and
  the **what's-new** box calmer.
- Keep it standard-library tkinter; no new packages. Check at the real DPI and in the German
  texts (`tr`). The GUI self-test (`--gui-selftest`) must still pass.

### U3 · The web chat page · L
The daily driver; the most worth polishing.
- **Messages:** clearer you vs. Starlight (alignment, a quiet role label or small avatar mark,
  timestamp on hover), comfortable width and rhythm, and the existing Markdown render (`render.js`)
  sitting well inside a bubble.
- **Streaming:** a tidy typing/cursor affordance while the answer streams; a smooth, unobtrusive
  **scroll-to-bottom** button when the user has scrolled up.
- **Code blocks:** a copy button and language label (reuse the pattern from `mdCodeBlock`).
- **Composer:** auto-growing textarea with a sensible max height, a clear send state, the kill
  switch and mic/photo controls arranged calmly; keep Enter-to-send, Shift+Enter newline.
- **Drawer and side panel:** the conversation list and the right-hand info (logins, secrets,
  saved files, approvals) tidied, with good empty states.
- Keep `role="log"`, `aria-live`, and every control's name. Screenshots light/dark, a short and a
  long conversation. The chat-page tests stay green.

### U4 · Settings page · M
- Consistent card rhythm and section headers; group related settings; make the per-module,
  per-tool **permission controls** (runs on its own / asks / blocked) easy to scan and change.
- Better form controls (the model presets, the safety-limits level + switches, the search box),
  clear **empty states**, and a visible "saved" confirmation.
- No change to what the controls do. German parity. Settings-page tests stay green.

### U5 · Automations, Memory, Activity, Kill, Pair · M
- One consistent card/table/pill style across these; readable tables that scroll inside their
  card on a narrow screen (already the rule — apply it evenly); kind empty states.
- The **Automations** builder (just reworked for several conditions) gets a visual tidy to match.
- **Kill** and **Pair** are small full-screen states; make them calm and unmistakable.
- German parity; each page screenshotted light/dark; tests stay green.

### U6 · Notes page: light consistency pass · S
- The notes page is already polished; only align it with the refreshed tokens (spacing, radius,
  motion), check the three-pane balance and the editor toolbar, and keep every notes test green.

### U7 · Responsive / phone pass · M
- The pages the phone actually uses (chat, settings, memory — notes is PC-only) get a mobile
  pass: the drawer as an off-canvas panel, touch targets at least 44px, the safe-area insets,
  no horizontal scroll, and the composer reachable above the keyboard.
- Test at phone width (e.g. 390px) light and dark; the remote/phone gate and `body.remote`
  rules still hold.

### U8 · Micro-interactions and feedback · M
- A consistent **toast** look and timing; **loading/skeleton** states instead of blank panels;
  smooth transitions that honour `prefers-reduced-motion`; tidy **focus management** when panels
  open and close.
- A small **keyboard-shortcuts overlay** (press `?`) listing the shortcuts already in the chat
  and notes pages, so they are discoverable.

### U9 · Icons and brand · S–M
- Decide on icons: today pages mix emoji (`⤓ 🔗 👁 🕘`) and text. Either keep emoji deliberately
  or introduce one small inline-SVG set for crispness and consistency (no icon-font dependency);
  apply it evenly.
- A consistent logo/wordmark in the header, matching **favicons** (`icon.svg` already exists),
  and the PWA manifest icons; a tidy Kill/Pair screen lock-up.

### U10 · Terminal chat refinement · M
- Give `cli.py` an ANSI colour theme matching the web accent (dark and, where the terminal
  supports it, light); light **Markdown** rendering (headings, bold, `code`, lists, rules);
  a tidy **streaming spinner** and a clear **status line** (model, kill switch, mode); sensible
  word-wrap to the terminal width. No new heavy dependency; the existing `NO_COLOR` and
  non-terminal fallbacks stay.

## Across all blocks
- **Verification per block:** light and dark screenshots (German where text shows), the existing
  Playwright page tests and the launcher GUI self-test stay green, and the accessibility media
  queries (`prefers-contrast`, `forced-colors`, `prefers-reduced-motion`) still apply. A new test
  only where behaviour changes — most of this is visual, guarded by the screenshot review and the
  pages still passing their tests.
- **Checks before pushing** (same as the project): `uv run ruff check .`, `ruff format --check .`,
  `mypy` and `mypy modules` (both platforms), `mypy --strict` on the launcher (both platforms),
  then the full suite under `xvfb-run`.
- **How we work:** branch `Starlight-V2-LM-Studio` (mirrors `main`); commit and push when a block
  is done; no pull request unless asked; GitHub Actions minutes are used up, so don't trigger CI.
- **Risks:** regressing accessibility (guard: the a11y section stays, check the media queries);
  per-page inline CSS drifting again (guard: move shared rules into `style.css` in U1 and keep new
  shared rules there); bloating page weight (guard: vanilla CSS, no libraries); losing German
  parity (guard: add i18n entries with every new string).
</content>
