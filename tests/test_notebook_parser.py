"""Block B2 of the notes plan (docs/plan-notes-app.md): reading a note's Markdown
(starlight/notebook/markdown.py) and resolving links (links.py)."""

from __future__ import annotations

import pytest

from starlight.notebook import markdown
from starlight.notebook.links import Known, Resolver
from starlight.notebook.markdown import parse, remove_property, set_property

SAMPLE = """---
aliases: [Einkauf, Shop list]
tags: [home, errands/weekly]
created: 2026-10-01
# a comment that must survive edits
rating: 4
---
# Shopping #list
See [[Pantry]], [[Recipes/Pancakes|the pancakes]], ![[photo.png]] and [[Plan#Week 2]].
Also [[Plan#^goal]], [[#Week 2]] and [[Table\\|shown]] (as written in a table).
A [guide](Docs/Guide%20One.md#Intro), ![a picture](Attachments/cat.jpg) and [web](https://example.org/#nottag).
Addresses https://x.org/page#nope and www.example.org/#nope2 have no tags.
`[[NotALink]] #notatag` C# and a#b and #123 and \\#escaped and #ok-tag/sub and #Ünïcode.
- [ ] Buy milk due: 2026-11-05 priority: hoch #shop ^milk
- [x] Call Tom done: 2026-11-01
  - [ ] Ask about it repeat: every month start: 2026-12-01 and more
1. [/] Half done fällig: 2026-11-07 Priorität: niedrig
- [ ] `due: 2026-01-01` stays text
* not a task [ ] here

## Week 2
Something ^para-1

### Detail
Inside week 2.

## Week 3

```python
# not a heading [[NoLink]] #nottag
- [ ] not a task
```

~~~
```
still code #nottag2
~~~

```query
where: tag is work
sort: due
```
"""


def test_properties_are_read_and_dates_come_back_as_text() -> None:
    note = parse(SAMPLE)
    assert note.properties == {
        "aliases": ["Einkauf", "Shop list"],
        "tags": ["home", "errands/weekly"],
        "created": "2026-10-01",
        "rating": 4,
    }
    assert note.error == "" and note.aliases == ["Einkauf", "Shop list"]
    assert note.body.startswith("# Shopping #list\n") and note.body_line == 7


def test_odd_properties_blocks() -> None:
    hr = "---\nJust a line between two rules\n---\nText\n"
    assert markdown.split(hr).properties == {} and markdown.split(hr).body == hr  # not properties
    broken = markdown.split("---\ntitle: [unclosed\n---\nBody\n")
    assert broken.present and broken.properties == {} and "could not be read" in broken.error
    assert broken.body == "Body\n"
    assert markdown.split("---\n---\nBody").present  # an empty block
    assert markdown.split("﻿---\na: 1\n---\nx").properties == {"a": 1}  # Notepad's BOM
    assert markdown.split("---\r\na: 1\r\n---\r\nx\r\n").properties == {"a": 1}  # CRLF
    assert markdown.split("---\na: 1\nno end").properties == {}  # never closed: plain text
    bomb = "---\na: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n---\n"
    assert "aliases" in markdown.split(bomb).error  # (no "billion laughs")
    assert markdown.property_tags({"tags": "#one two, three"}) == ["one", "two", "three"]
    assert markdown.property_tags({"tags": ["a b"]}) == ["a b"]
    assert markdown.aliases({"alias": "Other"}) == ["Other"]


def test_headings_links_and_embeds() -> None:
    note = parse(SAMPLE)
    assert [(h.level, h.text) for h in note.headings] == [
        (1, "Shopping #list"),
        (2, "Week 2"),
        (3, "Detail"),
        (2, "Week 3"),
    ]
    found = [(link.target, link.heading, link.block, link.text, link.embed) for link in note.links]
    assert found == [
        ("Pantry", "", "", "", False),
        ("Recipes/Pancakes", "", "", "the pancakes", False),
        ("photo.png", "", "", "", True),
        ("Plan", "Week 2", "", "", False),
        ("Plan", "", "goal", "", False),
        ("", "Week 2", "", "", False),
        ("Table", "", "", "shown", False),
        ("Docs/Guide One", "Intro", "", "guide", False),
        ("Attachments/cat.jpg", "", "", "a picture", True),
    ]
    assert [link.wiki for link in note.links][-2:] == [False, False]
    assert all("NoLink" not in link.target and "NotALink" not in link.target for link in note.links)


def test_tags_skip_code_addresses_and_escapes() -> None:
    note = parse(SAMPLE)
    assert note.tag_names() == ["home", "errands/weekly", "list", "ok-tag/sub", "Ünïcode", "shop"]
    assert [t.line for t in note.tags][:2] == [-1, -1]  # from the property


def test_tasks_with_plain_word_fields() -> None:
    tasks = parse(SAMPLE).tasks
    assert [(t.status, t.text, t.indent) for t in tasks] == [
        (" ", "Buy milk #shop", 0),
        ("x", "Call Tom", 0),
        (" ", "Ask about it and more", 2),
        ("/", "Half done", 0),
        (" ", "`due: 2026-01-01` stays text", 0),
    ]
    milk, tom, ask, half, code = tasks
    assert (milk.due, milk.priority, milk.open) == ("2026-11-05", "high", True)
    assert (tom.done, tom.open) == ("2026-11-01", False)
    assert (ask.repeat, ask.start) == ("every month", "2026-12-01")
    assert (half.due, half.priority) == ("2026-11-07", "low")  # German keys
    assert code.due == ""


def test_block_ids_sections_and_query_blocks() -> None:
    note = parse(SAMPLE)
    assert set(note.block_ids) == {"milk", "para-1"}
    assert markdown.block(SAMPLE, "milk") == "- [ ] Buy milk due: 2026-11-05 priority: hoch #shop\n"
    assert markdown.block(SAMPLE, "para-1") == "Something\n"
    assert markdown.block(SAMPLE, "nothing") is None
    week = markdown.section(SAMPLE, "week 2")
    assert week is not None and week.startswith("## Week 2\n") and "Inside week 2." in week
    assert "Week 3" not in week
    assert markdown.section(SAMPLE, "Week 2#Detail") == "### Detail\nInside week 2.\n"
    assert markdown.section(SAMPLE, "Week 3#Detail") is None
    assert [b.info for b in note.code] == ["python", "", "query"]
    assert note.code[1].text == "```\nstill code #nottag2\n"  # a shorter fence does not close it
    assert [q.text for q in note.queries] == ["where: tag is work\nsort: due\n"]
    unclosed = parse("text\n```\n#nottag [[nolink]]\n")
    assert unclosed.tags == [] and unclosed.links == [] and unclosed.code[0].end == 2


def test_property_edits_change_only_their_lines() -> None:
    changed = set_property(SAMPLE, "rating", 5)
    assert changed == SAMPLE.replace("rating: 4\n", "rating: 5\n")
    listed = set_property(SAMPLE, "tags", ["a", "b"])
    assert listed == SAMPLE.replace("tags: [home, errands/weekly]\n", "tags:\n- a\n- b\n")
    assert parse(listed).properties["tags"] == ["a", "b"]
    added = set_property(SAMPLE, "due", "2026-11-05")
    assert added == SAMPLE.replace("rating: 4\n---\n", "rating: 4\ndue: 2026-11-05\n---\n")
    assert remove_property(listed, "tags") == SAMPLE.replace("tags: [home, errands/weekly]\n", "")
    assert remove_property(SAMPLE, "nothing") == SAMPLE
    assert "# a comment that must survive edits" in set_property(SAMPLE, "rating", 1)
    # no block yet: one is made; line endings and Notepad's BOM stay
    assert set_property("Hello\r\n", "status", "open") == "---\r\nstatus: open\r\n---\r\nHello\r\n"
    assert set_property("﻿Hi", "a", 1) == "﻿---\na: 1\n---\nHi"
    assert remove_property("---\nx: 1\n---\nbody\n", "x") == "body\n"  # the empty block goes
    with pytest.raises(ValueError, match="could not be read"):
        set_property("---\nbad: [\n---\n", "a", 1)
    with pytest.raises(ValueError, match="cannot be the name"):
        set_property("x", "a: b", 1)


def test_links_resolve_like_obsidian_does() -> None:
    resolver = Resolver(
        [
            Known("Notes/Shopping.md", ("Einkauf",)),
            Known("Notes/Plan.md"),
            Known("Projects/Plan.md"),
            Known("Projects/Old/Plan.md"),
            Known("Recipes/Pancakes.md"),
            Known("Notes/Attachments/photo.png"),
            Known("Notes/Einkauf.md"),
        ]
    )
    assert resolver.resolve("shopping") == "Notes/Shopping.md"  # no case
    assert resolver.resolve("Plan", "Projects/Ideas.md") == "Projects/Plan.md"  # same folder
    assert resolver.resolve("Plan", "Recipes/Pancakes.md") == "Notes/Plan.md"  # shortest, A-Z
    assert resolver.resolve("Old/Plan") == "Projects/Old/Plan.md"  # a folder in the link
    assert resolver.resolve("Projects/Plan") == "Projects/Plan.md"
    assert resolver.resolve("photo.png") == "Notes/Attachments/photo.png"
    assert resolver.resolve("Einkauf") == "Notes/Einkauf.md"  # a real name beats an alias
    assert resolver.resolve("Shop") is None
    assert resolver.resolve("", "Notes/Plan.md") == "Notes/Plan.md"  # [[#Heading]]
    aliased = Resolver([Known("Notes/Shopping.md", ("Einkauf",))])
    assert aliased.resolve("einkauf") == "Notes/Shopping.md"
    assert aliased.names_of("Notes/Shopping.md") == {"shopping.md", "shopping", "einkauf"}


def test_the_notebook_can_be_imported_first() -> None:
    """starlight.notebook uses starlight.core, and the kernel uses the notebook: importing
    the notebook before anything else must work (it did not in A2)."""
    import subprocess
    import sys

    from tests.conftest import REPO

    for module in ("starlight.notebook", "starlight.notebook.markdown", "starlight.notebook.index"):
        done = subprocess.run(
            [sys.executable, "-c", f"import {module}"], cwd=REPO, capture_output=True, text=True
        )
        assert done.returncode == 0, done.stderr
