"""The Notes page's look (UI block U6): it takes its corners, shadows and motion from the
shared design system in style.css, and a ticked task looks the same everywhere."""

from __future__ import annotations

import re

from tests.conftest import REPO

STATIC = REPO / "starlight" / "web" / "static"


def test_the_notes_page_takes_its_corners_and_shadows_from_the_shared_scale() -> None:
    css = (STATIC / "notes.css").read_text(encoding="utf-8")
    literal = set(re.findall(r"border-radius: ?(\d+px)", css))
    assert literal <= {"3px", "4px"}, literal  # (only the small marks: a highlight, a key)
    assert "var(--shadow)" not in css  # (the lifted ones are --shadow-2)
    assert not re.search(r"transition:[^;]*\d+(\.\d+)?m?s", css)  # durations from --dur


def test_a_ticked_task_is_green_everywhere() -> None:
    editor = (STATIC / "notes-editor.js").read_text(encoding="utf-8")
    box = re.search(r'"\.cm-task-box": \{(.*?)\}', editor, re.S)
    assert box and 'accentColor: "var(--ok)"' in box.group(1)  # the editor
    notes = (STATIC / "notes.css").read_text(encoding="utf-8")
    assert re.search(r"\.task-row \.task-box \{[^}]*accent-color: var\(--ok\)", notes)  # Tasks
    shared = (STATIC / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.md li\.task > \.task-box \{[^}]*accent-color: var\(--ok\)", shared)
