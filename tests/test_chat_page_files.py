"""The chat page in parts: chat.html holds the markup, chat.css its look and five plain
scripts (chat-core.js ... chat-page.js) its workings, loaded in order. The web app serves
each file the page loads, exactly as it is, and no other chat-....js (as for the Notes page)."""

from __future__ import annotations

import re
from pathlib import Path

from starlight.web.app import CHAT_SCRIPTS, STATIC
from tests.test_remote import LOCAL, client, home

PARTS = ["chat-core.js", "chat-messages.js", "chat-list.js", "chat-input.js", "chat-page.js"]


def test_the_app_serves_every_file_the_chat_page_loads(tmp_path: Path) -> None:
    html = (STATIC / "chat.html").read_text("utf-8")
    scripts = re.findall(r'<script src="/([^"]+)"></script>', html)
    sheets = re.findall(r'<link rel="stylesheet" href="/([^"]+)" />', html)
    assert scripts == ["i18n.js", "common.js", "markdown.js", "render.js", *PARTS]
    assert sheets == ["style.css", "chat.css"]
    assert "<style" not in html and "<script>" not in html  # (nothing inline any more)
    assert tuple(PARTS) == CHAT_SCRIPTS
    assert [p.name for p in sorted(STATIC.glob("chat-*.js"))] == sorted(PARTS)  # (no stray file)
    with client(home(tmp_path / "h", remote=False)) as c:
        served_page = c.get("/", headers=LOCAL)
        assert served_page.text == html
        for name in scripts + sheets:
            served = c.get(f"/{name}", headers=LOCAL)
            assert served.status_code == 200, name
            assert served.text == (STATIC / name).read_text("utf-8"), name
            kind = "text/css" if name.endswith(".css") else "text/javascript"
            assert served.headers["content-type"].startswith(kind), name
        for other in ("/chat-secret.js", "/chat-.js", "/chat-core.js.map"):
            assert c.get(other, headers=LOCAL).status_code == 404, other
        worker = c.get("/sw.js", headers=LOCAL).text
    kept = worker.split("FILES = ")[1].split("]")[0]
    for name in [*PARTS, "chat.css"]:  # (the phone keeps the page whole for when the PC is off)
        assert f'"/{name}"' in kept, name


def test_each_part_says_what_it_is_and_the_order() -> None:
    for name in PARTS:
        top = (STATIC / name).read_text("utf-8").split("\n\n", 1)[0].replace("\n// ", " ")
        assert top.startswith("// "), name
        assert "Part of the chat page (chat.html)" in top, name
        assert ", ".join(PARTS) in top, name  # (the same order in every file)
