"""Command line: ``starlight --help``."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from collections.abc import Sequence
from typing import Any

from starlight import __version__
from starlight.core.config import ConfigError, load_config
from starlight.core.dispatcher import Confirmer
from starlight.core.kernel import Kernel
from starlight.core.killswitch import KillSwitch
from starlight.core.logging import setup_logging
from starlight.core.modules import NAME_PATTERN, ModuleState, discover
from starlight.core.paths import Paths
from starlight.core.permissions import PermissionPolicy
from starlight.core.tools import Tool

TEMPLATE = "_template"


def make_confirmer(policy: PermissionPolicy, assume_yes: bool) -> Confirmer:
    async def confirm(tool: Tool, args: dict[str, Any]) -> bool:
        if assume_yes:
            return True
        if not sys.stdin.isatty():
            return False
        prompt = (
            f"\nStarlight wants to run {tool.qualified_name}({json.dumps(args, default=str)})\n"
            "Allow? [y]es / [n]o / [a]lways this session: "
        )
        answer = (await asyncio.to_thread(input, prompt)).strip().lower()
        if answer in ("a", "always"):
            policy.allow_for_session(tool.qualified_name)
            return True
        return answer in ("y", "yes")

    return confirm


def _kernel(paths: Paths, assume_yes: bool = False, hotkey: bool = False) -> Kernel:
    kernel = Kernel(paths, hotkey=hotkey)
    kernel.dispatcher.confirmer = make_confirmer(kernel.policy, assume_yes)
    return kernel


def _kill_switch(paths: Paths, audited: bool = False) -> KillSwitch:
    config = load_config(paths)
    if audited:  # so `starlight kill` / `resume` land in logs/audit.log
        paths.ensure()
        setup_logging(
            paths.logs, config.logging.level, config.logging.max_bytes, config.logging.backups
        )
    return KillSwitch(paths.root / config.kill_switch.stop_file)


# -- commands ----------------------------------------------------------------------------


async def cmd_ask(paths: Paths, args: argparse.Namespace) -> int:
    async with _kernel(paths, assume_yes=args.yes) as kernel:
        result = await kernel.ask(" ".join(args.text))
    print(result.answer)
    if result.stopped:
        print("Kill switch is engaged. Run `starlight resume` to continue.", file=sys.stderr)
        return 2
    return 0


async def cmd_run(paths: Paths, args: argparse.Namespace) -> int:
    async with _kernel(paths, assume_yes=args.yes, hotkey=True) as kernel:
        print(f"Starlight {__version__}. Type /help for commands, /quit to exit.")
        hotkey = kernel.config.kill_switch.hotkey
        if kernel.hotkey_active:
            print(f"Kill hotkey: {hotkey}")
        elif hotkey:
            print("Kill hotkey inactive (install the 'hotkey' extra). /kill still works.")
        while True:
            try:
                line = (await asyncio.to_thread(input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if await _repl_command(kernel, line):
                    break
                continue
            result = await kernel.ask(line, keep_history=True)
            print(f"starlight> {result.answer}")
            if result.stopped:
                print("Kill switch is engaged. Type /resume to continue.")
    return 0


async def _repl_command(kernel: Kernel, line: str) -> bool:
    """Handle a /command. Returns True to quit."""
    command, _, rest = line[1:].partition(" ")
    rest = rest.strip()
    if command in ("quit", "exit"):
        return True
    if command == "help":
        print(
            "/tools            list tools\n"
            "/modules          list modules\n"
            "/reload NAME      hot-reload a module\n"
            "/enable NAME      enable a module\n"
            "/disable NAME     disable a module\n"
            "/kill [REASON]    engage the kill switch\n"
            "/resume           release the kill switch\n"
            "/clear            forget this conversation\n"
            "/quit             exit\n"
            "!module.tool {json}   call a tool directly (echo provider)"
        )
    elif command == "tools":
        _print_tools(kernel.dispatcher.tools())
    elif command == "modules":
        for name, record in sorted(kernel.modules.records.items()):
            print(f"  {name:<16} {record.status.value:<9} {record.error or ''}")
    elif command in ("reload", "enable", "disable") and rest:
        try:
            if command == "disable":
                await kernel.modules.disable(rest)
                print(f"{rest}: disabled")
            else:
                op = kernel.modules.reload if command == "reload" else kernel.modules.enable
                record = await op(rest)
                print(f"{rest}: {record.status.value} {record.error or ''}")
        except KeyError as exc:
            print(exc.args[0])
    elif command == "kill":
        kernel.kill_switch.trigger(rest or "REPL /kill", source="cli")
        print("Kill switch engaged.")
    elif command == "resume":
        kernel.kill_switch.reset()
        print("Kill switch released.")
    elif command == "clear":
        kernel.history = []
    else:
        print(f"Unknown command: /{command}. Try /help.")
    return False


def _print_tools(tools: Sequence[Tool]) -> None:
    if not tools:
        print("No tools registered.")
    for t in tools:
        summary = t.description.splitlines()[0] if t.description else ""
        print(f"  {t.qualified_name:<24} [{t.permission.value}] {summary}")


async def cmd_tools(paths: Paths, args: argparse.Namespace) -> int:
    async with _kernel(paths) as kernel:
        _print_tools(kernel.dispatcher.tools())
    return 0


def cmd_kill(paths: Paths, args: argparse.Namespace) -> int:
    reason = " ".join(args.reason) or "starlight kill"
    _kill_switch(paths, audited=True).trigger(reason, source="cli")
    print(f"Kill switch engaged: {reason}")
    return 0


def cmd_resume(paths: Paths, args: argparse.Namespace) -> int:
    _kill_switch(paths, audited=True).reset()
    print("Kill switch released.")
    return 0


def cmd_status(paths: Paths, args: argparse.Namespace) -> int:
    config = load_config(paths)
    switch = _kill_switch(paths)
    print(f"Starlight {__version__}")
    print(f"  home:         {paths.root}")
    print(f"  model:        {config.model.provider}")
    kill_state = f"ENGAGED ({switch.reason})" if switch.engaged else "off"
    print(f"  kill switch:  {kill_state}")
    found, errors = discover(paths.modules)
    state = ModuleState(paths.module_state_file)
    enabled = [n for n, (m, _) in found.items() if state.is_enabled(m)]
    print(f"  modules:      {', '.join(enabled) or 'none'} enabled, {len(errors)} invalid")
    return 0


def cmd_modules(paths: Paths, args: argparse.Namespace) -> int:
    found, errors = discover(paths.modules)
    state = ModuleState(paths.module_state_file)
    if args.action == "list":
        for name, (manifest, _) in sorted(found.items()):
            mark = "enabled " if state.is_enabled(manifest) else "disabled"
            print(f"  {name:<16} {mark} v{manifest.version:<8} {manifest.description}")
        for name, error in sorted(errors.items()):
            print(f"  {name:<16} INVALID  {error}")
        return 0
    if args.action == "new":
        return _new_module(paths, args.name)
    if args.name not in found:
        print(f"No module named '{args.name}' in {paths.modules}", file=sys.stderr)
        return 1
    state.set(args.name, args.action == "enable")
    print(f"{args.name}: {args.action}d (a running core picks this up before its next task)")
    return 0


def _new_module(paths: Paths, name: str | None) -> int:
    if not name or not re.match(NAME_PATTERN, name):
        print(
            "Module names use lowercase letters, digits and '_' (e.g. 'weather').", file=sys.stderr
        )
        return 1
    source, target = paths.modules / TEMPLATE, paths.modules / name
    if not source.is_dir():
        print(f"Template not found: {source}", file=sys.stderr)
        return 1
    if target.exists():
        print(f"{target} already exists.", file=sys.stderr)
        return 1
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
    class_name = "".join(part.capitalize() for part in name.split("_")) + "Module"
    for file in target.rglob("*"):
        if file.is_file() and file.suffix in (".py", ".yaml", ".md"):
            text = file.read_text(encoding="utf-8")
            text = text.replace("TemplateModule", class_name).replace("template", name)
            file.write_text(text, encoding="utf-8")
    print(f"Created {target}. Edit module.py, then run `starlight tools` to see its tools.")
    return 0


# -- entry point -------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="starlight", description="Starlight V2 local assistant")
    parser.add_argument("--version", action="version", version=f"starlight {__version__}")
    parser.add_argument("--home", help="project folder (default: $STARLIGHT_HOME or search up)")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="interactive session (kill hotkey active)")
    run.add_argument("-y", "--yes", action="store_true", help="approve confirmations")

    ask = sub.add_parser("ask", help="run one request and print the answer")
    ask.add_argument("text", nargs="+")
    ask.add_argument("-y", "--yes", action="store_true", help="approve confirmations")

    kill = sub.add_parser("kill", help="engage the kill switch (stops a running core)")
    kill.add_argument("reason", nargs="*")
    sub.add_parser("resume", help="release the kill switch")
    sub.add_parser("status", help="show configuration and kill-switch state")
    sub.add_parser("tools", help="list the tools the AI can call")

    modules = sub.add_parser("modules", help="list, enable, disable or create modules")
    modules.add_argument("action", choices=["list", "enable", "disable", "new"])
    modules.add_argument("name", nargs="?")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "modules" and args.action != "list" and not args.name:
        parser.error(f"modules {args.action} needs a module name")
    paths = Paths.discover(args.home)
    try:
        match args.command:
            case "ask":
                return asyncio.run(cmd_ask(paths, args))
            case "run":
                return asyncio.run(cmd_run(paths, args))
            case "tools":
                return asyncio.run(cmd_tools(paths, args))
            case "kill":
                return cmd_kill(paths, args)
            case "resume":
                return cmd_resume(paths, args)
            case "status":
                return cmd_status(paths, args)
            case "modules":
                return cmd_modules(paths, args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
