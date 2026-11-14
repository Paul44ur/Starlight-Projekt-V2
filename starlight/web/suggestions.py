"""Starter suggestions for a new, empty conversation (shown by the chat page).

A fixed table of (module, text). The texts are written by us; nothing from the user's data goes
into them. Only modules that are on count. The pick turns with the day, so it is not always the
same four, but one day always gives the same four (no random numbers).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

MAX_SUGGESTIONS = 4

# (module, text). The page translates the text (starlight/web/static/i18n.js).
TABLE: tuple[tuple[str, str], ...] = (
    ("briefing", "How does my day look?"),
    ("weather", "What's the weather this week?"),
    ("reminders", "Remind me in 2 hours to …"),
    ("todo", "What is due today?"),
    ("calendar", "What is on my calendar this week?"),
    ("mail", "What came in overnight?"),
    ("notes", "Note: …"),
    ("news", "What's in the news today?"),
    ("research", "Find out whether …"),
    ("system", "How healthy is my PC?"),
    ("expenses", "How much did I spend this month?"),
    ("trains", "When is the next train to …?"),
    ("translate", "Translate this into English: …"),
)
# Needs no module: fills up when fewer than four modules give a line.
FALLBACK = ("", "What can you do?")


def pick_suggestions(on: Iterable[str], today: date) -> list[dict[str, str]]:
    """Up to four suggestions for the modules that are ``on``, turning with ``today``."""
    names = set(on)
    lines = [line for line in TABLE if line[0] in names]
    if lines:
        turn = today.toordinal() % len(lines)  # (the same day: the same start)
        lines = lines[turn:] + lines[:turn]
    chosen = lines[:MAX_SUGGESTIONS]
    if len(chosen) < MAX_SUGGESTIONS:
        chosen.append(FALLBACK)
    return [{"text": text, "module": module} for module, text in chosen]
