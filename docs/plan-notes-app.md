# Plan: the notes app built into Starlight (v3, checked)

A memo for whoever continues this work, most likely a future Claude session without the
conversation that produced it. It holds the decisions, what was verified, how we work, and
the plan itself. Keep the **Status** section current as phases land.

## Status

Work goes block by block (section 9). One block is one session and one commit; mark it
**done** here (with the commit) when it is pushed, and move **next** on.

| Block | What | Size | Needs | State |
|---|---|---|---|---|
| A1 | Shared base: safe writing and history | M | – | **done** 2026-11-01 ("Notes plan A1: …") |
| A2 | Every writer through the safe path; undo tools; safety limit | M | A1 | **done** 2026-11-01 ("Notes plan A2: …") |
| A3 | "Changed · Open · Undo" chip in the chat | S | A2 | **done** 2026-11-01 ("Notes plan A3: …") |
| B1 | Editor build (CodeMirror bundle, licences, hash test) | S | – | **done** 2026-11-01 ("Notes plan B1: …") |
| B2 | Parser and index (Markdown, SQLite, full-text search) | M | A1 | **done** 2026-11-01 ("Notes plan B2: …") |
| B3 | Notes API (list, read, save, rename, trash, versions, pictures) | M | A2, B2 | **done** 2026-11-01 ("Notes plan B3: …") |
| B4 | Renderer extension (`render.js`) | M | – | **done** 2026-11-01 ("Notes plan B4: …") |
| B5 | Notes page: layout, list, switcher, reading view, welcome note | M | B3, B4 | **done** 2026-11-01 ("Notes plan B5: …") |
| B6 | Notes page: editing (CodeMirror, toolbar, `/` menu, autosave, conflicts, pictures, history panel) | L | B1, B5 | **done** 2026-11-01 ("Notes plan B6: …") |
| C1 | Links, aliases, embeds, backlinks, rename that fixes links | M | B6 | **done** 2026-11-01 ("Notes plan C1: …") |
| C2 | Tags and properties | M | C1 | **done** 2026-11-01 ("Notes plan C2: …") |
| C3 | The assistant uses the structure; "Ask Starlight" on a note | M | C2 | **done** 2026-11-01 ("Notes plan C3: …") |
| C4 | Templates, daily notes, calendar strip, monthly and yearly reviews | M | B6 | **done** 2026-11-01 ("Notes plan C4: …") |
| D1 | Capture: clipper (with the guard), highlights, inbox | M | C2 | **done** 2026-11-01 ("Notes plan D1: …") |
| D2 | Voice memos | S | D1 | **next** |
| D3 | Tasks in notes: parser, writing back, to-do integration | M | B2 | open |
| D4 | Tasks on the page: boxes, date chips, Tasks view | M | D3, B6 | open |
| E1 | Search operators with highlighted hits | M | B5 | open |
| E2 | Related notes, mentions without a link, OCR text | M | E1 | open |
| E3 | Query language (`query.py`) and `notes.query` | M | B2 | open |
| E4 | Query blocks as live tables | M | E3, B6 | open |
| F1 | Graph data, d3-force bundle, small graph per note | M | C1 | open |
| F2 | Whole-graph view | M | F1 | open |
| F3 | Boards: JSON Canvas files, tools, API | M | A2 | open |
| F4 | Board view on the page | L | F3, B6 | open |
| G1–G6 | Extras (export, Kanban, flashcards, live preview, Mermaid, deep links) | S–M each | see 9 | optional |

Last updated: 2026-11-01 (A1 to D1 done. D1: notes.note now takes (text, title="") so a
note without a title goes to Inbox/Inbox.md; call it with keywords. Clippings are marked by
their `source:` property, which is what makes a later read untrusted.
C4: templates keep their
English file names (Meeting, Daily, Project) in both languages, only their text is German
or English; the language comes from the page, or the PC's locale for the assistant's tools.
C3: "Ask Starlight" fills the chat box and waits
for Send (nothing is sent by itself); requests name the note by its path, which notes.read
now accepts.
C2: properties are edited on the page only in
reading mode (while the editor is open its text holds the note); tag edits use the tags
property, never the text.
C1: a missing note is offered ("Write it"), not
created on click, as the plan first said: a click must not make files by itself. Rename
logic is `starlight/notebook/rename.py` (plan, rename); modules use `ctx.notebook`.
B6: the editor module is
`static/notes-editor.js` (`createEditor(parent, options)` returns text, setText, insertAt,
run(command), focus, destroy); C1's `[[` autocomplete belongs in its `autocompletion`
override next to `slashMenu`. The editor edits the whole file, properties block included
(C2's properties panel can change them without the editor). "Template" in the "/" menu
is a placeholder for C4.
B5: `static/notes.html` (reading only); the page
keeps `state.current` (the note as `/api/note` returned it, with `body`, `stamp`,
`headings`); B6 adds an Edit/Read toggle next to "☆ Pin" in `.crumbs .actions` and should
reuse `openNote`/`drawInfo`. Tasks and Graph in the sidebar are placeholders (D4, F1/F2).
Tests drive the page in Chromium with `/api/**` answered by the real app
(`tests/test_notes_page.py`, `notes_page()`).
B4: `mdHooks` in markdown.js, filled by
render.js; links go to `/notes?open=<name>&heading=<h>` and tags to `/notes?tag=<t>`, so
B5's page must read those two parameters. A bare picture name is looked up in
`Notes/Attachments`.
B3: the API is in `starlight/web/notes_api.py`
(endpoint list in its docstring and in docs/architecture.md); a note is `/api/note?path=…`
(a query parameter, not `/api/notes/{path}`), pictures are served from
`/api/notes/file/{path}` (B4 links `![[picture]]` there).
B2: `Kernel.notebook_index` (made on
first use, root = the Starlight folder) is the Index; its API: `refresh`, `touch`, `notes`,
`note`, `properties`, `search`, `tags`, `with_tag`, `tasks`, `links`, `backlinks`,
`resolve`, `resolver`. `History.listeners` / `History.changed(path)` is the change feed
every writer already reaches; B3 should use `write_note(..., expected=stamp)` and nothing
else to write. Natural dates in tasks (`due: tomorrow`) are still raw text (D3).
B1: the build lives in `bundles/editor/`,
not `tools/editor/` (`/tools/` is gitignored: the launcher downloads there); the bundle is
625 KB (212 KB gzipped) because `entry.js` re-exports whole packages, so later blocks need
no rebuild; the page imports it from `/vendor/editor.js`. F1's d3-force bundle should go
to `bundles/graph/` the same way.
A3: the undo endpoint is `POST /api/changes/{id}/undo` with a server-given id (not
`/api/notes/undo` with a path): B3 should reuse it. "Open" on the chip comes with B5 (a
link to `/notes?open=…`); until then the chip shows the path on hover.
A2: notes, journal, recipes, files.save and memory keep versions; tools
files.versions/changes/restore, notes.trash/restore_deleted, memory.versions/undo; results
carry `changed: {path, title, version, new}` for A3;
files keeps versions of text up to 10 MB, not 1 MB as first planned).
Note for later blocks: history lives in its own database `data/history/history.sqlite`
(the only copy of the versions); the B2 index stays a separate cache in
`data/notebook/index.sqlite`. The kernel's History is `Kernel.file_history`
(`Kernel.history` is the conversation); modules use `ctx.history`.

## For my future self

**Where this came from.** The user (Paul, German, Windows 11, non-technical, uses the web
chat and Starlight.exe) asked which Obsidian features Starlight could "steal". Obsidian
itself will **not** be used: everything ships inside Starlight. The plan went through three
rounds; this is the final, checked version.

**The user's decisions (do not reopen them):**
1. Editor: a **shipped editor library**, CodeMirror 6 (built once, version-pinned, in the repo).
2. Tasks: **plain words** (`due:`, `priority:`, `repeat:`, `start:`, `done:`), not Obsidian's emojis.
3. Phone: **ignore it for now**. All notes interfaces are PC only (like settings), no phone layout.
4. Graph view and board view: **build both**.

**Verified on 2026-11-01** (re-check anything that matters before relying on it):
- npm is reachable from the cloud environment. A trial bundle (`esbuild --bundle --minify
  --format=esm`) of `@codemirror/view` 6.43, `state`, `commands`, `lang-markdown`, `language`,
  `autocomplete`, `search` and `@lezer/highlight` came to **569 KB (194 KB gzipped)**.
  `d3-force` 3.0.0 (ISC licence) bundles to **14 KB**. esbuild 0.28.
- Python's SQLite here has **FTS5** (SQLite 3.53.1). Not yet checked on Windows Python: keep a
  fallback (plain scan) and test it.
- Today's note writes are **not atomic and keep no versions**: `modules/notes/module.py`
  (`write_text` in `note`, `open("a")` in `append`), `modules/journal/module.py` (`open("a")`,
  weekly review `write_text`), `modules/recipes/module.py` (`write_text`). Memory uses
  `write_atomic` (`starlight/memory/store.py`) but keeps no versions.
- `starlight/web/static/markdown.js` is Starlight's own renderer: safe (only `textContent`,
  links only http(s)/mailto), already does headings, lists, tables, code highlighting and
  formulas. Extend it (`render.js`), do not replace it.
- The automations' condition checker (`matches()` in `modules/automations/module.py`) handles
  **one** condition (`field op value`), no and/or.
- The injection guard marks a request as untrusted **only by tool name**
  (`starlight/core/dispatcher.py`, `policy.is_untrusted_source(name)` after a tool result).
- The knowledge base (`modules/docs`) already indexes the Notes folder: table `parts (path,
  page, text, vector)` in `data/modules/docs/index.sqlite`.
- Memory files already have YAML properties (`topic`, `source`, `date`, `confidence`, `tags`).
- The to-do module keeps tasks in `data/modules/todo/todo.json`; priorities are low, normal and high.
- Web pages share `style.css` (colour tokens for dark and light) and `common.js` (`el`, `api`,
  `toast`); German texts come from `i18n.js` (exact-text map plus patterns; texts that are
  already German are marked `translate="no"`).

**How we work in this repo (from the whole project so far):**
- Branch `Starlight-V2-LM-Studio`; commit and push after each phase or milestone. No pull
  request unless the user asks. GitHub Actions minutes are used up: don't trigger CI runs.
- Before pushing: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
  `uv run mypy --platform win32`, `uv run mypy modules` (both platforms), `uv run mypy
  --strict launcher/starlight_launcher.py launcher/launcher_gui.py` (both platforms), and the full
  suite: `STARLIGHT_CHROMIUM=/opt/pw-browsers/chromium-1194/chrome-linux/chrome timeout 1500
  xvfb-run -a -s "-screen 0 1366x768x24" uv run pytest -p no:cacheprovider -q`.
- Every behaviour change gets a test, and new tests are run against the code before the change
  (a git worktree of the previous commit) to prove they fail on the old behaviour.
- Pages: German translations for every new text, accessibility (labels), light and dark, and a
  screenshot review (Playwright, see the Safety card test in `tests/test_safety_limits.py`).
- Safety first: anything that changes the user's files keeps an earlier version; changes to
  several items ask first; outside content counts as untrusted; settings change only at the PC.
- Write docs as plain, short sentences (README section, `docs/architecture.md`).

## 1. Goal and ground rules
- **Goal:** a notes app inside Starlight (a Notes page in the web chat, on the PC) with links,
  tags, templates, tasks, search, history, a graph and boards. The assistant works with the
  same structure (links, tags, properties, tasks feed its answers and actions).
- **Plain Markdown** in `Documents\Starlight` (Notes, Journal, Recipes, Clippings, Inbox),
  readable in any editor.
- **Shipped code allowed** when built locally, version-pinned, licensed and checked by a test
  (CodeMirror, Lezer, d3-force). Nothing is loaded from the internet at runtime.
- **Safety:** a version before every change; changes to several notes ask first; outside
  content counts as untrusted; one-click undo in the chat.
- **One shared base** (`starlight/notebook/`) for notes, journal, recipes, memory, to-do, page.
- **Each phase ships on its own:** tools and page together, tests (also against the old code),
  screenshots (light and dark, German), documentation, all checks, commit and push.

## 2. Syntax

| What | Syntax |
|---|---|
| Links | `[[Note]]`, `[[Note\|text]]`, `[[Note#Heading]]`, `[[Note#^id]]` |
| Embeds and pictures | `![[Note]]`, `![[photo.png]]` |
| Tags | `#tag`, `#work/projects`, `tags:` in properties |
| Properties | YAML at the top (`--- … ---`): `aliases`, `tags`, `source`, your own keys |
| Tasks | `- [ ] Call Tom due: 2026-11-05 priority: high repeat: every month start: …`; done: `- [x] … done: 2026-11-04`. German keys are read too (`fällig:`, `priorität: hoch/normal/niedrig`, `wiederholen:`, `erledigt:`). Starlight writes English keys. Typed natural dates (`due: tomorrow`, `fällig: Freitag`) become ISO dates on save. |
| Templates | `{{title}}`, `{{date}}`, `{{time}}`, `{{date:YYYY-MM-DD}}`, plus `{{events}}`, `{{todo}}`, `{{weather}}`, `{{yesterday}}` |
| Callouts | `> [!note] Title` (note, tip, warning, quote, todo) |
| Queries | a ` ```query ` block with `where:`, `sort:`, `fields:` lines, e.g. `where: status is open and tag is work` |
| Boards | `.canvas` files in the open JSON Canvas format (jsoncanvas.org, MIT) |
| Flashcards | `Question::Answer` |

## 3. Architecture: `starlight/notebook/`
(Not "vault": `starlight/core/vault.py` is already the password store.)

| Part | What it does |
|---|---|
| `markdown.py` | Pure functions: properties, headings/sections, links, tags, tasks, query blocks; ignores code blocks and URLs. Edits only the lines concerned (keeps comments and key order). Replaces memory's own parser (`FRONT` in `starlight/memory/store.py`). |
| `index.py` | `data/notebook/index.sqlite`: tables files, links, tags, properties, tasks, plus an FTS5 table. Re-reads only changed files (stat walk at most every 5 s, immediately after Starlight's own writes; no file-watcher dependency). Caps the file count (20,000). Fallback search without FTS5. |
| `links.py` | Resolves a link: file name or alias, case-insensitive, same folder first, then shortest unique path. Backlinks from the index. |
| `history.py` | Gzip copies in their own database `data/history/history.sqlite` (not the index: this is the only copy): list, compare (difflib), restore, trash. Keeps 20 versions or 90 days per file and 200 MB in all. While typing on the page: at most one version per 5 minutes, plus one on leaving the note. |
| `write.py` | `write_note(path, text, reason, expected_stamp)`: version first, refuse if the file changed since it was read, atomic write, index update. Everything writes through it. |
| `query.py` | One safe condition language (and, or, not, comparisons, `has`, dates, numbers), never evaluated as code, translated to SQL on the index. Automations can switch to it later (stay backward compatible with `matches()`). |
| `templates.py`, `boards.py` | Templates; reading and changing `.canvas` boards. |
| `static/render.js` | Extends `markdown.js`: links, embeds, pictures, callouts, task boxes, tags, query tables, hover previews. Used by chat answers and the Notes page alike. |
| `static/vendor/` | `editor.js` (CodeMirror bundle), `graph.js` (d3-force), licence texts. Built by `tools/editor/` (package.json with pinned versions, lockfile, one build command); a test checks the shipped files' hashes against the build. |

## 4. Look and feel
- Same style as the other pages: `style.css` colours (dark and light), the same header and buttons.
- Layout:

```
┌ Header: Chat · Notes · Memory · Activity · Automations · Settings ───────────┐
│ ┌ Sidebar ─────┐ ┌ Note (reading column, about 760 px) ┐ ┌ Info panel ────┐  │
│ │ Search        │ │ Title                               │ │ Outline        │  │
│ │ Today · Inbox │ │ Properties as chips                 │ │ Backlinks      │  │
│ │ Tasks · Graph │ │ Text: easy-to-read type,            │ │ Properties     │  │
│ │ Pinned        │ │ callouts, task boxes, pictures      │ │ Related notes  │  │
│ │ Folders, Tags │ │ [Edit] [Ask Starlight] [History]    │ │ (folds away)   │  │
│ └──────────────┘ └─────────────────────────────────────┘ └────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

- **Reading view first**, Edit on demand. In editing, CodeMirror shows *styled* Markdown
  (larger headings, bold shown bold, links coloured, markers dimmed, task lines with boxes).
- Callouts with a coloured edge and icon; tags as pills; properties as chips; due dates as
  chips (red overdue, amber today); backlinks as cards with the line that links; hover previews.
- No Markdown knowledge needed: toolbar, a `/` menu (heading, task, table, link, picture,
  template, date), pictures by drag and drop or paste, saving on its own with a "Saved" sign.
- Keyboard: Ctrl+K quick switcher, Ctrl+N new note, Ctrl+E edit/read, Ctrl+Enter tick a task.
- First visit: a "Willkommen" note with examples (itself an ordinary note).
- Screenshot review at every phase: light and dark, German and English.
- "Ask Starlight" on a note: summarise, find tasks, suggest links and tags, translate.

## 5. Phases

**P1 · History and the safe writing path · M**
- You see: "undo the last change to Shopping", versions of a note, notes back from the trash.
  After each note change by the assistant, the chat shows a chip **Changed: Shopping · Open · Undo**.
- How: `history.py`, `write.py`; notes, journal, recipes, `files.save` (text in the Starlight
  folder) and memory switched over. Tools `files.versions`, `files.restore`, `memory.versions`,
  `memory.undo`. New safety limits (Settings → Safety limits, `starlight/core/safety.py`):
  "Keep earlier versions" and how many.
- Done when: no write path skips history; trimming by count, age and size; conflicts refused;
  two changes in the same second both kept; the chip and its Undo work (browser test).

**P2 · The Notes page · L**
- You see: the layout from section 4; list, quick switcher, pinned notes, reading view;
  CodeMirror editor with styled Markdown, toolbar and `/` menu; saving on its own; conflict
  dialog for two tabs; new, rename, delete (trash); history panel (differences, restore);
  welcome note; pictures (in `Notes/Attachments/`, shown inline); `[[Note]]` in chat answers opens the page.
- How: `/api/notes/*`, PC only (`local_only`). Pictures: allowed image types only, size cap,
  served with nosniff (SVG only as a download). The editor build in `tools/editor/` + hash test.
- Done when: API and browser tests for every action and the conflict dialog; screenshots.

**P3 · Structure: links, embeds, backlinks, tags, properties · M**
- You see: `[[` and `#` suggest names; clickable links (a missing note is created on click);
  embeds; backlinks panel; properties panel (edit fields directly); tags sidebar. In the chat:
  "what links to X?", "show #work notes", "set status of X to done". Renames fix every link.
- How: `links.py`, index tables; tools `notes.links`, `backlinks`, `rename` (asks first when
  other notes change), `tag`, `untag`, `by_tag`, `tags`, `set_property`, `properties`. Memory
  recall follows links (`starlight/memory/service.py`): one step, at most 5, size budget.
- Done when: syntax samples, resolution rules, renames, embed loops, context cap, panels tested.

**P4 · Templates, daily notes, calendar strip · S–M**
- You see: "New from template" (Meeting, Daily, Project; German and English); a Today button
  for the day's journal note; a calendar strip with days that have entries; monthly and yearly
  reviews; "start a meeting note for the Müller call" in the chat.
- How: `templates.py`; tools `notes.from_template`, `templates`, `journal.month`,
  `journal.year`; automation templates (monthly review, weekly plan).
- Done when: variables, date formats, missing modules, the daily template are tested.

**P5 · Capture: clipper, highlights, inbox, voice memo · S–M**
- You see: "save this page as a note" (summary, `source:` link, in `Clippings/`); an Inbox view
  and "sort my inbox" (proposes, asks before moving); a 🎤 voice-note button on the page (text
  only); highlights as quote callouts.
- How: tools `notes.clip`, `highlight`, `inbox`, `sort_inbox`, `voice.memo`. Guard:
  `notes.clip` counts as outside content, and so does reading any note with a `source:`
  property (dispatcher: also taint when a tool result says `untrusted`).
- Done when: mocked web answers; the guard after a clip and after re-reading it; inbox sorting
  asks first; fake speech-to-text.

**P6 · Tasks inside notes · M**
- You see: tickable task boxes with date chips; a Tasks view (overdue, today, upcoming, no
  date); "what's due", reminders and the briefing include note tasks; "done: call Tom" ticks it.
- How: plain-word parser, natural dates turned into ISO on save, the index's task table; the
  to-do module includes note tasks (its own stay in `todo.json`); task ID from note + text;
  conflicts refused; a version before ticking.
- Done when: format variants, German keys, write-back, conflicts, reminders tested.

**P7 · Search and finding · S–M**
- You see: `tag:#work`, `path:Clippings`, `"exact phrase"`, `-word` with highlighted hits; a
  related-notes panel; "mentioned without a link" with one-click linking; text in pictures and
  scanned PDFs findable (when Tesseract is installed).
- How: FTS5 plus columns; a new `docs.similar(path)` tool on the existing vectors, falling back
  to shared tags and links; OCR text in the knowledge base (switchable, with limits).
- Done when: parser, related notes with and without the embedder, OCR with a fake backend tested.

**P8 · Queries · M**
- You see: ` ```query ` blocks become live tables (click a column to sort); "make me a table of
  my open work notes" makes the assistant write the block.
- How: `query.py` translated to SQL on the index, with a time limit.
- Done when: the language, sorting, missing properties, large folders tested.

**P9 · Graph view · M–L**
- You see: a small graph around each note in the info panel; the whole graph (zoom, drag,
  filter by tag or folder, colour by folder, size by links, labels when zoomed in, click to
  open; memories optionally shown).
- How: `/api/notes/graph` from the index; d3-force; SVG up to about 300 points, canvas beyond.
- Done when: graph data (filters, caps), rendering and clicks in the browser, screenshots.

**P10 · Board view · M–L**
- You see: boards with text cards, note cards (showing the note), pictures, links, groups and
  arrows; drag, resize, zoom, snap grid, saving on its own; "what's on my project board?",
  "add the article to the board".
- How: `.canvas` (JSON Canvas), `boards.py`, own rendering (HTML cards + SVG arrows, text only),
  versions like every file, tools `boards.read`, `boards.add_card`.
- Done when: format read/write, versions, browser tests for dragging and connecting, screenshots.

**P11 · Extras, each optional**
Export a note as HTML or PDF (from the existing conversation export, `export_html` in
`starlight/web/app.py`); Kanban for tasks; spaced repetition (`Question::Answer`); live preview
that hides Markdown markers; Mermaid diagrams (library about 2–3 MB: only if wanted); deep links
(`starlight://note/…`).

## 6. Order
A (P1) → B (P2) → C (P3, P4) → D (P5, P6) → E (P7, P8) → F (P9, P10) → G (P11). The order
avoids rework: base and writing path first, then the renderer, then the page frame, then the
features that slot into it.

## 7. Across all phases
- **Permissions:** single-note changes by the assistant run on their own (history and undo
  protect them); changes to several notes ask first; the user's own edits on the page never ask;
  outside content is untrusted; all notes interfaces are PC only.
- **Performance:** index re-reads only changed files; search and queries in SQLite; editor and
  graph load only on the Notes page; caps on files, note size (1 MB, as now), pictures and graph
  points; time-limit tests with large folders.
- **Quality:** a round-trip test (tools change only what they mean to), German texts,
  accessibility, screenshots at every phase.
- **Licences:** CodeMirror and Lezer (MIT), d3-force (ISC): licence texts in `static/vendor/`
  and a "third-party code" note in the README.

## 8. Risks

| Risk | Handling |
|---|---|
| Two tabs or Notepad change a note at once | Version stamp on every save; conflict dialog |
| The assistant changes the wrong things | Versions, the Undo chip, ask-first for several notes, the kill switch |
| Hidden instructions in clipped pages | Clipping and re-reading count as untrusted; the guard applies |
| The editor library ages | Pinned versions, one build command, hash test |
| Windows Python without FTS5 | Fallback search; check on the Windows build |
| A large graph gets slow | Caps, canvas beyond 300 points, filters |
| History and index grow | Caps by count, age and size; cleanup |

## 9. Blocks

Each block: goal, what it needs first, files, steps, tests, and when it is done. Every block
also: German texts for anything new on a page, docs (README / `docs/architecture.md`) where
users or developers notice the change, all checks from "How we work", new tests run against
the previous commit, a screenshot review for page work (light and dark, German), the Status
table updated, commit and push.

### Milestone A: safety net (P1)

**A1 · Shared base: safe writing and history · M** · needs: –
- Goal: one way to write the user's notes that never loses a version.
- Files: new `starlight/notebook/__init__.py`, `db.py` (opening SQLite files, schema versions
  by `PRAGMA user_version`), `write.py`, `history.py`; new `tests/test_notebook_history.py`.
  History has its own database, `data/history/history.sqlite`: it holds the only copy of the
  versions, so it must not share a file with the index, which is a cache.
- Steps:
  1. `db.py`: open/create the database, a `schema` table, small migrations.
  2. `history.py`: `save(path, reason)` keeps a gzip copy as a row (path, time, size, reason,
     deleted flag, content hash) in `data/history/history.sqlite`; `versions(path)`,
     `read(version)`, `diff(a, b)` (difflib, unified), `restore(path, version)` (saves the
     current one first), `trash(path)` and `restore_deleted`.
  3. Trimming: 20 versions or 90 days per file, 200 MB in all (settings in a new `notebook:`
     section of `StarlightConfig`); run after each save, cheaply.
  4. `write.py`: `stamp(path)` (mtime + size), `write_note(path, text, reason,
     expected_stamp=None)`: refuse with a clear error if the file changed since `expected_stamp`,
     save a version, write atomically (`core.fsutil.write_atomic`), return the new stamp and
     the version id. `append_note()` likewise.
- Tests: versions, restore, diff, trash and back, trimming by count/age/size, two writes in the
  same second, conflict refused, a crash between version and write leaves the old file intact.
- Done when: the base is complete and tested; nothing uses it yet.

**A2 · Every writer through the safe path; undo tools; safety limit · M** · needs: A1
- Goal: no change Starlight makes to notes, journal, recipes, saved text files or memory can be lost.
- Files: `modules/notes/module.py`, `modules/journal/module.py`, `modules/recipes/module.py`,
  `modules/files/module.py`, `starlight/memory/store.py`, `starlight/memory/service.py`,
  `starlight/core/safety.py` (+ German texts), `starlight/core/config.py`; tests.
- Steps:
  1. Replace the direct `write_text` / `open("a")` calls in notes (note, append, delete →
     trash), journal (write, weekly review), recipes with `write_note` / `append_note`.
  2. `files.save` / append: through `write_note` for text files up to 1 MB in the Starlight
     folder (others as before).
  3. `MemoryStore` write and delete through history (memory lives in Starlight's own folder;
     its versions go to the same history).
  4. New tools: `files.versions(path)`, `files.restore(path, version)` (notes and journal are
     files in the Starlight folder), `notes.restore_deleted(title)`, `memory.versions(id)`,
     `memory.undo(id)`.
  5. Write tools return `changed: {path, version}` (A3 shows it).
  6. Safety limits (`safety.py`): switch "Keep earlier versions of files Starlight changes"
     (on at every level) and a number "versions to keep" (5–100); German texts.
- Tests: each writer saves a version (and the test fails on the previous commit); restore via
  the tools; a deleted note comes back; memory undo; the switch and the number work.
- Done when: every write path is covered (a test lists the writers and checks each).

**A3 · "Changed · Open · Undo" chip in the chat · S** · needs: A2
- Goal: after the assistant changes a note, one click undoes it.
- Files: `starlight/web/app.py` (the tool sink already sees tool results: forward `changed`),
  `starlight/web/static/chat.html`, `i18n.js`; `starlight/tui.py` (a line "Changed X (undo:
  /undo)" and a `/undo` command); tests.
- Steps: forward `changed` to the page as `{"type": "changed", "path", "version", "title"}`;
  the page shows a chip under the answer: **Changed: Shopping · Open · Undo**; Undo calls
  `POST /api/notes/undo` (PC only) → `history.restore`, the chip turns into "Undone"; Open
  links to the Notes page once it exists (until then: the file path).
- Tests: browser test (chip appears, Undo restores the file), API test (PC only), terminal `/undo`.
- Done when: chip, undo and terminal line work; screenshots light and dark.

### Milestone B: the app (P2)

**B1 · Editor build · S** · needs: –
- Goal: the shipped CodeMirror bundle, reproducible and checked.
- Files: new `tools/editor/package.json` (pinned versions), `package-lock.json`, `entry.js`,
  `build.sh` / `build.ps1`; `starlight/web/static/vendor/editor.js`, `vendor/LICENSES.txt`
  (CodeMirror, Lezer: MIT); `README.md` ("Third-party code"); test.
- Steps: entry exports what the page needs (view, state, commands with history, lang-markdown,
  language/highlighting, autocomplete, search); esbuild `--bundle --minify --format=esm`;
  write `vendor/editor.sha256`; the test recomputes the hash of the shipped file.
- Tests: the hash matches; the bundle loads in Chromium and creates an editor.
- Done when: about 570 KB file shipped, licences included, rebuild documented in the folder's README.

**B2 · Parser and index · M** · needs: A1
- Goal: fast, correct knowledge of every note's title, aliases, tags, properties, links and tasks.
- Files: new `starlight/notebook/markdown.py`, `index.py`, `links.py`; `starlight/memory/store.py`
  (use `markdown.py` for properties); tests `test_notebook_parser.py`, `test_notebook_index.py`.
- Steps:
  1. `markdown.py`: split properties/text; headings and sections; links (`[[…]]`, `|`, `#`,
     `#^`, embeds `![[…]]`); tags (inline and `tags:`); tasks (plain words, section 2); query
     blocks; all ignore code blocks and URLs. Property edits change only their lines.
  2. `index.py`: tables files, links, tags, properties, tasks, and an FTS5 table (fallback: a
     LIKE scan when FTS5 is missing, chosen at start and tested both ways); `refresh()` walks
     the vault (Starlight folder without `code/`) at most every 5 s and re-reads only changed
     files; `write_note` updates the index directly; caps (20,000 files, 1 MB per note).
  3. `links.py`: resolve a link (name or alias, case-insensitive, same folder first, shortest
     unique path); backlinks from the index.
- Tests: a sample vault with every syntax (including tricky ones: links in code, `#` in URLs,
  headings vs tags), round-trip (property edits keep everything else), incremental refresh,
  deleted files leave the index, FTS5 and fallback, a large-folder time limit.
- Done when: parser and index complete; memory reads its properties through the new parser.

**B3 · Notes API · M** · needs: A2, B2
- Goal: everything the page needs, safe and PC only.
- Files: `starlight/web/app.py` (or a new `starlight/web/notes_api.py` included by it); tests.
- Steps: `GET /api/notes` (folders Notes, Journal, Recipes, Clippings, Inbox; titles, dates,
  pinned) · `GET /api/notes/{path}` (text, stamp, properties, links) · `PUT` (save with stamp →
  409 with both texts on conflict) · `POST` (new) · rename (plain, links in C1) · `DELETE`
  (trash) · versions, diff, restore · `POST /api/notes/undo` (from A3) · pictures: upload
  (PNG, JPEG, GIF, WebP; 10 MB) to `Notes/Attachments/`, served with nosniff, SVG only as a
  download · pinned notes (a small JSON in `data/notebook/`). All `local_only`; paths stay
  inside the vault (`PathGuard`).
- Tests: every endpoint, conflict, path tricks (`..`, absolute, Starlight's own folder),
  phone refused, picture types and size.
- Done when: the API is complete and documented in `docs/architecture.md`.

**B4 · Renderer extension · M** · needs: –
- Goal: notes and chat answers show links, embeds, pictures, callouts, task boxes and tags, safely.
- Files: new `starlight/web/static/render.js`; small hooks in `markdown.js`; `style.css`
  (callouts, tag pills, task boxes, chips); `chat.html` (load it); tests.
- Steps: hook into `mdInline`/`mdBlocks` for `[[links]]` (→ `/notes?open=…`, hover preview
  later), `![[picture]]` (only from `/api/notes/file/...`), callouts (`> [!type] Title`, five
  types with icons and colours), task lines (boxes; read-only in chat), `#tags` (pills);
  everything stays `textContent`.
- Tests: browser test with a sample text (each element appears; script and `javascript:`
  links stay inert), chat answers still render as before (existing tests).
- Done when: chat answers with `[[Note]]` show links; screenshots light and dark.

**B5 · Notes page: layout, list, switcher, reading view, welcome note · M** · needs: B3, B4
- Goal: open, find and read notes in a page that fits Starlight's look.
- Files: new `starlight/web/static/notes.html`; `app.py` (route `/notes`, PC only); the
  header nav in every page (`chat.html`, `memory.html`, `activity.html`, `automations.html`,
  `settings.html`); `i18n.js`; tests.
- Steps: three areas (sidebar: search box, Today, Inbox, Tasks, Graph placeholders, Pinned,
  folders; middle: reading column ~760 px; info panel: Outline, Properties, folds away under
  1100 px); quick switcher (Ctrl+K, fuzzy on titles and aliases); reading view through
  `render.js`; empty states; the "Willkommen" note created on the first visit (German or
  English by page language); `/notes?open=Title` opens a note.
- Tests: browser (list, switcher, open via link, welcome note once), phone refused, screenshots.
- Done when: reading works end to end and looks like the other pages.

**B6 · Notes page: editing · L** · needs: B1, B5
- Goal: comfortable editing without Markdown knowledge.
- Files: `notes.html`, a new `static/notes-editor.js` (our code on top of the bundle),
  `style.css`, `i18n.js`; tests.
- Steps: Edit/Read toggle (Ctrl+E); CodeMirror with styled Markdown (heading sizes, bold,
  links coloured, markers dimmed) in Starlight's colours (dark and light); toolbar (bold,
  italic, heading, list, task, link, picture); `/` menu (heading, task, table, link, picture,
  template placeholder, today's date); autosave after 1 s idle with a "Saved" sign (history
  keeps at most one version per 5 minutes and one when leaving); conflict dialog (yours /
  theirs / both); new, rename, delete (trash) buttons; pictures by paste and drag & drop;
  history panel (list, differences, restore); Ctrl+N, Ctrl+S.
- Tests: browser (type, autosave, reload shows it, conflict dialog, picture paste, `/` menu,
  history restore), screenshots light/dark, German.
- Done when: editing a note is as easy as in a simple editor, and nothing is ever lost.

### Milestone C: smarter notes (P3, P4)

**C1 · Links, aliases, embeds, backlinks, rename that fixes links · M** · needs: B6
- Goal: notes connect, both on the page and for the assistant.
- Files: `modules/notes/module.py`, `notes-editor.js`, `notes.html`, `render.js`, `app.py`; tests.
- Steps: `[[` autocomplete (titles and aliases); clickable links, a missing note is created on
  click; embeds (`![[Note]]`, a section with `#Heading`; loops stopped); backlinks panel
  (cards with the linking line); hover previews; rename rewrites links in every note (a version
  for each; the assistant's `notes.rename` asks first when other notes change); tools
  `notes.links`, `notes.backlinks`; `notes.read` returns links and backlinks; `_find` uses aliases.
- Tests: resolution cases, rename rewrites (and saves versions), embed loops, autocomplete and
  backlinks in the browser.

**C2 · Tags and properties · M** · needs: C1
- Goal: notes can be sorted and filtered.
- Files: notes module, `notes.html`, `notes-editor.js`; tests.
- Steps: `#` autocomplete (existing tags) and property-name autocomplete; properties panel
  (add, change, remove; types text, number, date, list, yes/no; edits only those lines); tags
  sidebar (nested, counts; click filters); tools `notes.tag`, `untag`, `by_tag`, `tags`,
  `set_property`, `properties`; memory tags searchable (`memory` tools).
- Tests: property edits keep the rest of the file, nested tags, tags in code ignored, panels.

**C3 · The assistant uses the structure; "Ask Starlight" on a note · M** · needs: C2
- Goal: links, tags and properties make answers better.
- Files: `starlight/memory/service.py` (recall follows links: one step, at most 5, a size
  budget), notes module (`notes.read` returns properties, links, backlinks compactly),
  `notes.html` ("Ask Starlight": summarise, find tasks, suggest links and tags, translate →
  opens the chat with the note as context); tests.
- Tests: linked memories and notes reach the context (and the cap holds), the button opens the
  chat with the right request.

**C4 · Templates, daily notes, calendar strip, reviews · M** · needs: B6
- Goal: notes start from good layouts; the journal is a daily habit.
- Files: new `starlight/notebook/templates.py`; shipped templates (Meeting, Daily, Project; in
  German and English) copied to `Starlight/Templates/` on first use; notes and journal modules;
  `notes.html` ("New from template", Today button, calendar strip); automations templates; tests.
- Steps: variables (`{{title}}`, `{{date}}`, `{{time}}`, `{{date:YYYY-MM-DD}}`, `{{events}}`,
  `{{todo}}`, `{{weather}}`, `{{yesterday}}`; Starlight's own filled through module calls,
  empty when a module is off); tools `notes.from_template`, `notes.templates`,
  `journal.month`, `journal.year`; a new journal day uses `Daily.md`; automation templates
  "monthly review" and "weekly plan".
- Tests: variables and formats, missing modules, the daily template, calendar strip in the browser.

### Milestone D: everyday use (P5, P6)

**D1 · Capture: clipper with the guard, highlights, inbox · M** · needs: C2
- Goal: get things into notes quickly, safely.
- Files: notes module; `starlight/core/dispatcher.py` (also mark a request as untrusted when
  a tool result says `untrusted: true`); `config/starlight.yaml` (`notes.clip` under
  `untrusted_sources`); `notes.html` (Inbox view); tests.
- Steps: `notes.clip(url)` (polite web access; title, short summary by the model, `source:`,
  `date`, `tags: [clipped]`, in `Clippings/`); reading a note with `source:` returns
  `untrusted: true`; `notes.highlight(note, text, source)` (quote callout); "note: …" without a
  title → `Inbox.md`; `notes.sort_inbox` proposes moves and asks before doing them.
- Tests: mocked web answers, the guard up after a clip and after re-reading the clip (and the
  re-read test fails on the previous commit), inbox sorting asks first.

**D2 · Voice memos · S** · needs: D1
- Goal: speak a thought, find it as text.
- Files: `modules/voice/module.py` (`voice.memo(seconds)`), `notes.html` (🎤 button: records
  in the browser, uploads, transcribes, appends to the Inbox or today's journal; only the text
  is kept); tests.
- Tests: fake speech-to-text; the button flow in the browser (with a fake microphone stream).

**D3 · Tasks in notes: parser, writing back, to-do integration · M** · needs: B2
- Goal: a task anywhere in a note counts like a to-do.
- Files: `starlight/notebook/markdown.py` (task details), `index.py` (task table),
  `modules/todo/module.py`; tests.
- Steps: plain-word keys (`due`, `start`, `priority`, `repeat`, `done`; German `fällig`,
  `priorität: hoch/normal/niedrig`, `wiederholen`, `erledigt`); natural dates (`tomorrow`,
  `Freitag`) turned into ISO dates when Starlight or the page saves (`core.schedule.parse_when`);
  `todo.list_tasks`, `todo.due`, `todo.done`, `todo.reopen` include note tasks (marked with
  their note); ID from note path + task text; writing back saves a version and refuses if the
  line changed; reminders and the briefing get them through `todo.due` (no change needed there).
- Tests: format variants, German keys, natural dates, ticking writes back, conflicts, duplicate
  texts, the briefing shows a note task.

**D4 · Tasks on the page · M** · needs: D3, B6
- Goal: see and tick tasks where they are.
- Files: `render.js` (boxes clickable on the Notes page), `notes-editor.js` (Ctrl+Enter ticks
  the line), `notes.html` (Tasks view: overdue, today, upcoming, no date; date chips red/amber); tests.
- Tests: ticking in reading view and editor writes `done:`; the Tasks view groups correctly;
  screenshots.

### Milestone E: finding (P7, P8)

**E1 · Search operators with highlighted hits · M** · needs: B5
- Files: new `starlight/notebook/search.py` (parses `tag:`, `path:`, `"phrase"`, `-word`,
  plain words → FTS5 + columns; fallback without FTS5); notes module (`notes.find` uses it);
  `notes.html` (results with highlighted hits); tests.
- Tests: the parser, ranking, both search paths, the page.

**E2 · Related notes, mentions without a link, OCR text · M** · needs: E1
- Files: `modules/docs/module.py` (new tool `docs.similar(path)` over the existing vectors;
  optional OCR text of pictures and scanned PDFs when Tesseract is there, switchable, with
  limits), notes module (`notes.related`, falling back to shared tags and links;
  `notes.mentions`), `notes.html` (Related panel, "mentioned without a link" with one-click
  linking); tests.
- Tests: related with and without the embedder, mentions and the one-click link (saves a
  version), OCR with a fake backend.

**E3 · Query language and `notes.query` · M** · needs: B2
- Files: new `starlight/notebook/query.py` (and, or, not, `is`, `is not`, `<`, `>`, `has`,
  dates and numbers; parsed into a tree, translated to parameterised SQL; never evaluated as
  code; time limit); notes module (`notes.query(where, sort, fields, limit)`); tests.
- Tests: the grammar (including wrong input with clear errors), SQL injection attempts stay
  data, missing properties, large folders.

**E4 · Query blocks as live tables · M** · needs: E3, B6
- Files: `render.js` (` ```query ` → a table through `GET /api/notes/query`), `notes.html`
  (click a column to sort), notes module docstring so the assistant writes blocks when asked;
  later and separately: automations may use `query.py` conditions (backward compatible).
- Tests: the block renders and refreshes, sorting, errors shown kindly, screenshots.

### Milestone F: overview (P9, P10)

**F1 · Graph data, d3-force bundle, small graph per note · M** · needs: C1
- Files: `tools/editor/` (add `graph.js` from d3-force, ISC licence, hash), `app.py`
  (`GET /api/notes/graph?around=…&depth=1`), `notes.html` (info panel: a small graph); tests.
- Tests: graph data (nodes, links, caps), the panel draws and clicking opens a note.

**F2 · Whole-graph view · M** · needs: F1
- Files: `notes.html` (or `graph.html`): zoom, drag, filter by tag or folder, colour by
  folder, size by number of links, labels when zoomed in, memories optionally; SVG up to about
  300 points, canvas beyond; tests.
- Tests: filters and caps, rendering, screenshots light and dark.

**F3 · Boards: JSON Canvas files, tools, API · M** · needs: A2
- Files: new `starlight/notebook/boards.py` (read/write `.canvas` per the JSON Canvas spec:
  text, file, link and group nodes; edges; unknown fields kept); new tools `boards.list`,
  `boards.read`, `boards.add_card` (versions like every file); API `GET/PUT /api/boards/...`
  with stamps; tests.
- Tests: spec examples round-trip, versions, conflicts, path safety.

**F4 · Board view on the page · L** · needs: F3, B6
- Files: new `static/board.js`, `notes.html` (open a `.canvas`), `style.css`; tests.
- Steps: cards (text, note cards showing the note, pictures, links, groups), arrows (SVG),
  drag, resize, connect, zoom and pan, snap grid, autosave with stamps; text only
  (`textContent`), links http(s) only.
- Tests: browser (add, drag, connect, save, reload), screenshots light and dark.

### Milestone G: extras (P11, each optional and on its own)
- **G1 · Export a note as HTML or PDF · S**: reuse `export_html` in `starlight/web/app.py`;
  a button on the Notes page; "send me Project X as PDF".
- **G2 · Kanban for tasks · M**: columns open / doing / done on the Notes page, from note tasks
  and to-dos (a `status:` key on task lines).
- **G3 · Flashcards (spaced repetition) · M**: `Question::Answer` lines; "quiz me"; intervals
  kept in `data/notebook/`; an optional daily automation.
- **G4 · Live preview · M**: CodeMirror decorations hide Markdown markers away from the cursor
  line (headings, bold, links), like a word processor.
- **G5 · Mermaid diagrams · S–M**: only if wanted; the library is about 2–3 MB (added to
  `tools/editor/`, with its licence).
- **G6 · Deep links · S**: `starlight://note/…` opens a note (Windows URL handler set by the
  launcher), usable from automations and other apps.
