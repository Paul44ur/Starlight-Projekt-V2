"""Pages that work on their own, to keep or to print as a PDF: a conversation (the chat's
export) and a note (docs/plan-notes-app.md, block G1). Each is one HTML file with the page's
own safe Markdown renderer inside, and nothing it needs from a server or the network: a
note's pictures come along inside the file, and links between notes become plain text.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

STATIC = Path(__file__).with_name("static")


def static_text(name: str) -> str:
    """A file of the web pages (markdown.js, render.js ...)."""
    return (STATIC / name).read_text("utf-8")


EXPORT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<style>
  body { font: 15px/1.55 system-ui, "Segoe UI", sans-serif; color: #1f2328; background: #fff;
         max-width: 820px; margin: 24px auto; padding: 0 16px; }
  h1 { font-size: 22px; margin-bottom: 2px; } .when { color: #5a6372; font-size: 13px; }
  .msg { border: 1px solid #d8dce3; border-radius: 10px; padding: 10px 14px; margin: 12px 0;
         break-inside: avoid; }
  .msg.you { background: #eef4ff; } .who { font-size: 11px; text-transform: uppercase;
         letter-spacing: .05em; color: #5a6372; margin-bottom: 4px; }
  .you .body { white-space: pre-wrap; }
  pre { background: #f6f7f9; border-radius: 6px; padding: 8px 10px; overflow-x: auto; }
  code { font-family: ui-monospace, Consolas, monospace; font-size: .92em; }
  .md-code-bar { font-size: 12px; color: #5a6372; } .md-code-bar button { display: none; }
  table { border-collapse: collapse; } th, td { border: 1px solid #d8dce3; padding: 3px 7px; }
  blockquote { border-left: 3px solid #d8dce3; margin: 0; padding-left: 10px; color: #5a6372; }
  .tok-k { color: #8250df; } .tok-s { color: #116329; } .tok-n { color: #b35900; }
  .tok-c { color: #6e7781; font-style: italic; } .tok-f { color: #0550ae; }
  .tok-v { color: #953800; }
  .math { font-family: "Cambria Math", "STIX Two Math", Georgia, serif; }
  .math-block { display: block; text-align: center; margin: .4em 0; }
  .bar { position: sticky; top: 0; background: #fff; padding: 6px 0; text-align: right; }
  @media print { .bar { display: none; } body { margin: 0 auto; } }
</style></head><body>
<div class="bar"><button onclick="print()">Print or save as PDF</button></div>
<h1 id="title"></h1><div class="when" id="when"></div><div id="log"></div>
<script type="application/json" id="data">__DATA__</script>
<script>
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text; return e; }
function toast() {}
</script>
<script>__MARKDOWN__</script>
<script>
const data = JSON.parse(document.getElementById("data").textContent);
document.getElementById("title").textContent = data.title;
document.getElementById("when").textContent = (data.created || "").slice(0, 16).replace("T", " ");
for (const m of data.messages) {
  const box = el("div", "msg " + (m.role === "user" ? "you" : "bot"));
  box.appendChild(el("div", "who", m.role === "user" ? "You" : "Starlight"));
  const body = box.appendChild(el("div", "body"));
  if (m.role === "user") body.textContent = m.content; else renderMarkdown(body, m.content);
  document.getElementById("log").appendChild(box);
}
if (data.print) setTimeout(() => print(), 300);
</script></body></html>
"""


def export_html(data: dict[str, Any], markdown_js: str, print_now: bool = False) -> str:
    """A conversation as one HTML file that shows it on its own (no server, no network):
    the answers drawn from their Markdown by the chat's own safe renderer."""
    payload = {
        "title": data.get("title") or "Conversation",
        "created": data.get("created") or "",
        "messages": [
            {"role": m.get("role"), "content": str(m.get("content", ""))}
            for m in data.get("messages", [])
        ],
        "print": print_now,
    }
    # "</" would end the script element early: a message cannot close it
    embedded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    parts = {
        "__TITLE__": html.escape(str(payload["title"])),
        "__MARKDOWN__": markdown_js.replace("</script", "<\\/script"),
        "__DATA__": embedded,
    }  # in one pass: a title or message that says "__DATA__" stays as it is
    return re.sub("__(?:TITLE|MARKDOWN|DATA)__", lambda m: parts[m.group(0)], EXPORT_PAGE)


NOTE_PAGE = """<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<style>
  body { font: 15px/1.6 system-ui, "Segoe UI", sans-serif; color: #1f2328; background: #fff;
         max-width: 760px; margin: 24px auto; padding: 0 16px; }
  h1.title { font-size: 26px; margin-bottom: 2px; } .when { color: #5a6372; font-size: 13px; }
  .props { color: #5a6372; font-size: 13px; margin: 6px 0 14px; }
  pre { background: #f6f7f9; border-radius: 6px; padding: 8px 10px; overflow-x: auto; }
  code { font-family: ui-monospace, Consolas, monospace; font-size: .92em; }
  .md-code-bar { font-size: 12px; color: #5a6372; } .md-code-bar button { display: none; }
  table { border-collapse: collapse; } th, td { border: 1px solid #d8dce3; padding: 3px 7px; }
  blockquote { border-left: 3px solid #d8dce3; margin: 0; padding-left: 10px; color: #5a6372; }
  .callout { border-left: 4px solid #4c8bf5; background: #f3f7ff; border-radius: 6px;
             padding: 6px 12px; margin: .6em 0; }
  .callout-title { font-weight: 600; } .callout-icon { margin-right: 6px; }
  .note-picture { max-width: 100%; height: auto; }
  .note-link { color: inherit; border-bottom: 1px dotted #8a8fa0; }
  .tag { color: #0047b3; }
  li.task { list-style: none; } li.task > .task-box { margin: 0 6px 0 -1.3em; }
  li.task[data-status="done"] { color: #5a6372; text-decoration: line-through; }
  .task-chip { display: inline-block; margin-left: .4em; padding: 0 .5em; border: 1px solid #d8dce3;
               border-radius: 999px; font-size: .8em; color: #5a6372; text-decoration: none; }
  .tok-k { color: #8250df; } .tok-s { color: #116329; } .tok-n { color: #b35900; }
  .tok-c { color: #6e7781; font-style: italic; } .tok-f { color: #0550ae; }
  .tok-v { color: #953800; }
  .math { font-family: "Cambria Math", "STIX Two Math", Georgia, serif; }
  .math-block { display: block; text-align: center; margin: .4em 0; }
  .bar { position: sticky; top: 0; background: #fff; padding: 6px 0; text-align: right; }
  @media print { .bar { display: none; } body { margin: 0 auto; } a { color: inherit; } }
</style></head><body>
<div class="bar"><button onclick="print()">__PRINT__</button></div>
<h1 class="title" id="title"></h1><div class="when" id="when"></div>
<div class="props" id="props"></div><div class="md" id="note"></div>
<script type="application/json" id="data">__DATA__</script>
<script>
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text; return e; }
function toast() {}
</script>
<script>__MARKDOWN__</script>
<script>__RENDER__</script>
<script>
const data = JSON.parse(document.getElementById("data").textContent);
document.getElementById("title").textContent = data.title;
document.getElementById("when").textContent = data.when;
document.getElementById("props").textContent = data.properties;
const body = document.getElementById("note");
renderMarkdown(body, data.text);
// on their own there is no Notes page: links between notes and tags become plain text,
// and pictures come from inside this file (or are left out)
for (const a of body.querySelectorAll("a.note-link, a.tag")) {
  const span = el("span", a.className, a.textContent);
  a.replaceWith(span);
}
for (const img of body.querySelectorAll("img.note-picture")) {
  const path = decodeURIComponent((img.getAttribute("src") || "").replace("/api/notes/file/", ""));
  if (data.pictures[path]) img.src = data.pictures[path]; else img.remove();
}
if (data.print) setTimeout(() => print(), 300);
</script></body></html>
"""


def note_html(
    title: str,
    text: str,
    *,
    when: str = "",
    properties: str = "",
    pictures: dict[str, str] | None = None,
    print_now: bool = False,
    german: bool = False,
) -> str:
    """A note as one HTML file that shows it on its own (no server, no network). ``text`` is
    its Markdown without the properties block; ``pictures`` maps a picture's path in the
    Starlight folder to its data: address."""
    from starlight.notebook.export import without_title

    payload = {
        "title": title, "text": without_title(text, title), "when": when, "properties": properties,
        "pictures": pictures or {}, "print": print_now,
    }  # fmt: skip
    embedded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    parts = {
        "__TITLE__": html.escape(title),
        "__LANG__": "de" if german else "en",
        "__PRINT__": "Drucken oder als PDF speichern" if german else "Print or save as PDF",
        "__MARKDOWN__": static_text("markdown.js").replace("</script", "<\\/script"),
        "__RENDER__": static_text("render.js").replace("</script", "<\\/script"),
        "__DATA__": embedded,
    }  # in one pass: a note that says "__DATA__" stays as it is
    return re.sub(
        "__(?:TITLE|LANG|PRINT|MARKDOWN|RENDER|DATA)__", lambda m: parts[m.group(0)], NOTE_PAGE
    )
