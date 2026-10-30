"""Safety controls: actions allowed for a while, limits, practice mode, private
conversations, the activity page, the rules in plain words, modules that did not come with
Starlight, and the support bundle."""

from __future__ import annotations

import json
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from starlight.cli import main
from starlight.core import Kernel, Paths
from starlight.core.activity import describe, read_activity
from starlight.core.config import PermissionsConfig, parse_limit
from starlight.core.dispatcher import Dispatcher
from starlight.core.events import EventBus
from starlight.core.kernel import question_for
from starlight.core.killswitch import KillSwitch
from starlight.core.modules import SHIPPED, outside_warning
from starlight.core.permissions import Permission, PermissionPolicy
from starlight.core.tools import Tool
from starlight.support import make_bundle, scrub
from tests.conftest import REPO


class NoArgs(BaseModel):
    pass


def setup(**config: Any) -> tuple[Dispatcher, list[str], list[str]]:
    """A dispatcher with mail.send (asks by nature) and calc.calculate (safe)."""
    asked: list[str] = []
    ran: list[str] = []

    def make(module: str, name: str, permission: Permission) -> Tool:
        async def run(**_: Any) -> str:
            ran.append(f"{module}.{name}")
            return "done"

        return Tool(module, name, "", permission, run, NoArgs)

    async def confirmer(t: Tool, args: dict[str, Any]) -> bool:
        asked.append(question_for(t, args))
        return True

    policy = PermissionPolicy(PermissionsConfig(**config))
    switch = KillSwitch(Path(tempfile.mkdtemp()) / "STOP")
    dispatcher = Dispatcher(switch, policy, EventBus(), confirmer=confirmer)
    dispatcher.register(make("mail", "send", Permission.CONFIRM))
    dispatcher.register(make("calc", "calculate", Permission.SAFE))
    return dispatcher, asked, ran


# -- for a while ------------------------------------------------------------------------------


async def test_an_action_allowed_for_a_while_asks_again_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, asked, ran = setup()
    clock = [1000.0]
    monkeypatch.setattr("starlight.core.permissions.time.time", lambda: clock[0])
    dispatcher.policy.allow_for("mail.*", 30 * 60)
    await dispatcher.call("mail.send")
    assert asked == [] and ran == ["mail.send"]
    assert list(dispatcher.policy.timed_rules()) == ["mail.*"]
    clock[0] += 30 * 60 + 1
    await dispatcher.call("mail.send")
    assert len(asked) == 1 and dispatcher.policy.timed_rules() == {}
    dispatcher.policy.allow_for("mail.send", 60)
    assert dispatcher.policy.end_timed("mail.send") and not dispatcher.policy.end_timed("x")


# -- limits -----------------------------------------------------------------------------------


def test_limits_are_read_and_wrong_ones_are_explained() -> None:
    assert parse_limit("20/hour") == (20, 3600) and parse_limit("5 / minutes") == (5, 60)
    assert parse_limit("3/day") == (3, 86400) and parse_limit("3 per day") is None
    assert parse_limit("many/hour") is None and parse_limit("3/week") is None
    with pytest.raises(ValidationError, match="should look like 20/hour"):
        PermissionsConfig(limits={"mail.send": "lots"})


async def test_past_its_limit_an_action_that_runs_on_its_own_asks_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, asked, ran = setup(auto_approve=["mail.send"], limits={"mail.*": "2/hour"})
    clock = [5000.0]
    monkeypatch.setattr("starlight.core.permissions.time.time", lambda: clock[0])
    for _ in range(2):
        await dispatcher.call("mail.send")
    assert asked == [] and ran == ["mail.send", "mail.send"]
    await dispatcher.call("mail.send")  # the third in the hour: asked (and approved here)
    assert len(asked) == 1 and "'mail.*' ran 2 times in the last hour" in asked[0]
    assert ran == ["mail.send"] * 3
    await dispatcher.call("calc.calculate")  # not under the limit: unaffected
    clock[0] += 3601  # an hour later the window has room again
    await dispatcher.call("mail.send")
    assert len(asked) == 1


async def test_a_refused_call_does_not_count_and_the_shipped_config_limits_mail() -> None:
    from starlight.core.config import load_config

    dispatcher, _, ran = setup(auto_approve=["mail.send"], limits={"mail.send": "1/day"})

    async def no(t: Tool, args: dict[str, Any]) -> bool:
        return False

    dispatcher.confirmer = no
    await dispatcher.call("mail.send")
    refused = await dispatcher.call("mail.send")
    assert not refused.ok and ran == ["mail.send"]
    shipped = load_config(Paths(REPO)).permissions.limits
    assert shipped["mail.send"] == "20/hour" and shipped["mail.reply"] == "20/hour"


# -- practice mode -----------------------------------------------------------------------------


async def test_practice_mode_describes_actions_and_still_looks_things_up() -> None:
    dispatcher, asked, ran = setup(auto_approve=["mail.send"], blocked=["calc.*"])
    dispatcher.policy.practice = True
    result = await dispatcher.call("mail.send")
    assert result.ok and "Practice mode: mail.send did NOT run" in result.output
    assert ran == [] and asked == []
    assert not (await dispatcher.call("calc.calculate")).ok  # blocked stays blocked
    dispatcher.policy.config.blocked.clear()
    assert (await dispatcher.call("calc.calculate")).ok and ran == ["calc.calculate"]


# -- from the chat (access module) -----------------------------------------------------------


class Yes:
    def __init__(self, *answers: bool) -> None:
        self.answers, self.questions = list(answers), []  # type: list[bool], list[str]

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("access", "hello"):
        use_module(name)
    with paths.config_file.open("a") as config:  # no embedding model to wait for
        config.write("memory:\n  enabled: false\nskills:\n  enabled: false\n")
    return paths


async def test_the_access_module_allows_for_a_while_and_switches_practice_mode(
    project: Paths,
) -> None:
    async with Kernel(project) as k:
        user = Yes(True, False, True)
        k.set_ui(user)  # type: ignore[arg-type]
        allowed = await k.dispatcher.call(
            "access.allow_action_for", {"pattern": "hello.shout", "minutes": 10}
        )
        assert allowed.ok and "next 10 minutes" in user.questions[0]
        assert k.policy.resolve("hello.shout", Permission.CONFIRM) is Permission.SAFE
        await k.dispatcher.call("access.practice_mode", {"on": True})
        assert k.policy.practice
        kept = await k.dispatcher.call("access.practice_mode", {"on": False})  # asked: no
        assert not kept.ok and k.policy.practice
        await k.dispatcher.call("access.practice_mode", {"on": False})  # asked: yes
        assert not k.policy.practice
        words = (await k.dispatcher.call("access.rules_in_plain_words")).output
        assert "hello.*" in words["runs without asking"]  # all of hello runs now
        assert "hello.shout" in words["allowed for a while"]
        cleared = await k.dispatcher.call("access.clear_rule", {"pattern": "hello.shout"})
        assert cleared.ok and k.policy.timed_rules() == {}


def test_the_summary_shortens_whole_modules() -> None:
    policy = PermissionPolicy(PermissionsConfig(blocked=["dev.git_commit"]))
    summary = policy.summary(
        [
            ("weather.now", Permission.SAFE),
            ("weather.forecast", Permission.SAFE),
            ("dev.git_status", Permission.SAFE),
            ("dev.git_commit", Permission.CONFIRM),
            ("dev.git_pull", Permission.CONFIRM),
        ]
    )
    assert summary == {
        "safe": ["dev.git_status", "weather.*"],
        "confirm": ["dev.git_pull"],
        "blocked": ["dev.git_commit"],
    }


def test_starlight_rules(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(paths.root), "rules"]) == 0
    out = capsys.readouterr().out
    assert "Runs without asking:" in out and "Asks you first:" in out and "Limits" in out


# -- private conversations --------------------------------------------------------------------


async def test_a_private_conversation_is_never_saved_or_learned_from(paths: Paths) -> None:
    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\nmemory:\n  enabled: false\nskills:\n  enabled: false\n"
    )
    async with Kernel(paths) as k:
        events: list[dict[str, Any]] = []

        async def seen(event: Any) -> None:
            events.append(event.data)

        k.bus.subscribe("task.completed", seen, owner="test")
        ordinary = k.conversations.current
        secret = k.new_conversation(private=True)
        assert k.conversations.current == ordinary  # the terminal does not continue it
        result = await k.ask("my bank PIN is 1234", keep_history=True, conversation=secret)
        assert result.answer == "You said: my bank PIN is 1234"
        files = list((paths.data / "conversations").glob("*.json"))
        assert all("1234" not in f.read_text() for f in files)
        assert [f.stem for f in files] == [ordinary]
        listed = {c["id"]: c for c in k.conversations.list()}
        assert listed[secret]["private"] and listed[secret]["count"] == 1
        assert k.conversations.get(secret)["private"] is True
        assert k.conversations.list(search="PIN")[0]["id"] == secret
        await k.ask("hello", keep_history=True, conversation=ordinary)
        assert [e["private"] for e in events] == [True, False]
        k.forget_conversation(secret)
        assert secret not in {c["id"] for c in k.conversations.list()}


async def test_nothing_is_learned_from_a_private_conversation(paths: Paths) -> None:
    from starlight.core.events import Event

    async with Kernel(paths) as k:
        learned: list[str] = []

        async def extract(request: str, answer: str, request_id: str) -> None:
            learned.append(request)

        k.memory.extract = extract  # type: ignore[method-assign]
        k.provider.stand_in = False  # type: ignore[union-attr]  (learn from the test model)
        await k._after_task(Event("task.completed", {"request": "private", "private": True}))
        await k._after_task(Event("task.completed", {"request": "open", "private": False}))
        for task in list(k._background):
            if task.get_name() == "memory-extract":
                await task
    assert learned == ["open"]


# -- the activity page ------------------------------------------------------------------------


def test_the_audit_log_in_plain_words(tmp_path: Path) -> None:
    entries = [
        {"event": "core.started", "timestamp": "2026-10-30T08:00:00Z"},
        {"event": "module.loaded", "module": "web", "timestamp": "2026-10-30T08:00:01Z"},
        {"event": "tool.call", "tool": "mail.send", "permission": "confirm",
         "args": {"to": "a@b.de", "password": "***"}, "timestamp": "2026-10-30T08:01:00Z"},
        {"event": "tool.denied", "tool": "commands.shell", "reason": "not confirmed (limit)",
         "timestamp": "2026-10-30T08:02:00Z"},
        {"event": "tool.result", "tool": "web.fetch", "ok": False, "error": "HTTP 500",
         "timestamp": "2026-10-30T08:03:00Z"},
        {"event": "tool.result", "tool": "web.fetch", "ok": True, "timestamp": "x"},
        {"event": "killswitch.triggered", "reason": "hotkey", "timestamp": "2026-10-30T08:04:00Z"},
    ]  # fmt: skip
    (tmp_path / "audit.log.1").write_text(json.dumps({"event": "core.stopped"}) + "\n")
    (tmp_path / "audit.log").write_text("\n".join(json.dumps(e) for e in entries) + "\nnot json\n")
    found = read_activity(tmp_path)
    assert [e["kind"] for e in found] == [
        "kill switch", "failed", "denied", "action", "starlight", "starlight",
    ]  # fmt: skip
    assert found[3]["text"].startswith("ran mail.send (you approved it) {")
    assert found[-1]["text"] == "Starlight stopped"  # from the rotated log, oldest last
    assert [e["kind"] for e in read_activity(tmp_path, kind="denied")] == ["denied"]
    assert len(read_activity(tmp_path, search="MAIL.SEND")) == 1
    assert len(read_activity(tmp_path, limit=2)) == 2
    assert describe({"event": "module.loaded"}) is None


# -- modules that did not come with Starlight -----------------------------------------------


def test_shipped_modules_are_the_repository_s_and_others_get_a_warning() -> None:
    folders = {
        p.name for p in (REPO / "modules").iterdir() if p.is_dir() and not p.name.startswith("_")
    }
    assert folders - {"__pycache__"} == SHIPPED
    assert outside_warning("weather") == ""
    assert "did not come with Starlight" in outside_warning("my_scraper")


def test_enabling_an_outside_module_from_the_command_line_warns(
    paths: Paths, write_module: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    write_module("scraper", "from starlight.core import Module\nclass Mod(Module): pass\n")
    assert main(["--home", str(paths.root), "modules", "enable", "scraper"]) == 0
    assert "did not come with Starlight" in capsys.readouterr().out


# -- the support bundle -----------------------------------------------------------------------


def test_scrub_hides_secrets_keys_and_the_home_folder() -> None:
    text = (
        "connected with hunter2-secret as C:\\Users\\Paul\\Starlight; "
        'header Bearer abcdefghijklmnopqrstuvwxyz123; "api_key": "sk-12345678901234567890xx"'
    )
    hidden = scrub(text, ["hunter2-secret", "abc"], home="C:\\Users\\Paul")
    assert "hunter2" not in hidden and "<secret hidden>" in hidden
    assert "Bearer abcdef" not in hidden and "sk-1234" not in hidden
    assert "~\\Starlight" in hidden and "Paul" not in hidden


def test_the_support_bundle_has_logs_and_settings_but_nothing_private(
    paths: Paths, tmp_path: Path
) -> None:
    (paths.logs / "starlight.log").write_text('{"event": "boom", "token": "tok_supersecret1"}\n')
    (paths.logs / "audit.log").write_text('{"event": "tool.call", "tool": "mail.send"}\n')
    (paths.config / "vault_index.yaml").write_text("keys: [mail.password]\n")
    (paths.data / "conversations").mkdir(parents=True, exist_ok=True)
    (paths.data / "conversations" / "a.json").write_text("my diary")
    (paths.memory / "note.md").parent.mkdir(parents=True, exist_ok=True)
    (paths.memory / "note.md").write_text("private memory")
    bundle = make_bundle(paths, tmp_path / "out", secrets=["tok_supersecret1"], checks="[ok] all")
    names = zipfile.ZipFile(bundle).namelist()
    assert {"about.txt", "README.txt", "config/starlight.yaml"} <= set(names)
    assert "logs/starlight.log" in names and "logs/audit.log" in names
    assert not any(n.startswith(("data", "memory")) or "vault" in n for n in names)
    text = zipfile.ZipFile(bundle).read("logs/starlight.log").decode()
    assert "supersecret" not in text
    about = zipfile.ZipFile(bundle).read("about.txt").decode()
    assert "Starlight" in about and "[ok] all" in about
    quiet = make_bundle(paths, tmp_path / "quiet", activity=False)
    assert "logs/audit.log" not in zipfile.ZipFile(quiet).namelist()


def test_starlight_support_bundle(
    paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--home", str(paths.root), "support-bundle", "--out", str(tmp_path / "b")])
    assert code == 0 and "Support bundle:" in capsys.readouterr().out
    assert len(list((tmp_path / "b").glob("starlight-support-*.zip"))) == 1
