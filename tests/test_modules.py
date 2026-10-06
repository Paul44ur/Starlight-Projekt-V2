"""Step 3 'done when': adding a capability means dropping a folder into modules/."""

from __future__ import annotations

from starlight.core import Kernel, Paths
from starlight.core.modules import ModuleStatus
from tests.conftest import ModuleWriter

GOOD = """
    from pydantic import BaseModel
    from starlight.core import Module, tool

    class Config(BaseModel):
        word: str = "{word}"

    class Mod(Module):
        Config = Config
        subscriptions = ["ping"]
        pings = 0

        async def setup(self, ctx):
            self.cfg = ctx.config

        @tool
        def say(self) -> str:
            return self.cfg.word

        @tool
        def crash(self) -> str:
            raise ValueError("tool bug")

        async def on_event(self, event):
            type(self).pings += 1
            raise RuntimeError("handler bug")
"""


async def test_template_is_not_loaded(kernel: Kernel) -> None:
    assert "_template" not in kernel.modules.records
    assert {t.module for t in kernel.dispatcher.tools()} == {"memory", "skills"}


async def test_module_tools_config_and_events(paths: Paths, write_module: ModuleWriter) -> None:
    write_module("good", GOOD.replace("{word}", "default"))
    (paths.module_configs / "good.yaml").write_text("word: configured\n")
    async with Kernel(paths) as kernel:
        assert (await kernel.dispatcher.call("good.say")).output == "configured"
        result = await kernel.dispatcher.call("good.crash")
        assert not result.ok and "tool bug" in (result.error or "")
        await kernel.bus.publish("ping")  # handler raises; core keeps going
        mod = kernel.modules.records["good"].instance
        assert type(mod).pings == 1
        assert (await kernel.ask("still alive?")).answer == "You said: still alive?"
    assert (paths.logs / "modules" / "good.log").exists()


async def test_crashing_module_does_not_take_down_core(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module("good", GOOD.replace("{word}", "ok"))
    write_module(
        "broken_setup",
        """
        from starlight.core import Module
        class Mod(Module):
            async def setup(self, ctx):
                raise RuntimeError("setup bug")
    """,
    )
    write_module("broken_import", "import does_not_exist\n")
    write_module("bad_config", GOOD.replace("{word}", "x"))
    (paths.module_configs / "bad_config.yaml").write_text("unknown_field: 1\nword: [1]\n")
    (paths.modules / "bad_manifest").mkdir()
    (paths.modules / "bad_manifest" / "module.yaml").write_text("name: Not Valid\n")

    async with Kernel(paths) as kernel:
        records = kernel.modules.records
        assert records["good"].status is ModuleStatus.LOADED
        for name in ("broken_setup", "broken_import", "bad_config"):
            assert records[name].status is ModuleStatus.FAILED, name
        assert "setup bug" in (records["broken_setup"].error or "")
        assert "bad_manifest" in kernel.modules.errors
        module_tools = [
            t.qualified_name
            for t in kernel.dispatcher.tools()
            if t.module not in ("memory", "skills")
        ]
        assert module_tools == ["good.crash", "good.say"]


async def test_dependencies(paths: Paths, write_module: ModuleWriter) -> None:
    write_module("base", GOOD.replace("{word}", "b"))
    write_module("child", GOOD.replace("{word}", "c"), dependencies="[base]")
    write_module("orphan", GOOD.replace("{word}", "o"), dependencies="[missing]")
    write_module("cyc_a", GOOD.replace("{word}", "a"), dependencies="[cyc_b]")
    write_module("cyc_b", GOOD.replace("{word}", "b"), dependencies="[cyc_a]")
    async with Kernel(paths) as kernel:
        records = kernel.modules.records
        assert records["child"].status is ModuleStatus.LOADED
        assert "missing" in (records["orphan"].error or "")
        assert records["cyc_a"].status is ModuleStatus.FAILED
        assert records["cyc_b"].status is ModuleStatus.FAILED

        await kernel.modules.disable("base")  # dependents go too
        assert records["child"].status is ModuleStatus.DISABLED
        assert kernel.dispatcher.get("child.say") is None


async def test_hot_reload_and_cross_process_enable(
    paths: Paths, write_module: ModuleWriter
) -> None:
    folder = write_module("good", GOOD.replace("{word}", "v1"))
    async with Kernel(paths) as kernel:
        assert (await kernel.dispatcher.call("good.say")).output == "v1"
        (folder / "module.py").write_text(
            (folder / "module.py").read_text().replace('"v1"', '"v2"')
        )
        await kernel.modules.reload("good")
        assert (await kernel.dispatcher.call("good.say")).output == "v2"

        # `starlight modules disable good` from another terminal
        kernel.modules.state.set("good", False)
        await kernel.ask("anything")
        assert kernel.dispatcher.get("good.say") is None
        kernel.modules.state.set("good", True)
        await kernel.ask("anything")
        assert kernel.dispatcher.get("good.say") is not None


async def test_disabled_by_default(paths: Paths, write_module: ModuleWriter) -> None:
    write_module("quiet", GOOD.replace("{word}", "q"), enabled_by_default="false")
    async with Kernel(paths) as kernel:
        assert kernel.modules.records["quiet"].status is ModuleStatus.DISABLED
        record = await kernel.modules.enable("quiet")
        assert record.status is ModuleStatus.LOADED


async def test_relative_imports_inside_module(paths: Paths, write_module: ModuleWriter) -> None:
    folder = write_module(
        "pkg",
        """
        from starlight.core import Module, tool
        from .helpers import shout

        class Mod(Module):
            @tool
            def go(self) -> str:
                return shout("x")
    """,
    )
    (folder / "helpers.py").write_text("def shout(s):\n    return s.upper()\n")
    async with Kernel(paths) as kernel:
        assert (await kernel.dispatcher.call("pkg.go")).output == "X"
