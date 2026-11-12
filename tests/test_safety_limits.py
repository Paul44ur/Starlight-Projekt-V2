"""Safety limits you set yourself (Settings -> Safety limits, starlight/core/safety.py):
the switches and levels, how a choice reaches the settings at a start and while Starlight
runs, the page (looked at from a phone, changed only at the PC), and each protection that
used to be fixed in code: Starlight's own folder, files elsewhere, programs and scripts,
the code sandbox, the access questions, scans, pairing and other web pages."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths, load_config, safepaths, safety
from starlight.core.config import StarlightConfig, load_module_config, read_yaml
from starlight.core.modules import discover, import_module_class
from starlight.core.permissions import Permission
from tests import test_chat_page
from tests.conftest import REPO
from tests.test_chat_page import answer
from tests.test_mail import User
from tests.test_remote import LOCAL, PHONE, client, home, pair, phone

HA = "http://homeassistant.local:8123"
static_site = test_chat_page.static_site  # (a fixture: a server for the page's files)


def choose(config_dir: Path, level: str = "balanced", **settings: Any) -> None:
    """What the page saves."""
    safety.save_choice(config_dir, safety.Choice(level, settings))


def module_config(name: str) -> type[Any]:
    found, _ = discover(REPO / "modules")
    return import_module_class(*found[name]).Config  # type: ignore[attr-defined,no-any-return]


# -- the switches --------------------------------------------------------------------------------


def test_every_switch_names_a_real_setting_and_speaks_german() -> None:
    for switch in safety.SWITCHES:
        assert switch.group in safety.GROUPS, switch.key
        title, help_text, warning = safety.GERMAN[switch.key]
        assert title and help_text and (warning or not switch.warning), switch.key
        if switch.module is None:
            safety._get(StarlightConfig(), switch.path)  # AttributeError if there is none
        else:
            assert switch.path[0] in module_config(switch.module).model_fields, switch.key
        for level in ("strict", "relaxed"):
            value = switch.level_value(level)
            if value is not None:
                assert switch.check(value) == value, (switch.key, level)
        if switch.kind == "bool":  # said as the protection, both ways round
            for on in (True, False):
                setting = switch.to_setting(on)
                if setting is not safety.AS_CONFIGURED:
                    assert switch.from_setting(setting) is on, switch.key


@pytest.mark.parametrize("level", safety.LEVELS)
def test_every_level_gives_settings_that_load(paths: Paths, level: str) -> None:
    choose(paths.config, level)
    load_config(paths)
    for switch in safety.SWITCHES:
        if switch.module is not None:
            load_module_config(paths, switch.module, module_config(switch.module))


def test_balanced_changes_nothing_and_a_level_sets_its_values(paths: Paths) -> None:
    paths.config_file.write_text(
        "agent:\n  max_steps: 12\npermissions:\n  auto_approve: ['hello.*']\n"
        "  limits: {mail.send: 20/hour}\n",
        encoding="utf-8",
    )
    choose(paths.config, "balanced")
    config = load_config(paths)
    assert config.agent.max_steps == 12 and config.permissions.auto_approve == ["hello.*"]
    assert config.permissions.limits == {"mail.send": "20/hour"}

    choose(paths.config, "strict")
    config = load_config(paths)
    assert config.agent.max_steps == 8 and config.permissions.auto_approve == []
    assert config.permissions.limits == safety.BY_KEY["limits"].strict  # replaced, not merged
    assert config.web.remote.confirm == ["*"] and "commands.*" in config.web.remote.blocked
    assert config.output.protect_starlight_folder and not config.output.write_anywhere

    choose(paths.config, "relaxed")
    config = load_config(paths)
    assert config.agent.max_steps == 30 and config.permissions.limits == {}
    assert not config.permissions.guard_after_untrusted and not config.output.ask_outside
    assert config.permissions.auto_approve == ["hello.*"]  # Relaxed leaves it as configured


def test_a_single_setting_beats_the_level_and_local_yaml_beats_both(paths: Paths) -> None:
    choose(paths.config, "strict", steps=50, guard=False)
    config = load_config(paths)
    assert config.agent.max_steps == 50 and not config.permissions.guard_after_untrusted
    assert config.http.max_requests_per_second == 3  # the rest of Strict still applies
    (paths.config / "starlight.local.yaml").write_text("agent:\n  max_steps: 3\n")
    assert load_config(paths).agent.max_steps == 3  # hand edits there are yours


def test_module_settings_follow_the_choice(paths: Paths) -> None:
    (paths.module_configs / "network.yaml").write_text("any_host: true\n", encoding="utf-8")
    network = module_config("network")
    choose(paths.config, "strict")
    assert load_module_config(paths, "network", network).any_host is False  # (listed only)
    choose(paths.config, "balanced", servers=False)
    assert load_module_config(paths, "network", network).any_host is True


def test_a_damaged_choice_never_stops_starlight(paths: Paths) -> None:
    (paths.config / safety.SAFETY_FILE).write_text(
        "level: wild\nsettings:\n  steps: lots\n  nonsense: 1\n  guard: false\n", encoding="utf-8"
    )
    choice = safety.read_choice(paths.config)
    assert choice.level == "balanced" and choice.settings == {"guard": False}
    assert not load_config(paths).permissions.guard_after_untrusted
    for broken in ("level: [strict\n", "- strict\n"):  # not YAML, or not settings at all
        (paths.config / safety.SAFETY_FILE).write_text(broken, encoding="utf-8")
        assert safety.read_choice(paths.config).level == "balanced"
        load_config(paths)  # starts


def test_values_from_the_page_are_checked() -> None:
    switch = safety.BY_KEY
    assert switch["limits"].check("mail.send: 50/hour\ndesktop.*: 5 / minute") == {
        "mail.send": "50/hour",
        "desktop.*": "5 / minute",
    }
    assert switch["origins"].check(f"{HA}\n{HA}\n") == [HA]  # (once)
    assert switch["phone_asks"].check("desktop.*, mail.send") == ["desktop.*", "mail.send"]
    for key, wrong in (
        ("limits", "mail.send 50"),
        ("limits", "mail.send: often"),
        ("steps", 0),
        ("steps", "many"),
        ("origins", "javascript:alert(1)"),
        ("origins", f"{HA}/path"),
        ("phone_asks", ["rm -rf /"]),
        ("guard", "no"),
    ):
        with pytest.raises(ValueError):
            switch[key].check(wrong)


# -- the page ------------------------------------------------------------------------------------


def test_the_page_changes_the_limits_at_the_pc_and_shows_them_on_the_phone(
    tmp_path: Path,
) -> None:
    root = home(tmp_path / "h")
    with client(root) as c:
        token = pair(c)
        seen = c.get("/api/safety", headers=phone(token))
        assert seen.status_code == 200 and seen.json()["level"] == "balanced"
        refused = c.post("/api/safety", json={"level": "relaxed"}, headers=phone(token))
        assert refused.status_code == 403  # a phone looks, the PC decides

        k = c.app.state.kernel  # type: ignore[attr-defined]
        state = c.post("/api/safety", json={"level": "strict"}, headers=LOCAL).json()
        assert state["level"] == "strict" and k.config.agent.max_steps == 8  # at once
        state = c.post("/api/safety", json={"key": "steps", "value": 25}, headers=LOCAL).json()
        row = next(s for g in state["groups"] for s in g["switches"] if s["key"] == "steps")
        assert (
            row["changed"] and row["value"] == 25 and row["de"]["title"] == "Schritte pro Anfrage"
        )
        assert state["changed"] == 1 and k.config.agent.max_steps == 25
        wrong = c.post("/api/safety", json={"key": "steps", "value": 1000}, headers=LOCAL)
        assert wrong.status_code == 400 and "between 1 and 100" in wrong.json()["detail"]
        c.post("/api/safety", json={"key": "steps", "reset": True}, headers=LOCAL)
        assert k.config.agent.max_steps == 8  # back to Strict's
        audit = (root / "logs" / "audit.log").read_text("utf-8")
    assert '"web.safety"' in audit
    assert read_yaml(root / "config" / safety.SAFETY_FILE) == {"level": "strict", "settings": {}}


def test_strict_keeps_the_choices_made_tool_by_tool(tmp_path: Path) -> None:
    root = home(tmp_path / "h")
    config = root / "config" / "starlight.yaml"
    config.write_text(
        config.read_text("utf-8") + "permissions:\n  auto_approve: ['hello.*']\n", "utf-8"
    )
    with client(root) as c:
        k = c.app.state.kernel  # type: ignore[attr-defined]
        made = c.post(
            "/api/permissions", json={"pattern": "hello.shout", "setting": "safe"}, headers=LOCAL
        )
        assert made.status_code == 200
        c.post("/api/safety", json={"level": "strict"}, headers=LOCAL)
        assert k.policy.resolve("hello.greet", Permission.CONFIRM) is Permission.CONFIRM
        assert k.policy.resolve("hello.shout", Permission.CONFIRM) is Permission.SAFE  # yours
        c.post("/api/safety", json={"level": "balanced"}, headers=LOCAL)
        assert k.policy.resolve("hello.greet", Permission.CONFIRM) is Permission.SAFE


def test_a_change_reaches_the_running_modules_and_folder_rules(tmp_path: Path) -> None:
    root = home(tmp_path / "h")
    with client(root) as c:
        k = c.app.state.kernel  # type: ignore[attr-defined]
        access = k.modules.records["access"].instance
        c.post("/api/safety", json={"key": "access_asks", "value": False}, headers=LOCAL)
        assert access.ctx.config.ask_before_changes is False
        assert safepaths.protects_starlight()
        c.post("/api/safety", json={"key": "own_folder", "value": False}, headers=LOCAL)
        assert not safepaths.protects_starlight()  # the path guard follows at once
        c.post("/api/safety", json={"key": "own_folder", "reset": True}, headers=LOCAL)
        assert safepaths.protects_starlight()
    assert safepaths.protects_starlight()  # (and all on again without a running Starlight)


# -- what used to be fixed in code -----------------------------------------------------------------


async def test_starlights_own_folder_can_be_opened_up(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    use_module("files")
    note = paths.config / "note.txt"
    note.write_text("inside", encoding="utf-8")
    async with Kernel(paths, ui=User()) as k:
        refused = await k.dispatcher.call("files.read", {"path": str(note)})
        assert not refused.ok and "off-limits" in str(refused.error)
        k.config.output.protect_starlight_folder = False  # what the page does
        opened = await k.dispatcher.call("files.read", {"path": str(note)})
    assert opened.ok and opened.output["content"] == "inside"


async def test_your_files_elsewhere_without_a_question_but_scripts_still_ask(
    paths: Paths,
    use_module: Callable[[str], Path],
    documents: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    use_module("files")
    work = tmp_path_factory.mktemp("Work")
    (work / "a.txt").write_text("hi", encoding="utf-8")
    choose(paths.config, outside=False)
    user = User()
    async with Kernel(paths, ui=user) as k:
        read = await k.dispatcher.call("files.read", {"path": str(work / "a.txt")})
        assert read.ok and user.questions == []
        script = await k.dispatcher.call(
            "files.save", {"path": str(work / "x.bat"), "content": "x"}
        )
        assert script.ok and len(user.questions) == 1 and ".bat" in user.questions[0]
        k.config.output.ask_runnable = False
        other = await k.dispatcher.call("files.save", {"path": str(work / "y.bat"), "content": "y"})
    assert other.ok and len(user.questions) == 1  # no second question


async def test_the_code_sandbox_limits_can_be_switched(tmp_path: Path) -> None:
    from starlight.sandbox import ProcessSandbox

    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "key").write_text("k", encoding="utf-8")
    box = ProcessSandbox(tmp_path / "work", deny=[secret])
    (tmp_path / "work").mkdir()
    read = {"main.py": f"print(open({str(secret / 'key')!r}).read())"}
    start = {
        "main.py": "import subprocess, sys\n"
        "print(subprocess.run([sys.executable, '-c', 'print(7)'], capture_output=True, "
        "text=True).stdout)"
    }
    run = await box.run(read, ["python", "main.py"], 60)
    assert not run.ok and "outside the sandbox" in run.stderr
    box.protect_folders = False
    run = await box.run(read, ["python", "main.py"], 60)
    assert run.ok and run.stdout.strip() == "k"
    run = await box.run(start, ["python", "main.py"], 60)
    assert not run.ok and "starting programs" in run.stderr
    box.block_programs = False
    run = await box.run(start, ["python", "main.py"], 60)
    assert run.ok and run.stdout.strip() == "7"


async def test_the_code_module_hands_the_limits_to_its_sandbox(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("code")
    (paths.module_configs / "code.yaml").write_text("sandbox: process\n", encoding="utf-8")
    choose(paths.config, sandbox_folders=False)
    async with Kernel(paths) as k:
        code = k.modules.records["code"].instance
        code._limits()
        assert code.sandbox.protect_folders is False and code.sandbox.block_programs is True


async def test_rules_change_from_the_chat_without_a_question_when_switched_off(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("access")
    user = User()
    async with Kernel(paths, ui=user) as k:
        asked = await k.dispatcher.call("access.allow_action", {"pattern": "hello.*"})
        assert asked.ok and len(user.questions) == 1
        k.modules.records["access"].instance.ctx.config.ask_before_changes = False
        quiet = await k.dispatcher.call("access.block_action", {"pattern": "hello.*"})
    assert quiet.ok and len(user.questions) == 1  # no new question
    secret = User()  # (secrets always ask)
    async with Kernel(paths, ui=secret) as k:
        await k.dispatcher.call("access.set_secret", {"key": "a.b", "secret": "x"})
    assert len(secret.questions) == 1


async def test_a_host_allowed_once_can_be_scanned_without_asking_again(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("recon")
    choose(paths.config, scan_asks=False)
    user = User()
    async with Kernel(paths, ui=user) as k:
        recon = k.modules.records["recon"].instance
        await recon._authorize("192.168.1.7", "192.168.1.7", "22")  # new: asks
        assert len(user.questions) == 1
        await recon._authorize("192.168.1.7", "192.168.1.7", "22")  # known: no question
        assert len(user.questions) == 1
        recon.config.ask_again_for_known = True
        await recon._authorize("192.168.1.7", "192.168.1.7", "22")
    assert len(user.questions) == 2


def test_pairing_can_be_left_out_and_its_limits_set(tmp_path: Path) -> None:
    root = home(tmp_path / "h")
    choose(root / "config", pairing=False, code_minutes=2, wrong_codes=2)
    with client(root) as c:
        assert c.get("/api/status", headers=PHONE).status_code == 200  # no pairing needed
        changed = c.post("/api/safety", json={"level": "relaxed"}, headers=PHONE)
        assert changed.status_code == 403  # the settings still stay at the PC
        code = c.post("/api/pair/code", headers=LOCAL).json()
        assert code["valid_seconds"] == 120
        wrong = "000000" if code["code"] != "000000" else "111111"
        for _ in range(2):
            c.post("/api/pair", headers=PHONE, json={"code": wrong, "name": "x"})
        locked = c.post("/api/pair", headers=PHONE, json={"code": code["code"], "name": "x"})
    assert locked.status_code == 403  # two wrong codes locked this device out


def test_paired_only_is_the_default(tmp_path: Path) -> None:
    with client(home(tmp_path / "h")) as c:
        assert c.get("/api/status", headers=PHONE).status_code == 403


def test_a_page_you_allowed_may_use_the_chat_and_no_other(tmp_path: Path) -> None:
    root = home(tmp_path / "h", remote=False)
    choose(root / "config", origins=[HA])
    allowed = {**LOCAL, "origin": HA}
    other = {**LOCAL, "origin": "http://evil.test"}
    with client(root) as c:
        ok = c.get("/api/status", headers=allowed)
        assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == HA
        assert c.get("/api/status", headers=other).status_code == 403
        asks = {"access-control-request-method": "POST"}
        before = c.options("/api/permissions", headers={**allowed, **asks})
        assert (
            before.status_code == 204 and "POST" in before.headers["access-control-allow-methods"]
        )
        assert (
            "access-control-allow-origin"
            not in c.options("/api/permissions", headers={**other, **asks}).headers
        )
        page = c.get("/", headers=LOCAL)
    assert page.headers["content-security-policy"] == f"frame-ancestors 'self' {HA}"
    assert "x-frame-options" not in page.headers


def test_without_other_pages_nothing_may_frame_the_chat(tmp_path: Path) -> None:
    with client(home(tmp_path / "h", remote=False)) as c:
        page = c.get("/", headers=LOCAL)
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["content-security-policy"] == "frame-ancestors 'none'"


# -- the card on the Settings page, in a browser ---------------------------------------------------


async def test_the_settings_card_sets_levels_and_single_protections(
    static_site: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import os

    async_api = pytest.importorskip("playwright.async_api")
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        test_chat_page.CHROMIUM if Path(test_chat_page.CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    modules = {s.module: module_config(s.module)() for s in safety.SWITCHES if s.module}
    state = safety.page_state(tmp_path, StarlightConfig(), modules)
    posted: list[dict[str, Any]] = []
    answers = {
        "/api/status": {"local": True},
        "/api/permissions": {
            "tools": [],
            "summary": {"safe": [], "confirm": [], "blocked": []},
            "limits": {},
            "practice": False,
        },
        "/api/modules": {"modules": []},
    }

    async def server(route: Any, request: Any) -> None:
        path = "/" + request.url.split("/", 3)[3].split("?")[0]
        if path == "/api/safety":
            if request.method == "POST":
                posted.append(request.post_data_json)
                state["level"] = posted[-1].get("level", state["level"])
            body = state
        elif path in answers:
            body = answers[path]
        else:
            await route.fulfill(status=404, body="{}", content_type="application/json")
            return
        await route.fulfill(body=json.dumps(body), content_type="application/json")

    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        try:
            page = await browser.new_page()
            await page.route("**/api/**", server)
            await page.goto(f"{static_site}/settings.html")
            await page.wait_for_selector("#safety h3")
            assert await page.locator("#safety h3").count() == 3
            balanced = page.locator("#levels button.balanced")
            assert await balanced.get_attribute("aria-pressed") == "true"
            assert await page.locator("#safety .srow").count() == len(safety.SWITCHES)

            own_folder = page.get_by_label("Keep Starlight's own folder off-limits")
            await own_folder.evaluate("box => box.click()")  # (the box itself is drawn hidden)
            assert await answer(page, yes=False) == "Switch this off?"  # no: it stays on
            assert await own_folder.is_checked() and posted == []
            await own_folder.evaluate("box => box.click()")
            await answer(page)
            await page.wait_for_function("() => document.querySelector('.toast')")
            assert posted[-1] == {"key": "own_folder", "value": False}
            steps = page.get_by_label("Steps per request")
            await steps.fill("20")
            await steps.press("Enter")
            await page.wait_for_timeout(200)
            assert posted[-1] == {"key": "steps", "value": 20}
            sent = len(posted)
            await steps.fill("")  # emptied: nothing is sent (it used to send 0)
            await steps.press("Enter")
            await page.wait_for_timeout(200)
            assert len(posted) == sent
            await page.locator("#levels button.strict").click()
            await page.wait_for_function(
                "() => document.querySelector('#levels button.strict.sel')"
            )
            assert posted[-1] == {"level": "strict"}

            german = await browser.new_page()  # the same card in German
            await german.add_init_script("localStorage.setItem('starlight-lang', 'de')")
            await german.route("**/api/**", server)
            await german.goto(f"{static_site}/settings.html")
            await german.wait_for_selector("#safety h3")
            assert await german.locator("#levels button.strict .lname").inner_text() == "Streng"
            titles = await german.locator("#safety .stitle").all_inner_texts()
            assert any(t.startswith("Schritte pro Anfrage") for t in titles)
            assert "Grenzen und der Wächter" in await german.locator("#safety h3").all_inner_texts()
        finally:
            await browser.close()


# -- found by the bug check ------------------------------------------------------------------------


def test_a_mistake_in_another_settings_file_does_not_break_a_change(tmp_path: Path) -> None:
    import shutil

    root = home(tmp_path / "h")
    shutil.copytree(REPO / "modules" / "recon", root / "modules" / "recon")
    with client(root) as c:
        k = c.app.state.kernel  # type: ignore[attr-defined]
        recon = k.modules.records["recon"].instance
        (root / "config" / "modules" / "recon.yaml").write_text("max_ports: lots\n")
        changed = c.post("/api/safety", json={"key": "scan_asks", "value": False}, headers=LOCAL)
        assert changed.status_code == 200 and "recon" in changed.json()["note"]
        assert recon.config.ask_again_for_known is True  # it keeps what it runs with
        steps = c.post("/api/safety", json={"key": "steps", "value": 20}, headers=LOCAL)
        assert steps.status_code == 200 and k.config.agent.max_steps == 20

        config = root / "config" / "starlight.yaml"
        good = config.read_text("utf-8")
        config.write_text(good + "viewer:\n  port: lots\n", "utf-8")  # broken meanwhile
        refused = c.post("/api/safety", json={"key": "steps", "value": 30}, headers=LOCAL)
        assert refused.status_code == 400 and "not saved" in refused.json()["detail"]
        assert k.config.agent.max_steps == 20  # nothing changed half-way
        assert safety.read_choice(root / "config").settings["steps"] == 20  # file as before


def test_starlight_remote_works_while_another_setting_has_a_mistake(paths: Paths) -> None:
    from starlight.cli import main

    paths.config_file.write_text("agent:\n  max_steps: zero\n", encoding="utf-8")
    assert main(["--home", str(paths.root), "remote", "devices"]) == 0


def test_an_allowed_page_matches_with_or_without_its_default_port() -> None:
    from starlight.web.app import allowed_request

    local = {"host": "127.0.0.1:8700"}
    assert allowed_request({**local, "origin": "https://example.com"}, "127.0.0.1",
                           ["https://example.com:443"])  # fmt: skip
    assert allowed_request({**local, "origin": "http://ha.local:8123"}, "127.0.0.1",
                           ["HTTP://HA.local:8123/"])  # fmt: skip
    assert not allowed_request({**local, "origin": "https://example.com:8443"}, "127.0.0.1",
                               ["https://example.com"])  # fmt: skip


def test_numbers_from_the_page_are_whole_where_they_must_be() -> None:
    steps = safety.BY_KEY["steps"]
    assert steps.check(12.0) == 12 and steps.check("12") == 12
    for wrong in (3.7, True, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            steps.check(wrong)
    assert safety.BY_KEY["web_pause"].check(0.5) == 0.5


def test_a_module_that_is_not_running_shows_its_protections_as_they_are(
    tmp_path: Path,
) -> None:
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "apps.yaml").write_text("ask_first_open: false\n")
    state = safety.page_state(tmp_path, StarlightConfig(), {})  # no module runs
    rows = {s["key"]: s for g in state["groups"] for s in g["switches"]}
    assert rows["failsafe"]["value"] is True and not rows["failsafe"]["module_on"]  # (default)
    assert rows["access_asks"]["value"] is True  # on, so no warning shown
    assert rows["first_open"]["value"] is False  # what its file says
