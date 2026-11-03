"""The Notes page in parts: notes.html holds the page, notes.css its look and six plain
scripts (notes-page.js ... notes-edit.js) its workings, loaded in order. The web app serves
each file the page loads, exactly as it is, and no other notes-....js."""

from __future__ import annotations

import re
from typing import Any

from starlight.web.app import NOTES_SCRIPTS, STATIC
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_remote import LOCAL


def test_the_app_serves_every_file_the_notes_page_loads(app: Any) -> None:
    html = (STATIC / "notes.html").read_text("utf-8")
    scripts = re.findall(r'<script src="/([^"]+)"></script>', html)
    sheets = re.findall(r'<link rel="stylesheet" href="/([^"]+)" />', html)
    assert scripts[-6:] == [
        "notes-page.js", "notes-tasks.js", "notes-info.js", "notes-views.js", "notes-find.js",
        "notes-edit.js",
    ]  # fmt: skip
    assert sheets == ["style.css", "notes.css"]
    assert "<style>" not in html and "<script>" not in html  # (nothing inline any more)
    for name in scripts + sheets:
        served = app.get(f"/{name}", headers=LOCAL)
        assert served.status_code == 200, name
        assert served.text == (STATIC / name).read_text("utf-8"), name
        kind = "text/css" if name.endswith(".css") else "text/javascript"
        assert served.headers["content-type"].startswith(kind), name
    assert set(scripts[-6:]) < set(NOTES_SCRIPTS)
    assert all((STATIC / name).is_file() for name in NOTES_SCRIPTS)
    assert app.get("/notes-editor.js", headers=LOCAL).status_code == 200  # (on the first Edit)
    for other in ("/notes-secret.js", "/notes-.js", "/notes-page.js.map"):
        assert app.get(other, headers=LOCAL).status_code == 404, other
