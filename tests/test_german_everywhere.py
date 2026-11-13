"""Block V7 of the UI 2 plan: every page, opened in German, shows no English text. It opens each
page (with the states that hold the most words: open menus, dialogs, details, every view), reads
every visible text and every title, placeholder and aria-label, and fails on a text that is
still English after the page translated itself, unless it is on the short allow-list below."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_app_look import APP
from tests.test_chat_page import CHROMIUM, SEND
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import app as app  # (the fixture)
from tests.test_notes_page import notebook as notebook  # (the fixture)
from tests.test_remote import LOCAL

# Texts that stay as they are in German: a name, a key, a file or a unit. Each with its reason.
ALLOWED = {
    "Starlight": "the name",
    "Space Grotesk": "a font's name",
    "Markdown": "a format's name",
    "Ctrl": "a key",
    "Alt": "a key",
    "Shift": "a key",
    "Enter": "a key",
    "Esc": "a key",
    "Tab": "a key",
}

# Texts that are English on purpose (a regular expression each, with the reason).
ALLOWED_PATTERNS = {
    r"^Modul [\w.]+$": "a module's name after the word",
    r"(fehlgeschlagen|nicht ausgeführt): ": "the reason is what the tool said, in English",
    r"^\S+ [\w*]+\.[\w*]+ \d+ ms$": "a tool step's name and time",
    r"^that automation is not valid: ": "the server's own message about what is wrong (English)",
    r"\(englisch\)$": "the schedule words must be English: the placeholder says so",
}

# How a text is told apart: it is English when it holds an English word below and nothing that
# marks it as German (an umlaut, or one of the German function words).
ENGLISH = set(
    [
        "the",
        "and",
        "to",
        "of",
        "is",
        "you",
        "your",
        "for",
        "with",
        "are",
        "this",
        "that",
        "when",
        "from",
        "can",
        "has",
        "have",
        "will",
        "it",
        "on",
        "off",
        "be",
        "at",
        "or",
        "by",
        "as",
        "if",
        "its",
        "them",
        "they",
        "their",
        "there",
        "here",
        "what",
        "which",
        "who",
        "how",
        "why",
        "where",
        "only",
        "also",
        "but",
        "then",
        "than",
        "too",
        "very",
        "just",
        "still",
        "already",
        "again",
        "once",
        "every",
        "each",
        "other",
        "without",
        "within",
        "about",
        "over",
        "under",
        "into",
        "onto",
        "upon",
        "yes",
        "ask",
        "asks",
        "first",
        "never",
        "runs",
        "run",
        "limits",
        "past",
        "pages",
        "page",
        "per",
        "keep",
        "own",
        "folder",
        "files",
        "file",
        "cannot",
        "change",
        "remote",
        "access",
        "nothing",
        "show",
        "hide",
        "add",
        "remove",
        "edit",
        "today",
        "yesterday",
        "tomorrow",
        "next",
        "last",
        "before",
        "after",
        "more",
        "less",
        "new",
        "open",
        "save",
        "delete",
        "send",
        "read",
        "write",
        "create",
        "update",
        "search",
        "find",
        "select",
        "choose",
        "try",
        "need",
        "help",
        "sorry",
        "error",
        "problem",
        "warning",
        "failed",
        "works",
        "work",
        "did",
        "does",
        "done",
        "make",
        "see",
        "look",
        "turn",
        "click",
        "back",
        "undo",
        "close",
        "cancel",
        "restore",
        "rename",
        "download",
        "upload",
        "reset",
        "settings",
        "notes",
        "memory",
        "activity",
        "automations",
        "conversation",
        "message",
        "messages",
        "answer",
        "question",
        "questions",
    ]
)
GERMAN = set(
    [
        "aus",
        "der",
        "die",
        "das",
        "und",
        "oder",
        "nicht",
        "ein",
        "eine",
        "einen",
        "einem",
        "einer",
        "mit",
        "für",
        "von",
        "zu",
        "zum",
        "zur",
        "ist",
        "auf",
        "den",
        "dem",
        "des",
        "im",
        "bei",
        "nach",
        "wie",
        "z",
        "dein",
        "deine",
        "deinen",
        "deinem",
        "deiner",
        "du",
        "dir",
        "dich",
        "sich",
        "wird",
        "werden",
        "kann",
        "alle",
        "alles",
        "keine",
        "kein",
        "noch",
        "nur",
        "auch",
        "schon",
        "wenn",
        "dann",
        "bis",
        "über",
        "gespeichert",
        "gelöscht",
    ]
)

COLLECT = """() => {
  const found = [];
  // translate=no marks the user's words, except on Settings' safety rows: the server sends
  // those in the page's language already, so they are read like any other text.
  const skip = el => {
    const own = el.closest('[translate=no]');
    if (own && !own.closest('#safety')) return true;
    // .mdesc, .tdesc, .mname: modules and tools describe themselves in English (their own
    // docstrings); .confirm .q: a tool's question to you comes from the tool the same way.
    return !!el.closest('.notranslate, code, pre, textarea, script, style, .msg .body, '
      + '.cm-editor, svg, .mdesc, .tdesc, .mname, .confirm .q');
  };
  const shown = el => el.checkVisibility ? el.checkVisibility({visibilityProperty: true}) : true;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const p = n.parentElement, t = n.nodeValue.replace(/\\s+/g, ' ').trim();
    if (!p || !t || skip(p) || !shown(p)) continue;
    found.push(['text', t, p.tagName.toLowerCase() + (p.id ? '#' + p.id : '')
      + (p.className && typeof p.className === 'string' ? '.' + p.className.split(' ')[0] : '')]);
  }
  for (const el of document.querySelectorAll('[title], [placeholder], [aria-label]')) {
    if (skip(el)) continue;
    for (const a of ['title', 'placeholder', 'aria-label']) {
      const v = el.getAttribute(a);
      if (v) found.push([a, v.replace(/\\s+/g, ' ').trim(), el.tagName.toLowerCase()
        + (el.id ? '#' + el.id : '')]);
    }
  }
  return found;
}"""


def looks_english(text: str) -> bool:
    """Whether the text is made of English words (a name, a number or a code is not)."""
    text = re.sub(r"„[^“]*“", "", text)  # a quoted text is a rule's own words
    words = [w.lower() for w in re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", text)]
    if not words or text in ALLOWED or any(re.search(p, text) for p in ALLOWED_PATTERNS):
        return False
    if re.fullmatch(r"[\w*.:/-]+", text) and ("." in text or "_" in text or "/" in text):
        return False  # a tool (mail.send), a file or a path: code, not words
    if re.search("[äöüß]", text.lower()) or any(w in GERMAN for w in words):
        return False
    return any(w in ENGLISH for w in words)


async def english_left(page: Any) -> list[tuple[str, str, str]]:
    """The texts on the page that are English although the page is German: (kind, text, where)."""
    seen: set[tuple[str, str]] = set()
    left = []
    for kind, text, where in await page.evaluate(COLLECT):
        if (kind, text) in seen or not looks_english(text):
            continue
        seen.add((kind, text))
        left.append((kind, text, where))
    return left


def executable() -> str | None:
    found = os.environ.get("STARLIGHT_CHROMIUM") or (CHROMIUM if Path(CHROMIUM).exists() else None)
    if found is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    return found


# What the real server cannot be made to hold here: answered from this table instead.
MEMORIES = {
    "count": 3,
    "memories": [
        {"id": "m1", "topic": "Garden", "content": "Tomatoes like sun", "date": "2026-11-10",
         "source": "chat", "tags": ["garden"], "expires": "2026-12-01", "expired": False},
        {"id": "m2", "topic": "Mail", "content": "Prefers short answers", "date": "2026-10-01",
         "source": "learned", "tags": [], "expires": "2026-11-01", "expired": True},
        {"id": "m3", "topic": "Home", "content": "Lives in Köln", "date": "2026-09-01",
         "source": "you", "tags": [], "expires": None, "expired": False},
    ],
}  # fmt: skip
SKILLS = {
    "skills": [
        {"name": "weekly-report", "status": "approved", "description": "Report",
         "instructions": "1. Read", "version": 3, "uses": 1},
        {"name": "draft-note", "status": "pending", "description": "A draft",
         "instructions": "1. Write", "version": 1, "uses": 0},
    ]
}  # fmt: skip
ACTIVITY = {
    "entries": [
        {"time": "2026-11-12T10:00:00", "kind": kind, "text": text}
        for kind, text in [
            ("action", 'ran mail.send (you approved it) {"to": "anna"}'),
            ("action", "ran weather.forecast"),
            ("denied", "did not run files.delete: it asks first and you said no"),
            ("practice", 'practice mode: described mail.send {"to": "anna"}'),
            ("failed", "web.search failed: no connection"),
            ("kill switch", "web.search was stopped by the kill switch"),
            ("kill switch", "kill switch ENGAGED: the button"),
            ("kill switch", "kill switch released"),
            ("kill switch", "a request was stopped"),
            ("answer", "answered a request (3 steps, 2 tool calls)"),
            ("answer", "answered a request (1 steps)"),
            ("failed", "could not answer a request (the model did not respond)"),
            ("setting", "rule changed: mail.* -> confirm"),
            ("setting", "a device was paired (100.1.2.3)"),
            ("denied", "a wrong pairing code was tried (100.1.2.3)"),
            ("setting", "a device was removed: Pixel"),
            ("practice", "practice mode on"),
            ("setting", "the paused model was resumed"),
            ("starlight", "Starlight started"),
            ("starlight", "Starlight stopped"),
        ]
    ]
}
APPROVALS = {
    "items": [
        {"id": "a1", "title": "Send the report", "created": "2026-11-12T10:00:00",
         "runs": "mail.send"},
    ]
}  # fmt: skip


def with_rules(real: dict[str, Any]) -> dict[str, Any]:
    """The real automations answer, with four rules of the user's in it."""
    rules = [
        {"name": "morning-weather", "enabled": True, "next": "2026-11-13 07:30",
         "does": "every day at 07:30: run weather.forecast", "may_run_without_asking": []},
        {"name": "usb-backup", "enabled": True, "may_run_without_asking": ["backup.run"],
         "does": "when usb.inserted happens: run backup.run"},
        {"name": "old-one", "enabled": False,
         "does": 'Monday at 09:00: tell you "Plan the week"', "may_run_without_asking": []},
        {"name": "broken", "enabled": True, "problem": "no schedule",
         "does": '"every week at 9:00" (does not run until the schedule is fixed: no schedule)',
         "may_run_without_asking": []},
    ]  # fmt: skip
    return real | {"rules": rules}


OVERRIDES: dict[str, Any] = {
    "/api/automations": with_rules,
    "/api/memory": MEMORIES,
    "/api/skills": SKILLS,
    "/api/activity": ACTIVITY,
    "/api/approvals": APPROVALS,
}


@asynccontextmanager
async def german_page(
    site: str,
    app: Any,
    name: str,
    *,
    lang: str = "de",
    scheme: str = "dark",
    width: int = 1366,
    height: int = 900,
    touch: bool = False,
    init: str = "",
    overrides: dict[str, Any] | None = None,
    **context: Any,
) -> AsyncIterator[Any]:
    """A page in Chromium. /api/... goes to the real app, except what ``overrides`` (or the
    table above) holds; ``init`` runs before the page's own scripts (the chat's stand-ins)."""
    async_api = pytest.importorskip("playwright.async_api")
    path = executable()
    table = OVERRIDES | (overrides or {})

    async def backend(route: Any, request: Any) -> None:
        path_ = "/" + request.url.split("/", 3)[3]
        bare = path_.split("?")[0]
        if request.method == "GET" and bare in table and not callable(table[bare]):
            await route.fulfill(
                status=200, body=json.dumps(table[bare]), content_type="application/json"
            )
            return
        headers = {**LOCAL}
        if request.headers.get("content-type"):
            headers["content-type"] = request.headers["content-type"]
        answer = app.request(
            request.method, path_, headers=headers, content=request.post_data_buffer
        )
        body = answer.content
        if request.method == "GET" and callable(table.get(bare)):
            body = json.dumps(table[bare](answer.json())).encode()
        await route.fulfill(
            status=answer.status_code,
            body=body,
            content_type=answer.headers.get("content-type", "application/json"),
        )

    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=path)
        try:
            options: dict[str, Any] = {
                "color_scheme": scheme,
                "viewport": {"width": width, "height": height},
                "locale": "de-DE" if lang == "de" else "en-US",
            }
            if touch:
                options |= {"has_touch": True, "is_mobile": True}
            page_context = await browser.new_context(**(options | context))
            page = await page_context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.add_init_script(
                f"try {{ localStorage.setItem('starlight-lang', '{lang}'); "
                f"localStorage.setItem('starlight-theme', '{scheme}'); }} catch (e) {{}}\n" + init
            )
            await page.route("**/api/**", backend)
            page.errors = errors  # type: ignore[attr-defined]
            yield page
        finally:
            await browser.close()


@pytest.fixture
def full_app(tmp_path: Path, notebook: Path) -> Any:
    """The real web app with every module of the repository (so Settings is full)."""
    from tests.test_remote import client, home

    root = home(tmp_path / "h")
    for module in (REPO / "modules").iterdir():
        target = root / "modules" / module.name
        if module.is_dir() and not module.name.startswith("_") and not target.exists():
            shutil.copytree(module, target)
    with client(root) as c:
        yield c


async def settle(page: Any) -> None:
    await page.wait_for_load_state("networkidle")


async def open_all(page: Any) -> None:
    await page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")


# Each scenario opens one page and yields a label at every state worth reading.
Scenario = AsyncIterator[str]


async def settings_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/settings.html")
    await page.wait_for_selector("details.mod")
    await page.wait_for_selector("#safety .srow")
    await open_all(page)
    yield "settings"
    for level in ("strict", "relaxed"):
        await page.locator(f"#levels button.{level}").click()
        if level == "relaxed":  # (a looser level asks first)
            await page.wait_for_selector("dialog.ask[open]")
            yield "settings relaxed asks"
            await page.locator("dialog.ask[open] button[type=submit]").click()
        await settle(page)
        yield f"settings {level}"
    await page.locator("#filter").fill("zzzz")
    yield "settings nothing found"
    await page.locator("#filter").fill("")
    await page.locator("#pair").click()
    await page.wait_for_selector("#code:not(:empty)")
    yield "settings pairing code"
    await page.locator("#practice").evaluate("b => b.click()")
    yield "settings practice"
    # a protection switched off asks first (and its dialog is read in German too)
    await page.locator("#safety .switch input:checked").first.evaluate("b => b.click()")
    await page.wait_for_selector("dialog.ask[open]")
    yield "settings ask"
    await page.locator("dialog.ask[open] button[type=button]").click()


async def settings_devices(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/settings.html")
    await page.wait_for_selector("#remote-state:not(:empty)")
    yield "settings devices"
    if await page.locator("#devices button").count():
        await page.locator("#devices button").first.click()
        await page.wait_for_selector("dialog.ask[open]")
        yield "settings remove device"
        await page.locator("dialog.ask[open] button[type=button]").click()


async def automations_form(page: Any) -> Scenario:
    await page.locator('input[name="trigger"][value="on"]').check()
    yield "automations on"
    await page.locator("#add-if").click()
    await page.locator("#if-mode").click()
    yield "automations written condition"
    await page.locator("#add-step").click()
    await page.locator("#name").fill("my-rule")
    await page.locator("#check").click()
    await settle(page)
    yield "automations check"


async def memory_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/memory.html")
    await page.wait_for_selector("#memories > *:not(.skeleton)")
    await settle(page)
    yield "memory"
    await open_all(page)
    yield "memory open"
    for view in await page.locator("#views button").all():
        await view.click()
        await settle(page)
        yield "memory view"
    async for label in memory_more(page):
        yield label


async def activity_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/activity.html")
    await settle(page)
    yield "activity"


async def automations_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/automations.html")
    await page.wait_for_selector(".rule")  # (the rules, the templates and the events come together)
    await page.wait_for_selector("#templates > *")
    await page.wait_for_selector("#on option", state="attached")
    await settle(page)
    yield "automations"
    await open_all(page)
    yield "automations open"
    async for label in automations_form(page):
        yield label


async def memory_more(page: Any) -> Scenario:
    await page.locator("#search").fill("zzzz")
    await settle(page)
    yield "memory nothing found"


async def kill_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/kill.html")
    await settle(page)
    yield "kill"


async def pair_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/pair.html")
    await settle(page)
    yield "pair"
    await page.locator("#go").click()
    # (the answer comes from the server: read the page only after it has arrived, not
    # before; on a slow machine "network idle" alone can come too early)
    await page.wait_for_function("() => document.getElementById('msg').textContent !== ''")
    await settle(page)
    yield "pair after a try"


async def chat_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/chat.html")
    await page.wait_for_selector(".conv")
    await page.evaluate("() => { if (!window.sockets.length) connect(); }")
    await page.wait_for_function("() => window.sockets.length > 0")
    yield "chat"
    await page.evaluate("() => addMsg('you', 'Hello')")
    await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
    await page.evaluate(SEND, {"type": "tool", "tool": "mail.read", "state": "running"})
    await page.evaluate(SEND, {"type": "tool", "tool": "mail.read", "state": "done", "ms": 840})
    await page.evaluate(SEND, {"type": "thought", "text": "I will look."})
    await page.evaluate(SEND, {"type": "token", "text": "Here is"})
    await page.evaluate(
        SEND,
        {
            "type": "answer",
            "text": "Here is the news.",
            "conversation": "c1",
            "stats": {"seconds": 2.4, "tokens": 120, "context_tokens": 9000},
        },
    )
    yield "chat answer"
    await page.evaluate(SEND, {"type": "confirm", "id": "1", "question": "Send the mail?"})
    await page.evaluate(SEND, {"type": "prompt", "id": "2", "question": "Your name?"})
    await page.evaluate(SEND, {"type": "notify", "text": "A reminder"})
    await page.evaluate(SEND, {"type": "busy"})
    await page.evaluate(SEND, {"type": "stopped"})
    await page.evaluate(SEND, {"type": "error", "text": "It broke"})
    yield "chat cards"
    await page.locator("#title").click()
    yield "chat title menu"
    await page.keyboard.press("Escape")
    await page.keyboard.press("Control+/")
    yield "chat shortcuts"
    await page.keyboard.press("Escape")
    await page.keyboard.press("Control+k")
    yield "chat quick switcher"
    await page.keyboard.press("Escape")
    await page.locator("#newprivate").click()
    yield "chat private"
    await page.locator(".conv").first.hover()
    yield "chat hover"
    await page.locator("#input").fill("/")
    yield "chat slash"
    await page.locator("#input").fill("/help")
    await page.keyboard.press("Enter")
    yield "chat help"


async def chat_status_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/chat.html")
    await page.wait_for_selector(".conv")
    await page.wait_for_function("() => !document.getElementById('waiting').hidden")
    yield "chat status"
    await page.evaluate("() => { window.fetch = async () => { throw new Error('down'); }; }")
    await page.evaluate("() => { refresh(); refresh(); }")
    await page.wait_for_function("() => !document.getElementById('offline').hidden")
    yield "chat offline"


async def notes_states(page: Any, site: str) -> Scenario:
    await page.goto(f"{site}/notes.html")
    await page.wait_for_selector("#folders details")
    await page.wait_for_selector("h1.title, .hello")
    yield "notes welcome"
    await page.goto(f"{site}/notes.html?path=Notes%2FShopping.md")
    await page.wait_for_selector("h1.title")
    yield "notes note"
    await page.locator("#edit-toggle").click()
    await page.wait_for_selector(".cm-content")
    yield "notes editing"
    await page.keyboard.press("Control+e")
    for view in ("today", "inbox", "tasks", "graph", "boards", "cards", "trash"):
        await page.locator(f'.quick [data-view="{view}"]').click()
        await settle(page)
        yield f"notes {view}"
    await page.locator("#search").fill("milk")
    await settle(page)
    yield "notes search"
    await page.locator("#search").fill("")
    await page.locator("#find").click()
    yield "notes find"
    await page.keyboard.press("Escape")
    await page.goto(f"{site}/notes.html?tag=home")
    await settle(page)
    yield "notes tag"


DEVICES = {
    "remote": {"enabled": True, "hosts": ["my-pc.tail1234.ts.net"]},
    "devices": [{"id": "d1", "name": "Pixel", "paired": "2026-10-20", "last_seen": ""}],
}
REMOTE_OFF = {"remote": {"enabled": False, "hosts": []}, "devices": []}
# name -> (the states, what the table answers instead of the app, the page's own init script)
BUSY = APP.replace('"kill": {"engaged": false}', '"kill": {"engaged": true}').replace(
    '"access": {}',
    '"access": {"servers": ["nas"], "login_sites": ["a.example"], "secrets": ["key"]},'
    ' "practice": true, "paused": "the game", "waiting": 2, "saves_files": "C:/Docs/Starlight"',
)
SCENARIOS: dict[str, tuple[Any, dict[str, Any], str]] = {
    "settings": (settings_states, {}, ""),
    "settings-devices": (settings_devices, {"/api/devices": DEVICES}, ""),
    "settings-remote-off": (settings_devices, {"/api/devices": REMOTE_OFF}, ""),
    "memory": (memory_states, {}, ""),
    "activity": (activity_states, {}, ""),
    "automations": (automations_states, {}, ""),
    "kill": (kill_states, {}, ""),
    "pair": (pair_states, {}, ""),
    "chat": (chat_states, {}, APP),
    "chat-status": (chat_status_states, {}, BUSY),
    "notes": (notes_states, {}, ""),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_no_page_shows_an_english_text_in_german(
    name: str, static_site: str, full_app: Any
) -> None:
    states, overrides, init = SCENARIOS[name]
    async with german_page(static_site, full_app, name, init=init, overrides=overrides) as page:
        found = []
        async for label in states(page, static_site):
            found += [(label, *row) for row in await english_left(page)]
        assert page.errors == []  # type: ignore[attr-defined]
    assert not found, "\n".join(map(str, found))


# What a page says in a toast, a dialog or a tooltip that it sets later: one sample of each.
TEXTS = [
    "pairing is for your other devices",
    "wrong page",
    "wrong or expired code (or too many tries)",
    "Choose this level?",
    "Delete the skill weekly-report?",
    "Leave practice mode?",
    "Remove Pixel?",
    "It can no longer use Starlight.",
    "Restart Starlight now?",
    "Switch this off?",
    "Turn it on?",
    "Delete the automation 'usb-backup'?",
    "Let mail.send run without asking you first?",
    "Save this automation?",
    "Could not import it: bad file",
    "Dismissed",
    "Only at the PC: no",
    "Saved",
    "Saved: usb-backup (next 2026-11-13 07:30)",
    "The restore was cancelled.",
    "weekly v3 added as a draft: read it, then approve it",
    "Installed de-kerstin (63 MB)",
    "Done: Send the report",
    "It did not work: no",
    "'usb-backup' ran",
    "'usb-backup' did not finish: see Activity",
    "Practice mode on",
    "Practice mode off",
    "mail is on",
    "mail could not start",
    "mail is off",
    "Not listening any more",
    "Practice mode is off",
    "Practice mode: actions are described, not done",
    "Unpinned",
    "Pinned to the top",
    "No command /x: type / to see them",
    "Turn it off at the PC: no",
    "The model is loading again (about a minute)",
    "It was not paused",
    "Could not add a.txt",
    "The kill switch is on: Starlight does nothing until you resume",
    "Release the kill switch on the PC",
    "Game mode (the game): the model is off the graphics card. Press to bring it back now",
    "2 waiting for your OK",
    "Asked the PC to wake up; give it a minute",
    "not run",
    "practice mode: described, not run",
    "running …",
    "failed",
    "stopped",
    "done",
    "Working on it … 5 s",
    "Thought for 3 s",
    "Not changed: the earlier question and answer stay",
    "Stopped; the earlier answer stays",
    "Stop this answer (Esc)",
    "Send (Enter)",
    "Wait until this answer is finished",
    "Only a saved answer can be a branch point",
    "A new branch from there; the original stays as it was",
    "Only the latest answer can be regenerated",
    "Nothing to ask again",
    "Connection lost: the answer was interrupted.",
    "Kill switch engaged. Press Resume on the PC to continue.",
    "Starlight is still working on the last message.",
    "[stopped]",
    "Forgot 2 memories",
    "Forgot 1 memory",
    "Allowed for a while",
    "mail.*: 3 more min",
    "Remote access is on at https://my-pc.tail1234.ts.net (through Tailscale). Pair a device "
    "with a code, then open that address on it. No devices are paired yet.",
    "never",
    "This automation: every day at 07:30: run weather.forecast",
    "This automation: when usb.inserted happens: run backup.run — it may run backup.run "
    "without asking each time.",
    "the arguments of mail.send are not valid JSON",
    "an event is about to start",
    "sensors.battery_low — the battery is low, on battery",
]


async def test_what_pages_say_later_is_translated(static_site: str, full_app: Any) -> None:
    async with german_page(static_site, full_app, "pair") as page:
        await page.goto(f"{static_site}/pair.html")
        await page.wait_for_load_state("load")
        same = await page.evaluate("t => t.filter(x => window.starlightTranslate(x) === x)", TEXTS)
    assert same == []


def test_the_server_gives_every_protection_its_german() -> None:
    from starlight.core import safety

    for switch in safety.SWITCHES:
        german = safety.GERMAN[switch.key]
        assert german[0] and german[1], switch.key
        assert bool(german[2]) == bool(switch.warning), switch.key
        assert switch.unit in ("", "seconds", "minutes")  # (the page translates these)


async def test_an_automation_is_described_in_german(static_site: str, full_app: Any) -> None:
    async with german_page(static_site, full_app, "pair") as page:
        await page.goto(f"{static_site}/pair.html")
        said = await page.evaluate(
            "t => t.map(x => window.starlightTranslate(x))",
            [
                "every day at 07:30: run weather.forecast",
                "weekdays at 07:30: run briefing.send",
                'every 2 hours: ask "Run all my backups."',
                'monthly on day 1 at 10:00: run memory.duplicates; then tell you "Hi" '
                "(only if count > 0)",
                'Monday, Friday at 09:00: run certs.expiring {"days": 30}',
                'when sensors.battery_low happens: tell you "Plug in."',
            ],
        )
    assert said == [
        "jeden Tag um 07:30: weather.forecast ausführen",
        "werktags um 07:30: briefing.send ausführen",
        "alle 2 Stunden: Starlight fragen „Run all my backups.“",
        "monatlich am 1. um 10:00: memory.duplicates ausführen; dann dir sagen „Hi“ "
        "(nur wenn count > 0)",
        'Montag, Freitag um 09:00: certs.expiring ausführen {"days": 30}',
        "wenn sensors.battery_low passiert: dir sagen „Plug in.“",
    ]
