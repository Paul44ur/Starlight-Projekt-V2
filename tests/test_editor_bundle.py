"""Block B1 of the notes plan (docs/plan-notes-app.md): the editor for the Notes page ships
as one built file, static/vendor/editor.js (CodeMirror 6). These tests check that the
shipped file is the one the build made, that its licences are included, and that it works
in a browser."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_page import CHROMIUM
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_web_chat import LOCAL, client, web_home

VENDOR = REPO / "starlight" / "web" / "static" / "vendor"
BUILD = REPO / "bundles" / "editor"


def test_the_shipped_editor_is_the_one_the_build_made() -> None:
    data = (VENDOR / "editor.js").read_bytes()
    recorded = (VENDOR / "editor.sha256").read_text("utf-8").split()
    assert recorded == [hashlib.sha256(data).hexdigest(), "editor.js"]
    assert b"\r\n" not in data  # (git must not change its line endings: .gitattributes)
    attributes = (REPO / ".gitattributes").read_text("utf-8")
    assert "starlight/web/static/vendor/** -text" in attributes
    assert 400_000 < len(data) < 900_000  # about 625 KB: a big change means a different set


def test_versions_are_pinned_and_every_bundled_package_has_its_licence() -> None:
    package = json.loads((BUILD / "package.json").read_text("utf-8"))
    pinned = {**package["dependencies"], **package["devDependencies"]}
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in pinned.values()), pinned
    lock = json.loads((BUILD / "package-lock.json").read_text("utf-8"))["packages"]
    licences = (VENDOR / "LICENSES.txt").read_text("utf-8")
    for name in package["dependencies"]:
        version = lock[f"node_modules/{name}"]["version"]
        assert version == pinned[name], name  # the lock agrees with package.json
        assert f"\n{name} {version} (MIT)\n" in licences, name
    listed = re.findall(r"^(\S+) (\d+\.\d+\.\d+) \((\S+)\)$", licences, re.MULTILINE)
    assert len(listed) >= 10
    for name, version, kind in listed:  # each one: the version in the lock, MIT
        assert lock[f"node_modules/{name}"]["version"] == version, name
        assert kind == "MIT", name
    assert licences.count("Permission is hereby granted, free of charge") == len(listed)


def test_the_web_app_serves_only_the_listed_vendor_files(tmp_path: Path) -> None:
    with client(web_home(tmp_path / "h")) as c:  # type: ignore[attr-defined]
        editor = c.get("/vendor/editor.js", headers=LOCAL)
        assert editor.status_code == 200
        assert editor.headers["content-type"].startswith("text/javascript")
        assert editor.headers["x-content-type-options"] == "nosniff"
        assert hashlib.sha256(editor.content).hexdigest() in (VENDOR / "editor.sha256").read_text()
        assert c.get("/vendor/LICENSES.txt", headers=LOCAL).status_code == 200
        for other in ("editor.sha256", "../app.py", "..%2Fapp.py", "nothing.js"):
            assert c.get(f"/vendor/{other}", headers=LOCAL).status_code == 404, other


async def test_the_editor_loads_in_a_browser_and_edits_markdown(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        try:
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(f"{static_site}/pair.html")
            result: Any = await page.evaluate(
                """async () => {
                  const cm = await import('/vendor/editor.js');
                  const parent = document.createElement('div');
                  document.body.appendChild(parent);
                  const view = new cm.EditorView({
                    parent,
                    state: cm.EditorState.create({
                      doc: '# Shopping\\n\\n- [ ] milk',
                      extensions: [
                        cm.history(), cm.markdown({base: cm.markdownLanguage}),
                        cm.syntaxHighlighting(cm.defaultHighlightStyle),
                        cm.keymap.of([...cm.defaultKeymap, ...cm.historyKeymap]),
                        cm.EditorView.lineWrapping,
                      ],
                    }),
                  });
                  view.dispatch({changes: {from: view.state.doc.length, insert: '\\n- [ ] eggs'}});
                  const typed = view.state.doc.toString();
                  cm.undo(view);
                  const nodes = [];
                  cm.syntaxTree(view.state).iterate({enter: n => { nodes.push(n.name); }});
                  return {typed, undone: view.state.doc.toString(), nodes,
                          lines: parent.querySelectorAll('.cm-line').length,
                          exports: ['autocompletion', 'search', 'Decoration', 'ViewPlugin',
                                    'tags', 'HighlightStyle', 'StateField', 'GFM']
                            .filter(n => !(n in cm))};
                }"""
            )
        finally:
            await browser.close()
    assert errors == []
    assert result["typed"] == "# Shopping\n\n- [ ] milk\n- [ ] eggs"
    assert result["undone"] == "# Shopping\n\n- [ ] milk"  # the history works
    assert "ATXHeading1" in result["nodes"] and "Task" in result["nodes"]  # Markdown + GFM
    assert result["lines"] == 3
    assert result["exports"] == []  # what the Notes page will use is there
