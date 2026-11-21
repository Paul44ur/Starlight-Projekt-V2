# Plan: the overlay that points (Jarvis-style)

The user asked on 2026-11-21 for two things. First, Starlight should not be "a UI you have
to go to". It should be there on the screen and answer by voice. Second, it should point at
things on the screen: "Hey Starlight, where is the settings menu?" draws a line from the top
of the screen to the menu, frames it, and says "Here you go", without covering the screen.
It **always points without asking, and asks before it clicks**. The look should be
futuristic but clean. Reference for the feel: FatihMakes/Mark-LVII (a glowing "reactor"
orb, glass panels). Its licence is CC BY-NC 4.0 and the older Mark repos have none:
**take ideas only, copy no code from them.**

The user's PC: Windows 11, **two 1080p monitors, 100 % scaling**, AMD RX 9060 XT. The code
must still handle other layouts (a monitor left of or above the main one, 125 %/150 %,
mixed scaling), because that is where overlays usually break.

**State: agreed 2026-11-21.** The user asked for the plan to be split into work packages
for helper models. A concept picture (not final) is in the project's shared folder as
`plans/overlay-concept.png`.

Defaults the user has not answered yet (easy to change later, do not block on them):
- The web pages keep the Paper look (`docs/plan-ui-3.md`). Only the overlay looks
  futuristic. A matching redo of the pages would be its own plan.
- The orb is always visible, dim, at the top edge of monitor 1. A setting hides it until
  called (`orb: awake`).

## Status

| Pkg | Block | What | Needs | State |
|---|---|---|---|---|
| W1 | O1 | Protocol and geometry: the messages, monitors, where the orb, line, frame and caption go (pure Python) | – | open |
| W2 | O1 | The overlay process (Qt): windows per monitor, drawing, animation, the Yes/No card | W1's interface (section 4, fixed here) | open |
| W3 | O1 | The `overlay` module: starts and watches the process, kill switch, config, events, `point_at` | W1 | open |
| W4 | O2 | `desktop.find_target`: UI Automation, then OCR, then the vision model, returning a box | – | open |
| W5 | O3 | Voice states: the voice module tells what it is doing (listening, thinking, speaking), the mic level, captions | – | open |
| W6 | O4 | Ask before clicking: Yes/No by voice or click; "click it" after a point | W3, W4, W5 (and W2 for the card) | open |
| W7 | O5 | Settings section, German, launcher install, README and architecture docs | W2, W3 | open |
| W8 | O6 | Review and the user's Windows test list | all | open |

W1, W4 and W5 start at once, side by side. W2 and W3 start as soon as W1 is merged (or
at once, against the fixed interface in section 4). Then W6, then W7, then W8.

Each package: its own worktree from the newest `Starlight_Fable`, one commit, report as in
`docs/guide-for-helpers.md` section 11. **Helpers never push.** The lead reviews, merges,
runs the checks, pushes, and sets the package's State to **done** (with the commit).

## 1. How it fits together

```
 core process (pythonw, the user's desktop session)              overlay process (Qt)
 ┌─────────────────────────────────────────────────────┐         ┌──────────────────────┐
 │ voice module ──events──▶ overlay module ──stdin────────────────▶ windows per monitor   │
 │   (states, level,        (W3)          ◀─stdout─────────────── │ orb, line, frame,    │
 │    captions, asks)        │  ▲                      JSON lines │ caption, Yes/No card │
 │                           │  │ ctx.call                       └──────────────────────┘
 │                           ▼  │                                  python -m starlight.overlay
 │ desktop module: find_target (W4), click (existing, CONFIRM)     │
 └─────────────────────────────────────────────────────┘
```

- **The overlay is its own process**, `python -m starlight.overlay`, started by the
  `overlay` module with the core's own Python (`sys.executable`). Qt wants the main thread,
  and a crash in drawing must not take the core down. Talk is **JSON lines over the
  child's stdin and stdout**: no port, no token, and the child ends when the core ends
  (stdin closes).
- **Qt is PySide6-Essentials** (LGPL) in a new optional extra `overlay`, added to `all`.
  Only `starlight/overlay/` imports Qt. The core, the module and their tests never do.
- **Modules never import each other.** The voice module and the overlay module talk
  through events (section 5). The overlay module uses the desktop module through
  `ctx.call("desktop.find_target", ...)` and `ctx.call("desktop.click", ...)`.
- **Only the primary Starlight process** runs the overlay (`ctx.background`), so two open
  Starlights never draw twice.

New and changed files, by package:

| Package | Owns (creates or changes) |
|---|---|
| W1 | `starlight/overlay/__init__.py`, `starlight/overlay/protocol.py`, `starlight/overlay/geometry.py`, `tests/test_overlay_protocol.py`, `tests/test_overlay_geometry.py` |
| W2 | `starlight/overlay/__main__.py`, `starlight/overlay/app.py`, `starlight/overlay/scene.py`, `starlight/overlay/winapi.py`, `pyproject.toml` (the extra), `uv.lock`, `tests/test_overlay_app.py`, `tests/conftest.py` (one line: the one-worker group) |
| W3 | `modules/overlay/` (all files), `config/modules/overlay.yaml`, `tests/test_overlay_module.py` |
| W4 | `modules/desktop/backends.py`, `modules/desktop/module.py`, `modules/desktop/targets.py` (new), `config/modules/desktop.yaml`, `tests/test_desktop_targets.py` |
| W5 | `modules/voice/module.py`, `modules/voice/levels.py` (new), `tests/test_voice_states.py` |
| W6 | `modules/voice/voiceui.py` (new), `modules/voice/module.py` (small), `modules/overlay/module.py` (small), `tests/test_overlay_confirm.py` |
| W7 | `starlight/web/static/settings.html`, `starlight/web/app.py` (settings routes only), `starlight/web/static/i18n.js` (one block), `launcher/*` (only if the extra needs a step), `README.md`, `docs/architecture.md`, `docs/setup-windows.md`, `tests/test_overlay_settings.py` |
| W8 | `docs/plan-overlay.md` (status, findings), `docs/overlay-windows-test.md` (new) |

No package changes a file another package owns, except where a row says "small", which
then happens after the other package is merged (W6 comes last for that reason).

## 2. Finding the target (W4)

New tool in the desktop module, **`find_target(target: str, window: str = "")`**,
`Permission.SAFE` (it only reads the screen, nothing is kept). It tries in order and stops
at the first hit:

1. **UI Automation** (Windows only). Look in the foreground window first, then in every
   other visible top-level window on any monitor, largest first, at most 8 windows and
   2 seconds in all. Match the control's `Name`: exact (case-insensitive), then "starts
   with", then "contains". Try each **name variant** from a small table in `targets.py`
   (English and German, both directions): settings/Einstellungen/Optionen,
   file/Datei, edit/Bearbeiten, view/Ansicht, help/Hilfe, search/Suche/Suchen,
   save/Speichern, open/Öffnen, close/Schließen, new/Neu, print/Drucken,
   back/Zurück, menu/Menü, more/Mehr, share/Teilen, download/Herunterladen. Strip
   filler words first: "the", "menu", "button", "der", "die", "das", "das Menü", "Knopf",
   "Schaltfläche" (keep the bare word if stripping leaves nothing). Skip controls that are
   off screen (`IsOffscreen`) or have an empty or zero-size rectangle.
   **COM:** every UI Automation call made in a worker thread must be inside
   `with uiautomation.UIAutomationInitializerInThread():`. Sync tools run in
   `asyncio.to_thread`, so the existing `click_control` and `list_controls` most likely
   fail today with a COM error on Windows. Fix them the same way (and say so in the report;
   it can only be confirmed on Windows, W8).
2. **OCR** on each monitor (`find_phrase`), with the same variants. Tesseract's languages
   become `eng+deu` by default when `deu.traineddata` is installed, else `eng` with one
   warning in the log. Prefer a hit in the foreground window's rectangle; among the rest,
   the highest confidence.
3. **The vision model** (only if `vision` is configured). A new prompt asks for a box:
   `{"bbox_2d": [x1, y1, x2, y2]}` in the image's pixels, or `{"bbox_2d": null}`. Qwen2.5-VL
   answers in absolute pixels of the image it was given; scale back with the existing
   `scale`. If the model gives only a point (`x`, `y`, as the old `locate` prompt), make
   a 64 x 40 box around it. Vision hits are `confidence: "maybe"`.

Returns (coordinates are physical pixels on the virtual desktop, as `screen_info`):
`{"found": true, "box": [x, y, w, h], "monitor": 1, "method": "uia"|"ocr"|"vision",
"label": "<the control's name or the text found>", "confidence": "sure"|"maybe",
"window": "<window title, cut to 80>"}` or
`{"found": false, "target": "...", "tried": ["uia", "ocr", "vision"], "windows": [up to 5
window titles]}`. The titles help the model ask "which window?". It never guesses.

Keep `locate` as it is (other code uses it). `find_target` uses the existing `Screen`,
`Ocr` and `UiAutomation` backends, so the tests use the fakes in `tests/test_desktop.py`
(add a fake UI Automation with a small control tree).

## 3. What it looks like (W2)

Reference values; W8 tunes them on the user's PC. All sizes in logical pixels. One accent
colour (default `#4FC8FF`, setting `accent`) plus white, on dark glass.

- **Orb:** 44 px across, centred on the top edge of the orb monitor, its centre 30 px
  below the edge. A radial glow (white core, accent, fading out at 1.2x the radius) and
  two thin tilted ellipse rings (radius 18 and 22, squashed to 0.35, tilted +20° and -35°).
  - `asleep`: 35 % opacity, still. Nothing animates and no timer runs.
  - `idle` (awake, waiting): 70 %, rings turn once per 12 s.
  - `listening`: 100 %, glow radius follows the mic `level` (x1.0 to x1.5, smoothed).
  - `thinking`: rings turn once per 1.2 s, the glow pulses at 1 Hz.
  - `speaking`: glow follows the speech `envelope`, played from the moment it arrives.
  - With `orb: awake`, `asleep` means hidden.
- **Line:** from the orb's lower edge to the nearest point on the target box's edge. Three
  strokes, all with round caps: a dark halo (rgba 10,20,30,0.35, 5 px) so it shows on
  light windows, a soft glow (accent at 12 %, 10 px), and the core (gradient from accent
  at 15 % at the orb to 90 % at the target, 1.6 px). It draws out from the orb in 300 ms
  (ease-out). If the target is on the **other monitor**, the line starts at that monitor's
  own orb point (a small orb "ghost" appears there for the point), never crossing the gap.
- **Frame:** a rounded rectangle (radius 6) 4 px outside the box, accent 2 px, plus two
  glow rings (5 px and 10 px out, alpha 60 and 25). It pulses twice (scale 1.00 to 1.04,
  600 ms each), then stays. `confidence: maybe` draws it **dashed** and the caption says
  "Maybe here" / „Vielleicht hier“.
- **Lifetime of a point:** 6 s, or until the mouse is inside the box (checked 10 times a
  second with `QCursor.pos()`), or a new `point` or `clear`. It fades out in 250 ms.
- **Caption:** one or two lines, at most 60 characters a line (longer text is cut with
  "…"), on dark glass (rgba 12,18,28,0.80, radius 10, 1 px white at 20 %), white text,
  15 px. It sits **beside** the orb, on the side away from the target, so the line never
  crosses it (no target: to the right). Shown for `seconds` (default 4), then fades.
- **Yes/No card:** under the orb, dark glass like the caption, the question (up to three
  lines) and two buttons ("Ja" / "Nein", in the hello's language). It is the **only**
  part that takes clicks (see 4.3). Enter = first choice, Esc = last, but only if the card
  has the keyboard focus; it never takes the focus by itself.
- **Reduce motion** (setting, and Windows' "Animation effects" off, read once at start
  with `SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION)`): no pulsing, no turning, the
  line appears at once; states show as opacity only.
- **Cost:** draw only what changed; no timer while nothing moves. Target under 3 ms a
  frame at 1080p on the user's PC (W8 measures it).

## 4. Interfaces (fixed here so W1, W2 and W3 can work side by side)

### 4.1 Messages (`starlight/overlay/protocol.py`, W1)

One JSON object per line, UTF-8, `\n` at the end, at most 64 KB a line. Every message has
`"type"`. The core's first message is `hello`; the child answers `ready`. Unknown types are
logged and ignored (both sides). Bad JSON is logged and skipped, never fatal.

Core to overlay:

| type | fields |
|---|---|
| `hello` | `v`: 1, `accent`: "#RRGGBB", `orb_monitor`: 1-based int, `orb`: "always"\|"awake", `reduce_motion`: bool, `language`: "de"\|"en" |
| `config` | the same fields as `hello` except `v`, each optional: a settings change |
| `point` | `id`: str, `box`: [x, y, w, h] (ints, physical virtual-desktop pixels), `label`: str, `confidence`: "sure"\|"maybe", `seconds`: float (default 6) |
| `clear` | – (removes the line, the frame and the caption; not the card) |
| `state` | `state`: "asleep"\|"idle"\|"listening"\|"thinking"\|"speaking" |
| `level` | `level`: float 0..1 (mic, while listening) |
| `speech` | `envelope`: list of floats 0..1, `rate`: values per second (20) |
| `caption` | `text`: str, `who`: "you"\|"starlight", `seconds`: float (default 4) |
| `ask` | `id`: str, `question`: str, `choices`: [{"id": str, "label": str}] (2 or 3), `timeout`: float |
| `cancel_ask` | `id`: str |
| `quit` | – |

Overlay to core:

| type | fields |
|---|---|
| `ready` | `v`: 1, `screens`: [{"name", "x", "y", "w", "h", "dpr"}] (logical, Qt's view), `exclude_from_capture`: bool (true when every window is hidden from screenshots) |
| `answer` | `id`, `choice`: the choice's id, or null on timeout |
| `error` | `text`: str (logged by the module, shown nowhere) |

`protocol.py` has one frozen dataclass per message, `encode(msg) -> bytes` (one line) and
`decode(line: bytes) -> Message | None` (None for anything invalid: wrong types, missing
fields, numbers out of range, a `box` with non-positive size, more than 3 choices, text
over 2,000 characters). Strings are cut, never trusted as markup (Qt draws them as plain
text: use `drawText`, never a rich-text label).

### 4.2 Geometry (`starlight/overlay/geometry.py`, W1)

Pure functions over plain tuples, no Qt:

- `Monitor(name, physical: Rect, logical: Rect, dpr: float)`.
- `match_monitors(physical: list[Rect], logical: list[tuple[name, Rect, dpr]]) ->
  list[Monitor]`: pair Windows' monitors (physical pixels, from `EnumDisplayMonitors`) with
  Qt's screens by size (physical size / dpr ≈ logical size, within 2 px) and then by order.
- `to_logical(point, monitors) -> (monitor index, logical point)` and the same for a rect
  (a rect is placed on the monitor holding its centre; it is clipped to that monitor).
- `orb_point(monitor) -> point`: top centre, 30 px down.
- `line_end(start, box) -> point`: the nearest point on the box's edge (outside by 4 px).
- `caption_rect(orb, size, monitor, target=None) -> rect`: beside the orb on the side away
  from the target, 16 px gap, kept inside the monitor.
- `card_rect(orb, size, monitor) -> rect`: under the orb, inside the monitor.

Tests cover: one monitor; two side by side (the user's case: 0,0 and 1920,0); the second
left of the first (negative x); one above; 125 % and 150 %; mixed 100 % + 150 %; a box
across the gap between two monitors; a box partly off screen; a target to the left, right
and below the orb.

### 4.3 The windows (W2)

- One `QWidget` per `QScreen`, the screen's full geometry, flags `FramelessWindowHint |
  WindowStaysOnTopHint | Tool | WindowTransparentForInput | WindowDoesNotAcceptFocus`,
  attributes `WA_TranslucentBackground`, `WA_ShowWithoutActivating`,
  `WA_TransparentForMouseEvents`. It never steals the focus and never shows in the taskbar
  or Alt+Tab.
- The Yes/No card is a **separate small window** without `WindowTransparentForInput`
  (otherwise it could not be clicked), shown only while an `ask` is open.
- On Windows (`winapi.py`, ctypes, every function with `argtypes`/`restype`):
  `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE = 0x11)` on every overlay window,
  so screenshots (mss, OCR, vision, and the user's own) do not show it. If it fails (older
  Windows), log once and go on; then the overlay module must send `clear` and wait 150 ms
  before `find_target` takes a screenshot (W3 does this when `ready` says
  `exclude_from_capture: false`; W2 adds that field to `ready`).
  `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` before Qt starts.
  `EnumDisplayMonitors` + `GetMonitorInfoW` for the physical rects.
- Screens added or removed (`QGuiApplication.screenAdded/Removed`): rebuild the windows
  and send `ready` again.
- stdin is read on a plain thread that turns each line into a Qt signal (Qt objects are
  touched only on the GUI thread). stdin closed or `quit`: fade out and exit within 1 s.
- `python -m starlight.overlay --demo` shows each state, a point at a fixed box and an
  ask, one after another, for looking at it by hand (and for W8's screenshots).

### 4.4 The overlay module (W3)

`modules/overlay/module.yaml`: name `overlay`, description "Points at things on your
screen and shows Starlight's orb: where is a button or menu, show me, zeig mir",
keywords `["where is", "where's", "show me", "point", "find on screen", "wo ist",
"zeig mir", "zeige", "wo finde ich", "Menü", "Knopf", "Einstellungen"]`, permissions
`[safe, confirm]`, `enabled_by_default: true` (it does nothing visible until `enabled`).

Config (`config/modules/overlay.yaml`, every line with a comment like the other files):

```yaml
enabled: false        # Starlight.exe turns it on after installing the overlay extra
orb: always           # always: a dim orb at the top edge; awake: only while it listens or talks
orb_monitor: 1        # which monitor (1, 2 ...) holds the orb
accent: "#4FC8FF"     # the overlay's colour
reduce_motion: false  # also on when Windows' animation effects are off
point_seconds: 6      # how long a point stays
```

Behaviour:
- `setup`: if `enabled` and the `overlay` extra is importable (`importlib.util.find_spec
  ("PySide6")`), start the child with `ctx.background`. On Windows use
  `creationflags=CREATE_NO_WINDOW`. Send `hello`, wait up to 10 s for `ready`.
- The child dies: log it, restart after 2, 4, 8 ... up to 60 s; after 5 failures in 10
  minutes stop and log one warning. `shutdown` sends `quit`, waits 2 s, then kills.
- **Kill switch:** a cleanup sends `clear` and `cancel_ask` for an open ask (answering it
  "no") and `state: asleep`. While engaged, `point_at` raises `ToolError`.
- Tools:
  - **`point_at(target: str, window: str = "")`**, SAFE: "Show where something is on the
    screen: draws a line from the top of the screen to it and frames it. Use it whenever
    the user asks where something is on their screen." Calls
    `ctx.call("desktop.find_target", ...)`; on a hit sends `point` and a `caption`
    ("Hier ist es" / "Here you go", or "Vielleicht hier" for maybe, with the label) and
    remembers the hit as `last_target` (with the time). Returns the hit, plus
    `"shown": false` if the overlay is off or not running (the model then just answers in
    words). On a miss it returns the miss unchanged and points at nothing.
  - **`clear_pointer()`**, SAFE.
  - **`overlay_status()`**, SAFE: enabled, running, screens, last error.
- Events it subscribes to and forwards (section 5): `voice.state`, `voice.level`,
  `voice.speech`, `voice.caption`, `overlay.ask`, `overlay.cancel_ask`. Answers from the
  child go out as `overlay.answer`.
- `level` messages are dropped when more than 15 a second arrive.

Tests use a fake child: a small Python script (in the test) that echoes `ready`, records
what it receives in a file and can be told to crash. No Qt.

## 5. Events between the voice and overlay modules (W5, W6)

| Event | Data | Sent by | When |
|---|---|---|---|
| `voice.state` | `state`: "idle"\|"listening"\|"thinking"\|"speaking"\|"asleep" | voice | listening: the wake phrase was heard or a follow-up is awaited; thinking: from the request to the answer; speaking: while playing; idle: after; asleep: wake word off |
| `voice.level` | `level`: 0..1 | voice | while `listening`, at most 15 a second (RMS of the int16 frames, scaled so normal speech is about 0.5, clipped) |
| `voice.speech` | `envelope`, `rate` | voice | just before playback starts: the RMS of the WAV in 50 ms steps (`levels.py`) |
| `voice.caption` | `text`, `who` | voice | the request it heard (`you`), and the first sentence of the answer (`starlight`), cut to 120 characters |
| `overlay.ask` | `id`, `question`, `choices`, `timeout`, optional `tool`, `args` | voice (W6) | a CONFIRM tool asks during a voice request |
| `overlay.cancel_ask` | `id` | voice (W6) | answered by voice first, or the request ended |
| `overlay.answer` | `id`, `choice` | overlay | a click on the card, or the timeout (null) |

The voice module sends these only when the wake word is on; they cost nothing when nobody
subscribes. Typed requests in the web chat do not move the orb (version 1).

## 6. Ask before clicking (W6)

Today a CONFIRM tool asked during a wake-word request goes to the default interface,
which answers **no** (`NoUser`), so voice cannot click at all. The dispatcher asks with a
fixed text, `question_for()` in `starlight/core/kernel.py`: an optional reason, then
"Run <tool> with {args}?". Do not change that text (automations and the approval queue
parse it). W6 adds:

- `modules/voice/voiceui.py`: `VoiceUI(UserIO)`. The voice module sets it as
  `current_ui` around `run_request` in `on_speech` (and resets the token after).
  - `confirm(question)`: find the tool and arguments with
    `re.search(r"Run (\S+) with (.*)\?$", question, re.S)` (the reason may come before
    it). Publish `overlay.ask` with `id`, `tool`, `args` (the text), a spoken-style
    `question` and yes/no choices in the user's language. The spoken question: for
    `overlay.click_target` "Soll ich das anklicken?" / "Shall I click it?"; for any
    other tool "Darf ich <tool> ausführen?" / "May I run <tool>?". Say it out loud and at
    the same time listen for the answer (the existing `_next_speech`, 8 s):
    "ja", "jo", "klar", "mach", "yes", "okay", "ok" = yes; "nein", "nee", "stopp",
    "abbrechen", "no", "cancel" = no; anything else = ask once more, then no. The first
    answer wins (a click on the card, via `overlay.answer`, or the voice); the other is
    cancelled (`overlay.cancel_ask`). No answer in 20 s = **no**. If no overlay is running
    (`ctx.has_tool("overlay.point_at")` is false, or no `overlay.answer` can come), voice
    alone decides. The dispatcher already writes yes and no to the audit log.
  - `ask(question, secret)`: say the question, take the next speech as the answer;
    `secret=True` always returns None (never by voice).
  - `notify(text)`: a caption plus saying it.
- `modules/overlay/module.py`:
  - On `overlay.ask` with `tool == "overlay.click_target"`: show `last_target` again
    (`point`) and put its label in the card: "„Einstellungen“ anklicken?".
  - Tool **`click_target(target: str = "")`**, `Permission.CONFIRM`: "Click what was just
    pointed at (or find `target` and point first). Asks the user first." With no target
    it uses `last_target` if it is younger than 60 s, else `ToolError` ("point at it
    first"). It clicks at the box's centre with `ctx.call("desktop.click", {"x", "y"})`.
    `desktop.click` is CONFIRM too, so the user would be asked twice: around that one
    call set `current_ui` to a small "already approved for desktop.click" interface, the
    same idea as `ApprovedUI` in `modules/approvals/module.py` (write a small copy in the
    overlay module: modules do not import each other).
  - Practice mode (`policy.practice`) keeps working by itself: the dispatcher describes
    the click instead of doing it. Add a test that shows this.
- Clicking keeps the `desktop` module's failsafe and red banner as they are.

## 7. Settings, German, install, docs (W7)

- Settings page, a new section "Overlay" under the voice section: on/off, "Orb: always /
  only when awake", "Orb on monitor" (1..n from `screen_info`), colour (a few presets:
  cyan `#4FC8FF`, green `#3DDC97`, amber `#FFB547`, violet `#A78BFA`, white `#E8EEF5`),
  reduce motion, and a "Show a test point" button (points at the Settings page's own
  heading for 3 s). Routes `local_only`. Changing a setting writes the yaml and sends the
  child a `config` message (no restart).
- If the extra is missing: the section says "Not installed. Starlight.exe → Overlay
  installs it (about 100 MB)." W7 checks how `Starlight.exe --voice` installs things and
  does the same for the extra, or, if `uv sync --extra all` already covers it after W2,
  only says so.
- German entries in one block in `i18n.js`, marked `// overlay (plan-overlay W7)`.
  Captions and the card's buttons in the child come from the hello's `language` (W2 keeps
  a tiny table of its own six texts).
- README: what the user sees, two short paragraphs. `docs/architecture.md`: the process,
  the messages, the events. `docs/setup-windows.md`: turning it on.

## 8. Review and the Windows test list (W8)

The lead (or a careful helper) checks the whole flow in this container with the demo mode
and the fake child, then writes `docs/overlay-windows-test.md`: a short list for the user
on his PC, each with what should happen:

1. Turn it on in Settings. The orb appears dim at the top of monitor 1; nothing else
   changes, the mouse clicks through everywhere, no taskbar entry.
2. "Hey Starlight, wo sind die Einstellungen?" in Explorer: line and frame on the
   right spot; "Hier ist es".
3. The same with the target on monitor 2.
4. A target only the vision model can find (the gear icon in Chrome's menu). Note the time.
5. "Klick drauf": the card and the question; say "nein": nothing is clicked. Again, "ja":
   it clicks, the red banner shows.
6. Ctrl+Alt+J during a point: everything disappears.
7. A screenshot with Win+Shift+S while a line shows: the line is not in it.
8. A fullscreen game: the orb is not drawn over it (expected), nothing breaks.
9. Task Manager: the overlay process uses ~0 % CPU while the orb is still.

The user's results go into section 9 below, and fixes become new rows in the Status table.

## 9. Findings

(empty: filled in by W8 and the user's test)

## 10. Limits (said plainly to the user)

- Exclusive-fullscreen games and Windows admin prompts (UAC) cannot be drawn over.
- Some apps hide their controls from UI Automation; then OCR or the vision model must find
  them, which is slower and less exact (shown as "maybe").
- Tests here run Qt with `QT_QPA_PLATFORM=offscreen`: geometry, messages and drawing are
  tested, but click-through, the focus and "hidden from screenshots" only on Windows (W8).
- This container needs these system libraries for Qt tests (root, once):
  `apt-get update && apt-get install -y libegl1 libxkbcommon0 libfontconfig1 libgl1
  libdbus-1-3`. Without them, Qt tests skip (`pytest.importorskip` on
  `PySide6.QtGui`), they must not fail.

## 11. Briefs for the helpers

Every brief starts with: read `CLAUDE.md`, `docs/guide-for-helpers.md`, then this file
(sections 1, 4 and your package's section). Work in your own worktree from the newest
`Starlight_Fable`. Change only the files your package owns (section 1). Show that your new
tests fail on the old code (for new files: that they fail when the new module is absent is
not enough; break one behaviour on purpose and show a test catches it). Run the static
checks of the guide, plus `mypy` on your new package. One commit, no push.

**W1 (protocol and geometry).** Write 4.1 and 4.2 exactly. Done when: every message
round-trips through `encode`/`decode`; every invalid case in 4.1 gives None; every layout
in 4.2's test list gives the expected numbers (write them out by hand in the tests, not
computed by the code under test); no Qt import anywhere in these files.

**W2 (the overlay process).** Write 4.3 and the look in section 3, using W1's
`protocol` and `geometry` (if W1 is not merged yet, code against section 4 and leave
the import; the lead merges W1 first). Add the extra `overlay = ["PySide6-Essentials>=6.7"]`
to `pyproject.toml`, add it to `all`, run `uv lock`. Tests (`QT_QPA_PLATFORM=offscreen`):
the child started as a subprocess answers `hello` with `ready`; `point` then `clear`;
`ask` then a simulated click gives `answer`; `ask` with a timeout gives `answer` with null;
bad lines are skipped; stdin closed ends it within 2 s; a rendered frame (`widget.grab()`
saved as PNG) has accent-coloured pixels on the line between the orb and the box and none
elsewhere far from them. Look at the PNGs (light and dark backgrounds behind them: paint
a fake desktop into a test image as in `plans/overlay-concept.png`) and fix what looks
wrong. `winapi.py` only runs on Windows: test its pure parts, list the rest in the report.
Add `test_overlay_app.py` to the one-worker group in `tests/conftest.py`.

**W3 (the overlay module).** Write 4.4. Tests with a fake child and a fake
`desktop.find_target` (a stand-in module registered in the test kernel, see how other
tests stub `ctx.call`): start and `hello`; restart with back-off after a crash and give up
after 5; kill switch sends `clear`/`state asleep` and blocks `point_at`; `point_at` hit
sends `point` + `caption` and returns the hit; miss sends nothing; overlay off gives
`shown: false`; events are forwarded; `level` is throttled; `shutdown` sends `quit`.

**W4 (finding the target).** Write section 2. Tests with the fakes: a UI Automation hit in
the foreground window; a hit only in a background window; the German variant
("settings" finds "Einstellungen"); filler words; an off-screen control is skipped; OCR
fallback on monitor 2 with correct global coordinates (2 side by side, and one left of the
main monitor); vision bbox scaled back; vision point-only answer; a miss lists the window
titles; nothing found never returns a box. Also the COM fix (no test possible here: say so).

**W5 (voice states).** Write the voice side of section 5. `levels.py`: `rms_level(frame:
bytes) -> float` and `envelope(wav: bytes, rate=20) -> list[float]` (pure, tested with
generated sine and silence). Publish the events at the right moments in `on_speech`,
`_say`, the listener and `wake_word(on)`. Tests drive `on_speech` with stubbed
transcription, `run_request` and playback, and assert the event order: listening →
caption(you) → thinking → caption(starlight) → speech → speaking → idle. Levels at most 15
a second. Nothing published while the wake word is off. No change in what is said.

**W6 (ask before clicking).** Write section 6, after W3, W4 and W5 are merged. Tests: a
voice request whose tool needs CONFIRM: answered "ja" by voice → runs; "nein" → not run;
a click answer (`overlay.answer`) → runs, and the voice listener is cancelled; silence →
not run after the timeout (use a short timeout in the test); `click_target` without a
recent point → ToolError; with one → `desktop.click` at the centre, asked once.

**W7 (settings and docs).** Write section 7. Screenshots of the new section in light,
dark and German; a paired phone does not see it and the routes answer 403.

**W8 (review).** Section 8. Run the full suite and the static checks once on the merged
branch; run `--demo` under xvfb and look at screenshots of every state; write the Windows
test list; fill in section 9 with anything that is off; update the Status table.
