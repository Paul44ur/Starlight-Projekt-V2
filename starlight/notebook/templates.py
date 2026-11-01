"""Templates: notes that start from a good layout (docs/plan-notes-app.md, block C4).

A template is an ordinary note in the Templates folder of the Starlight folder. Starlight
ships three (Meeting, Daily, Project), in German or English, copied there the first time
templates are used; change them or add your own. The journal starts each new day from
Daily.md.

Variables, replaced when a note is made:
    {{title}}            the new note's title
    {{date}}             today, 2026-11-01
    {{time}}             now, 14:30
    {{date:FORMAT}}      today in your format: YYYY, YY, MMMM (November), MMM (Nov), MM, M,
                         dddd (Sunday), ddd (Sun), DD, D; in the template's language
    {{yesterday}}        a link to yesterday's journal day, [[2026-10-31]]
    {{events}}           today's calendar      (the calendar module)
    {{todo}}             what is due today      (the to-do module)
    {{weather}}          the weather now        (the weather module)
The last three are empty when their module is off or does not answer. Anything else in
{{ }} stays as written.
"""

from __future__ import annotations

import datetime as dt
import locale
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

FOLDER = "Templates"
VARIABLE = re.compile(r"\{\{\s*([a-z]+)\s*(?::\s*([^}]*?)\s*)?\}\}")
TOKEN = re.compile(r"YYYY|YY|MMMM|MMM|MM|M|dddd|ddd|DD|D")

WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
}  # fmt: skip

# what fills {{events}}, {{todo}} and {{weather}}: a module's tool and its arguments
FROM_MODULES_SAFE = {"calendar", "todo", "weather"}  # (only these read-only tools are called)
FROM_MODULES: dict[str, tuple[str, dict[str, Any]]] = {
    "events": ("calendar.agenda", {"days": 1}),
    "todo": ("todo.due", {"days": 0}),
    "weather": ("weather.now", {}),
}

SHIPPED = {
    "en": {
        "Meeting": "---\ntags: [meeting]\ndate: {{date}}\n---\n# {{title}}\n\n"
        "**When:** {{date:dddd, D MMMM YYYY}}, {{time}}\n**Who:** \n\n"
        "## Topics\n- \n\n## Decisions\n- \n\n## To do\n- [ ] \n",
        "Daily": "# {{date:dddd, D MMMM YYYY}}\n\n## Today\n{{events}}\n\n## Due\n{{todo}}\n\n"
        "## Notes\n",
        "Project": "---\ntags: [project]\nstatus: open\nstarted: {{date}}\n---\n# {{title}}\n\n"
        "## Goal\n\n## Next steps\n- [ ] \n\n## Notes\n",
    },
    "de": {
        "Meeting": "---\ntags: [besprechung]\ndate: {{date}}\n---\n# {{title}}\n\n"
        "**Wann:** {{date:dddd, D. MMMM YYYY}}, {{time}}\n**Wer:** \n\n"
        "## Themen\n- \n\n## Entscheidungen\n- \n\n## Zu erledigen\n- [ ] \n",
        "Daily": "# {{date:dddd, D. MMMM YYYY}}\n\n## Heute\n{{events}}\n\n## Fällig\n{{todo}}\n\n"
        "## Notizen\n",
        "Project": "---\ntags: [projekt]\nstatus: offen\nstarted: {{date}}\n---\n# {{title}}\n\n"
        "## Ziel\n\n## Nächste Schritte\n- [ ] \n\n## Notizen\n",
    },
}


def system_language() -> str:
    """ "de" when this PC speaks German (Windows: "German_Germany"; elsewhere "de_DE"),
    else "en"."""
    try:
        name = (locale.getlocale()[0] or "").lower()
    except ValueError:
        name = ""
    return "de" if name.startswith(("de", "german")) else "en"


def language(lang: str | None) -> str:
    return "de" if (lang or "").lower().startswith("de") else "en"


def ensure(root: Path, lang: str | None = None) -> Path:
    """The Templates folder; the first time, Starlight's own templates are put in it (in
    ``lang``, else the PC's language). Templates already there are never overwritten."""
    folder = root / FOLDER
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in SHIPPED[language(lang or system_language())].items():
            path = folder / f"{name}.md"
            if not path.exists():
                path.write_text(text, encoding="utf-8")
    return folder


def names(root: Path, lang: str | None = None) -> list[str]:
    """The templates there are (A to Z)."""
    folder = ensure(root, lang)
    return sorted((p.stem for p in folder.glob("*.md") if p.is_file()), key=str.casefold)


def find(root: Path, name: str, lang: str | None = None) -> Path | None:
    """The template called ``name`` (without case), or None."""
    wanted = name.strip().removesuffix(".md").casefold()
    for path in ensure(root, lang).glob("*.md"):
        if path.stem.casefold() == wanted and path.is_file():
            return path
    return None


def format_date(when: dt.datetime | dt.date, pattern: str, lang: str = "en") -> str:
    """``when`` written in ``pattern`` (the tokens in the module's notes)."""
    words = language(lang)

    def token(match: re.Match[str]) -> str:
        part = match.group(0)
        return {
            "YYYY": f"{when.year:04d}",
            "YY": f"{when.year % 100:02d}",
            "MMMM": MONTHS[words][when.month - 1],
            "MMM": MONTHS[words][when.month - 1][:3],
            "MM": f"{when.month:02d}",
            "M": str(when.month),
            "dddd": WEEKDAYS[words][when.weekday()],
            "ddd": WEEKDAYS[words][when.weekday()][: 2 if words == "de" else 3],
            "DD": f"{when.day:02d}",
            "D": str(when.day),
        }[part]

    return TOKEN.sub(token, pattern)


def _events(output: dict[str, Any], lang: str) -> str:
    lines = []
    for event in output.get("events") or []:
        if event.get("all_day"):
            when = "ganztägig" if lang == "de" else "all day"
        else:
            when = f"{str(event.get('start', ''))[-5:]}\u2013{event.get('end', '')}"
        place = f" ({event['location']})" if event.get("location") else ""
        lines.append(f"- {when} {event.get('title', '')}{place}")
    return "\n".join(lines)


def _todo(output: dict[str, Any], lang: str) -> str:
    lines = []
    for kind in ("overdue", "due"):
        for task in output.get(kind) or []:
            late = (" (überfällig)" if lang == "de" else " (overdue)") if kind == "overdue" else ""
            lines.append(f"- {task.get('text', '')}{late}")
    return "\n".join(lines)


def _weather(output: dict[str, Any], lang: str) -> str:
    summary = str(output.get("summary") or "")
    place = str(output.get("place") or "")
    return f"{place}: {summary}" if place and summary else summary


FORMATTERS = {"events": _events, "todo": _todo, "weather": _weather}

Caller = Callable[[str, dict[str, Any]], Awaitable[Any]]


async def render(
    text: str,
    *,
    title: str = "",
    lang: str | None = None,
    now: dt.datetime | None = None,
    call: Caller | None = None,
) -> str:
    """The template's text with its variables filled in. ``call(tool, args)`` runs a
    module's tool and gives its output (None when the module is off or failed)."""
    words = language(lang)
    when = now or dt.datetime.now()
    wanted = {m.group(1) for m in VARIABLE.finditer(text)}
    filled: dict[str, str] = {}
    for name in wanted & set(FROM_MODULES):
        output = None
        if call is not None:
            tool, args = FROM_MODULES[name]
            try:
                output = await call(tool, args)
            except Exception:  # a module that fails leaves its place empty
                output = None
        filled[name] = FORMATTERS[name](output, words) if isinstance(output, dict) else ""

    def replace(match: re.Match[str]) -> str:
        name, argument = match.group(1), match.group(2)
        if name == "title":
            return title
        if name == "date":
            return format_date(when, argument, words) if argument else when.date().isoformat()
        if name == "time":
            return format_date(when, argument, words) if argument else when.strftime("%H:%M")
        if name == "yesterday":
            return f"[[{(when.date() - dt.timedelta(days=1)).isoformat()}]]"
        if name in filled:
            return filled[name]
        return match.group(0)

    return VARIABLE.sub(replace, text)
