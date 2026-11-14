# A guide for helper models working on Starlight

This guide is for a smaller model that is given ONE piece of work on Starlight, most often
on the pages (the UI). A lead writes your brief, reviews your change and merges it. Read
`CLAUDE.md` first, then this guide, then your brief. Where your brief and this guide differ,
the brief wins.

Starlight is a local AI assistant: a Python core (`starlight/`), modules (`modules/`), a
FastAPI web chat (`starlight/web/`), and a standard-library Windows launcher (`launcher/`).
The user is German and uses Windows 11. Everything runs on their own PC.

## 1. The ten rules

1. **Stay inside your item.** Do not refactor, rename or reformat code you do not have to
   touch. A small, exact change is worth more than a large, tidy one.
2. **Read before you write.** Open the file, read the code around the place, and copy its
   style: its names, its comments, how it handles errors.
3. **Every behaviour change gets a test**, and you show that the new test fails on the old
   code (section 7).
4. **Every new text on a page gets a German entry** (section 4).
5. **Never use the browser's own boxes** (`confirm`, `prompt`, `alert`). Ask with
   `askYesNo` and `askText` (section 3).
6. **Only tokens for colour, space and radius.** No new colours, no hex values in a page
   (section 2).
7. **The user's words are never HTML and never translated.** Set them with `textContent`
   and mark them `translate="no"` (section 5).
8. **Safety first.** Keep an earlier version before changing a file of the user's. Treat
   every file, page and message from outside as untrusted. Settings change only at the PC.
9. **Look at what you built.** Take screenshots, light and dark and in German, and open
   them. Fix what looks wrong before you report (section 8).
10. **Report honestly.** Say plainly what fails, what you skipped and what you could not
    check. A wrong "all done" costs more than an honest "this part is missing".

## 2. The look: tokens and building blocks

All shared styles are in `starlight/web/static/style.css`. A page adds only what is its own
(`chat.css`, `notes.css`, or a `<style>` block in a small page).

**Tokens** (defined on `:root`, with a night and a day value each; night is the default; values are in `docs/plan-ui-2.md` section 2):

| Kind | Tokens |
|---|---|
| Surfaces | `--bg` (page), `--panel` (cards, bars), `--panel2` (hover, fields) |
| Lines and text | `--border`, `--text`, `--dim` (secondary text) |
| Accent (violet: links, focus, switches) | `--accent`, `--accent-tint` (soft hover wash), `--on-accent` (text on a filled accent, ok or warn) |
| Primary (gold: the star and ONE main action) | `--primary`, `--on-primary`, `--primary-edge` (the border on a day page) |
| Status | `--ok`, `--warn`, `--bad` (red text), `--danger` (filled red behind `--on-danger` text) |
| Space | `--s-1` 4px, `--s-2` 8px, `--s-3` 12px, `--s-4` 16px, `--s-5` 24px, `--s-6` 32px |
| Radius | `--r-1` 6px, `--r-2` 8px, `--r-3` 12px, `--r-4` 16px (cards), `--r-pill` |
| Fonts | `--font-display` (Space Grotesk, headings), `--font-body`, `--font-mono` |
| Type | `--fs-xs` 12px, `--fs-sm` 13px, `--fs-md` 15px, `--fs-lg` 16px, `--fs-xl` 20px |
| Shadow | `--shadow-1` (resting), `--shadow-2` (lifted: toasts, dialogs, menus) |
| Motion | `--ease`, `--dur-fast`, `--dur` |

**Building blocks** already in `style.css`. Use them before you write new CSS:

- Layout: `.page`, `.card`, `.row`, `.stack`, `.cluster`, `.grow`
- Text: `.dim`, `.muted`, `.small`, `.truncate`, `.sr-only`, `.num` (mono numbers), `.section-label`, `kbd`
- Status: `.pill` (with `.on`, `.warn`, `.off`), `.toast`, `.skeleton` (loading bars)
- Buttons: plain `button`, `button.primary` (the one main action), `button.ok`,
  `button.danger` (filled red), `button.danger-outline`, `button.icon`, `button.quiet`
- Dialogs: `.ask` (questions), `#quick` (the quick switcher), `#shortcuts`

**What a good page looks like here**

- One main action per view is `primary`. The rest are plain buttons.
- A destructive button is red, and never the one the keyboard starts on.
- Secondary text is `--dim`, not a smaller grey of your own.
- Things line up: icons 18px in rows, text on one baseline, the same gap between rows.
- Long text is cut with an ellipsis (`.truncate`), never allowed to push the layout wide.
- Nothing scrolls sideways at 360px width. A touch screen gets controls of at least 44px
  (`@media (pointer: coarse)` in `style.css` already does this for standard controls).
- The page works in the light look, the dark look, high contrast and with reduced motion.
  Use the tokens and the existing blocks and you get all four for free.
- The first paint is not empty: show a `.skeleton` while a list loads.

## 3. Shared helpers (`common.js`)

Every page loads `common.js`. Use these; do not write your own version.

| Helper | What it does |
|---|---|
| `el(tag, cls, text)` | Makes an element. `text` goes in as text, never as HTML. |
| `api(path, {method, body})` | A JSON call. Throws an `Error` with the server's message. |
| `toast(text)` | One short message at the bottom. A new one replaces the last. |
| `askYesNo(text, {yes, no, danger})` | A question in the page's look. Resolves `true` or `false`. |
| `askText(label, value, {yes, placeholder})` | One line of text. Resolves the text, or `null`. |
| `icon(name, alone)`, `setIcon(node, name)` | A drawn line icon (see below). |
| `showShortcuts()` | The page's keyboard shortcuts (`window.SHORTCUTS`). |
| `cycleTheme()` | Light, dark, like the system. |
| `readySignOn()` | Whether this device wants the "answer is ready" sign. |

**Questions.** A text with a blank line in it shows as the question and its details:
`await askYesNo("Delete \"" + name + "\"?\n\nIt cannot be brought back.", {yes: "Delete",
danger: true})`. With `danger`, the answer is red and the keyboard starts on Cancel. Esc and
a click beside the window mean "no". Functions that ask must be `async`.

**Icons.** `ICONS` in `common.js` holds every icon (24 x 24, drawn as lines in the text
colour): menu, plus, lock, sun, moon, speaker, mute, clip, camera, mic, stop, send, down,
more, search, info, history, download, link, trash, eye, close, today, inbox, tasks, graph,
cards, minus, fit, talk, refresh, picture, notes, memory, activity, automations, settings,
edit, pin, folder, archive, forget, print, back, chevron, boards. In markup write
`data-icon="trash"`; in code call `setIcon(button, "trash")`. Do not use emoji as icons.
A new icon is drawn in the same weight as the others and added to `ICONS`; a button that
has only an icon needs a `title` and an `aria-label`.

**Menus.** On the chat page, `popMenu(anchor, items)` (in `chat-list.js`) takes rows of
`[icon name, label, action, hint]`.

**The quick switcher** is `quick.js` (Ctrl+K). A new page must load it after `common.js`.

## 4. German

The pages are written in English. `starlight/web/static/i18n.js` turns them into German
when the language is German. It translates text nodes and the `title`, `placeholder` and
`aria-label` attributes by itself, also for elements added later.

- A fixed text: add `"English text": "German text",` to the `DE` dictionary. The key is the
  exact English text, with single spaces.
- A text with a changing part: add a pattern to `PATTERNS`, for example
  `[/^Delete "(.+)"\?$/, "„$1“ löschen?"],`. The changing part stays as it is.
- A text that never reaches the page as an element (a notification, a text you build and
  pass on): translate it yourself with `window.starlightTranslate(text)`.
- Put all your new entries together in ONE block with a comment line, at the place your
  brief names. Two helpers that add entries at the same place cause a merge conflict.
- Use informal German ("du"). Use real German quotation marks („…“). Keep it short: German
  runs about a third longer than English, so check that buttons and headings still fit.
- Do not reuse one English text for two meanings. "Archive" the action and "Archive" the
  place need two different English texts, or German cannot tell them apart.
- The launcher window has its own list: the `GERMAN` dict in `launcher/launcher_gui.py`,
  used through `tr()` and `trf()`.

## 5. The user's words

Names, titles, notes, folder names, model names and anything typed by the user or read
from a file:

- go in with `textContent` (or `el(tag, cls, text)`), never with `innerHTML`;
- are marked `translate="no"` (or `node.translate = false`), so a conversation called
  "Memory" does not turn into "Gedächtnis";
- are cut to a sensible length before they go into a tooltip or a notification;
- never decide what the code does. A file name from a file is checked before it is used as
  a path (no `..`, no drive letters, only inside the folder it belongs to).

Markdown from the model is drawn by `markdown.js` and `render.js`, which escape it. Do not
write a second Markdown renderer.

## 6. Where things live

**Pages** (`starlight/web/static/`):

| Page | Files |
|---|---|
| Chat | `chat.html` (markup only), `chat.css`, then in this order `chat-core.js` (state, the socket, the message flow), `chat-messages.js` (drawing messages), `chat-list.js` (the conversation list, its menu, the bar's title), `chat-input.js` (the message box, files, voice, `/` commands, keys), `chat-page.js` (status, the context meter, start-up) |
| Notes | `notes.html`, `notes.css`, `notes-page.js`, `notes-edit.js`, `notes-editor.js`, `notes-find.js`, `notes-info.js`, `notes-tasks.js`, `notes-views.js`, `board.js` |
| Others | `today.html`, `settings.html`, `memory.html`, `activity.html`, `automations.html`, `kill.html`, `pair.html` |
| Shared | `style.css`, `common.js`, `i18n.js`, `quick.js`, `markdown.js`, `render.js`, `sw.js` |

The chat's scripts are classic scripts that share their top-level names. A `const` in a
later file cannot be used while an earlier file loads. Keep the load order.

**A new file must be served.** The app serves static files by name. A new script or
stylesheet needs its route in `starlight/web/app.py` (see `CHAT_SCRIPTS`), an entry in the
service worker's list in `sw.js` (raise its version), and the tests that list the files.

**The server.** Routes are in `starlight/web/app.py` and, for larger areas, in their own
files registered from it (`notes_api.py`, `backup_api.py`, `model_api.py`). Every route
begins with a guard: `viewer(request)` for anyone who may look (the PC and paired phones),
`local_only(request)` for the PC only. Anything that changes settings, safety, devices, the
model or the data as a whole is `local_only`.

**The launcher** (`launcher/`) uses only the standard library and must pass
`mypy --strict` on Linux and with `--platform win32`. Windows-only code (ctypes, winreg)
starts with `if sys.platform != "win32": return ...`. It cannot run here, so keep it small,
set `argtypes` and `restype` on every Win32 function, test the logic around it with
stand-ins, and list it in your report.

## 7. Tests

The test command (one file or a few; from your worktree):

```
V=/home/user/Starlight-Projekt-V2/.venv/bin
env -u UV_PROJECT_ENVIRONMENT -u UV_CACHE_DIR PYTHONPATH=$PWD NO_PROXY=127.0.0.1 \
  STARLIGHT_CHROMIUM=/opt/pw-browsers/chromium timeout 900 \
  xvfb-run -a -s "-screen 0 1366x768x24" $V/python -m pytest -p no:cacheprovider -q tests/test_x.py
```

Add `-n 4` to run the whole suite in parallel (about five minutes). Run the whole suite only
if your brief says so. Do not run `uv sync` or `uv run`.

**Page tests** use Playwright with a stubbed server. Reuse the helpers:

| Helper | In | For |
|---|---|---|
| `static_site`, `SEND`, `SETTINGS_API` | `tests/test_chat_page.py` | serving the pages; sending a socket message; the Settings page's stub |
| `answer(page, yes, text)`, `YES` | `tests/test_chat_page.py` | answering a question; answering all of them with yes |
| `browser_page` | `tests/test_feedback_look.py` | opening a page with an init script |
| `open_app`, `APP`, `LIST`, `STATUS`, `NARROWED` | `tests/test_chat_app_look.py` | the chat with conversations and a status |
| `notes_page`, `opened`, `app`, `notebook` | `tests/test_notes_page.py` | the Notes page on a real app |
| `client`, `home`, `LOCAL`, `PHONE`, `pair` | `tests/test_remote.py` | API tests as the PC and as a paired phone |

**What to test for a UI change**

- what the user sees (texts, order, what is hidden) and what gets sent to the server;
- the "no" path as well as the "yes" path of every question;
- German: the texts are translated and a user's name is not;
- a phone: nothing sticks out at 360px (`document.documentElement.scrollWidth <= 360`);
- a paired phone: PC-only controls are hidden and the route answers 403;
- text that looks like HTML shows as text.

**Wait, do not sleep.** Use `page.wait_for_selector` and `page.wait_for_function`. A dialog
closes a moment after the key, and a list arrives a moment after the click.

**Show the new tests fail on the old code,** in a scratch copy of the base commit:
`git worktree add --detach <scratch folder> <base commit>`, copy your new test files into
its `tests/`, run them there with that folder on `PYTHONPATH` (they must fail), then
`git worktree remove --force <scratch folder>`. Never use `git stash`: the stash list is
shared by all worktrees, and one helper once popped another helper's stash. Say in your
report what failed. Never weaken a test to make it pass: no `skip`, no `xfail`, no removed assertion.

**Static checks** (all must pass):

```
$V/ruff check . && $V/ruff format --check . && $V/mypy && $V/mypy --platform win32 \
 && $V/mypy modules && $V/mypy modules --platform win32 \
 && $V/mypy --strict launcher/starlight_launcher.py launcher/launcher_gui.py launcher/launcher_tray.py \
 && $V/mypy --strict --platform win32 launcher/starlight_launcher.py launcher/launcher_gui.py launcher/launcher_tray.py
```

Check JavaScript with `node` (`new Function(source)` for a classic script).

## 8. Screenshots

For anything new on a page, take screenshots and open them with the Read tool:

- the light look and the dark look (`localStorage.setItem('starlight-theme', 'light')`);
- German (`localStorage.setItem('starlight-lang', 'de')`);
- a phone, 360px wide, when the page is used on a phone.

Use `device_scale_factor=2` for a picture a person will see. Look for: text that is cut or
wraps badly, things that do not line up, a button that looks like the main action but is
not, low contrast, an empty area, German that does not fit.

## 9. Checklist for a UI change

- [ ] The change does what the brief asks, and nothing else.
- [ ] Tokens only; existing blocks reused; no emoji as icons.
- [ ] No `confirm`, `prompt`, `alert`; no `innerHTML` with the user's words.
- [ ] German entries for every new text, in one block.
- [ ] Keyboard: Tab reaches it, Enter and Esc do the expected, focus is visible, and the
      focus goes back where it was when a dialog closes.
- [ ] Screen readers: a `title` and `aria-label` on icon buttons, `role` where an element
      acts as something else.
- [ ] 360px wide: nothing sticks out. Light and dark both look right.
- [ ] PC-only things are hidden for a phone and refused by the server.
- [ ] Tests: seen, sent, "no" path, German, phone. They fail on the old code.
- [ ] Static checks pass. `git status --short` shows only your files.
- [ ] Docs: a short paragraph in `README.md` (what the user sees) and
      `docs/architecture.md` (how it works), in plain, short sentences.

## 10. Common mistakes

- **A name that is already taken.** The pages share global names. Search all the static
  files before you add a top-level `function` or `let` (a second `ask` once broke the chat).
- **A key that also does something else.** Keys pressed in a dialog must stay in it
  (`stopPropagation` on its `keydown`), or the chat's Esc stops the answer being written.
- **A translated name.** A title set without `translate="no"` is translated if it happens
  to be a word the dictionary knows.
- **A lone separator or label.** Hide the things around an element when it is hidden.
- **State that vanishes.** If a list can leave out the open item (a search, the archive),
  what the bar shows must not depend on that list.
- **A file nobody serves.** A new script that is not routed gives a blank page in the app
  but works in the static test site. Add the route and the test.
- **Editing while tests run.** A test run reads the files as they are. Do not change files
  in the folder a run is using.
- **`git stash`.** Do not use it (see section 7). A scratch worktree left behind: remove it.

## 11. Your report

One commit in your worktree; the lead rewrites its message. Then report:

1. the commit hash and the files changed;
2. what it does now, in a few plain sentences;
3. the tests you added, that they pass, and what failed on the old code;
4. the last lines of the static checks;
5. where the screenshots are and which ones you opened;
6. what you did differently from the brief, and why;
7. what you could not do or are unsure about;
8. what can only be checked on a real Windows PC.

Never push, never open a pull request, never trigger CI.
