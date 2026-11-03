"""Automations with several conditions: `count > 0 and server is nas`, `not (status is done)`,
und/oder/nicht. The notes' query language (starlight.notebook.query) reads them and checks
them on an event's fields or a tool's result; every condition written the old way keeps its
meaning, and a new rule with a condition that cannot be read is refused."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.notebook import query
from tests.conftest import REPO
from tests.test_automations import User, call, deliveries, module
from tests.test_automations import project as project  # (the fixture)

DATA = {
    "count": 3, "server": "NAS", "expiring": ["a.example"], "subject": "Server down: nas",
    "tags": ["work", "home"], "due": "2026-11-05", "done": False, "nested": {"name": "x"},
    "zero": 0, "nothing": "",
}  # fmt: skip


def automations() -> Any:
    """modules/automations/module.py, loaded by its path (with modules/ on the import path,
    modules/calendar would hide Python's own calendar module)."""
    name = "automations_for_tests"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, REPO / "modules" / "automations" / "module.py"
        )
        assert spec is not None and spec.loader is not None
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded  # (pydantic looks its own types up there)
        spec.loader.exec_module(loaded)
    return sys.modules[name]


@pytest.mark.parametrize(
    ("condition", "holds"),
    [
        ("count > 0 and server is nas", True),
        ("count > 5 or server is nas", True),
        ("count > 5 or server is router", False),
        ("not (server is nas)", False),
        ("nicht (server ist nas) oder count >= 3", True),
        ("count >= 3 und server ist nas", True),
        ("server = router oder server = nas", True),
        ("tags has home and not (tags has garden)", True),
        ("subject has down and count < 10", True),
        ("due < 2026-11-10 and done is no", True),
        ("nested.name is x and expiring", True),
        ("missing is empty and nothing is empty and zero is not empty", True),
        ("missing is not x and not missing", True),
        ("count>0", True),  # (read now; before, a field called "count>0")
        ("Count > 2 and SERVER is nas", True),  # (no case, the new way)
    ],
)
def test_several_conditions(condition: str, holds: bool) -> None:
    assert automations().matches(condition, DATA) is holds


@pytest.mark.parametrize(
    ("condition", "holds"),
    [
        ("count > 0", True), ("count > 3", False), ("server == nas", True),
        ("server != nas", False), ("subject ~ DOWN", True), ("expiring", True),
        ("zero", False), ("nothing", False), ("missing", False), ("missing != x", True),
        ("missing == x", False), ("Count > 2", False),  # (the old way: a name's case counts)
    ],
)  # fmt: skip
def test_conditions_written_the_old_way_keep_their_meaning(condition: str, holds: bool) -> None:
    assert automations().matches(condition, DATA) is holds


def test_words_with_and_or_or_in_them_go_in_quotes() -> None:
    matches, data = automations().matches, {"subject": "Rock and roll tonight"}
    assert matches('subject ~ "rock and roll"', data)
    assert matches("subject has 'rock and roll' and subject has tonight", data)
    assert not matches("subject ~ rock and roll", data)  # (unquoted: "roll" is a second check)


def test_the_query_language_reads_the_old_operators_too() -> None:
    assert query.parse("server == nas") == query.parse("server is nas")
    assert query.parse("subject ~ down") == query.parse("subject has down")
    assert query.parse("rating") == query.Cond("rating", "set")
    assert query.parse("rating and status is open") == query.AllOf(
        (query.Cond("rating", "set"), query.parse("status is open"))
    )  # a field alone: on notes, that it is filled in
    assert query.to_sql(query.parse("rating")) == query.to_sql(query.parse("rating is not empty"))
    with pytest.raises(query.QueryError, match="expected a value after '>'"):
        query.parse("count > and x")


def test_evaluating_on_data() -> None:
    evaluate = query.evaluate
    assert evaluate(query.parse("items.0.name is a and items.1.name is b"),
                    {"items": [{"name": "A"}, {"name": "B"}]})  # fmt: skip
    assert evaluate(query.parse("size >= 1,5"), {"size": "2"})  # (numbers as numbers)
    assert not evaluate(query.parse("size >= 10"), {"size": "9"})  # (not as text: "9" > "10")
    assert evaluate(query.parse("when < today+1"), {"when": "2000-01-01T10:00"})
    assert evaluate(query.parse("ok is yes and other is nein"), {"ok": True, "other": "no"})
    assert evaluate(query.parse("labels has urgent"), {"labels": {"urgent": 1}})
    assert not evaluate(query.parse("x has y"), "not a dict")


async def test_rules_with_several_conditions(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        made = await call(
            k, "automations.add", name="servers", on="network.server_down",
            condition="server is nas or server is router", say="{event.server} is down",
        )  # fmt: skip
        assert made.ok, made.error
        told = deliveries(k)
        for server in ("nas", "printer", "Router"):
            await k.bus.publish("network.server_down", {"server": server})
        await module(k).settle()
        assert told == ["Automation: nas is down", "Automation: Router is down"]
        bad = await call(
            k, "automations.add", name="broken", on="network.server_down",
            condition="server is nas or", say="x",
        )  # fmt: skip
        assert not bad.ok and "the condition cannot be read" in str(bad.error)
        steps = await call(
            k, "automations.add", name="steps", when="daily 9:00", tool="probe.count",
            say="counted", args={"n": 2},
        )  # fmt: skip
        assert steps.ok
        rule = module(k)._rule("steps")
        rule.actions[1].only_if = "count > 1 and items has b"  # (set as in the file)
        ran = await call(k, "automations.run_now", name="steps")
        await module(k).settle()
        assert ran.ok and "Automation: counted" in told


def test_a_new_rule_s_only_if_is_checked_too() -> None:
    loaded = automations()
    build = loaded.AutomationsModule._build
    with pytest.raises(Exception, match="only_if cannot be read"):
        build({"name": "x", "when": "daily 9:00", "actions": [
            {"tool": "probe.count"}, {"say": "hi", "only_if": "count >"},
        ]})  # fmt: skip
    kept = loaded.Rule.model_validate(  # (one already saved stays: it just never holds)
        {"name": "x", "when": "daily 9:00", "actions": [{"say": "hi", "only_if": "count >"}]}
    )
    assert kept.actions[0].only_if == "count >"
    assert not loaded.matches("count >", {"count": 3})


async def test_the_weekly_open_notes_template(
    project: Paths, use_module: Any, documents: Path
) -> None:
    use_module("notes")
    notes = documents / "Starlight" / "Notes"
    notes.mkdir(parents=True)
    (notes / "Garden.md").write_text("---\nstatus: open\n---\n# Garden\n", encoding="utf-8")
    (notes / "Old.md").write_text("---\nstatus: done\n---\n# Old\n", encoding="utf-8")
    async with Kernel(project, ui=User(True)) as k:
        made = await call(k, "automations.use_template", template="weekly-open-notes")
        assert made.ok and "notes.query" in made.output["rule"]
        told = deliveries(k)
        await call(k, "automations.run_now", name="weekly-open-notes")
        await module(k).settle()
    assert told == [
        "Automation: 1 note(s) still say status: open or doing. Say 'show my open notes' to "
        "go through them."
    ]
