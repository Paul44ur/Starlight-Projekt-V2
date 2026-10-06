"""Command line: ``starlight --help``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import FrameType
from typing import Any

import httpx

from starlight import __version__, console
from starlight.core.agent import AgentResult
from starlight.core.config import ConfigError, load_config
from starlight.core.dispatcher import Confirmer
from starlight.core.kernel import Kernel
from starlight.core.killswitch import KillSwitch
from starlight.core.logging import setup_logging
from starlight.core.model import TokenCallback, create_provider
from starlight.core.modules import NAME_PATTERN, ModuleState, discover
from starlight.core.output import resolve_output_folder
from starlight.core.paths import Paths
from starlight.core.permissions import PermissionPolicy
from starlight.core.stats import Stats
from starlight.core.tools import Tool
from starlight.core.vault import Vault, VaultError
from starlight.memory import Memory
from starlight.models.bench import benchmark
from starlight.models.serve import ServeError, run_server, serve_command
from starlight.skills import SkillManager
from starlight.skills.git import GitError

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
        answer = (await asyncio.to_thread(console.answer, prompt)).strip().lower()
        if answer in ("a", "always"):
            policy.allow_for_session(tool.qualified_name)
            return True
        return answer in ("y", "yes")

    return confirm


class TerminalUI:
    """Questions from the core and modules (confirmations, 2FA codes) in the terminal."""

    def __init__(self, assume_yes: bool = False) -> None:
        self.assume_yes = assume_yes

    async def confirm(self, question: str) -> bool:
        if self.assume_yes:
            return True
        if not sys.stdin.isatty():
            return False
        answer = await asyncio.to_thread(console.answer, f"\n{question} [y/N]: ")
        return answer.strip().lower() in ("y", "yes")

    async def ask(self, question: str, secret: bool = False) -> str | None:
        if not sys.stdin.isatty():
            return None
        if secret:
            console.drop_typeahead()
            return await asyncio.to_thread(getpass.getpass, f"\n{question}: ")
        return (await asyncio.to_thread(console.answer, f"\n{question}: ")).strip() or None

    async def notify(self, text: str) -> None:
        print(f"\n[starlight] {text}")


def _kernel(paths: Paths, assume_yes: bool = False, hotkey: bool = False) -> Kernel:
    kernel = Kernel(paths, hotkey=hotkey)
    kernel.set_ui(TerminalUI(assume_yes))
    kernel.set_confirmer(make_confirmer(kernel.policy, assume_yes))
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


async def _ask(kernel: Kernel, line: str, on_token: TokenCallback) -> AgentResult | None:
    """Run one request. Ctrl+C while Starlight answers stops that answer, not Starlight
    (it returns None then)."""
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(kernel.ask(line, keep_history=True, on_token=on_token))

    def stop(signum: int, frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(task.cancel)

    try:
        previous = signal.signal(signal.SIGINT, stop)
    except ValueError:  # not the main thread: keep the usual Ctrl+C
        previous = None
    try:
        return await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise  # Starlight itself is stopping
        return None
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


async def cmd_run(paths: Paths, args: argparse.Namespace) -> int:
    async with _kernel(paths, assume_yes=args.yes, hotkey=True) as kernel:
        print(f"Starlight {__version__}. Type /help for commands, /quit to exit.")
        print("Ctrl+C stops an answer while it is being written.")
        if kernel.kill_switch.engaged:
            print(
                f"The kill switch is still ON ({kernel.kill_switch.reason}): nothing will "
                "run until you type /resume."
            )
        hotkey = kernel.config.kill_switch.hotkey
        if kernel.hotkey_active:
            print(f"Kill hotkey: {hotkey}")
        elif hotkey:
            print(
                "Kill hotkey inactive (needs the 'hotkey' extra and a desktop session). "
                "/kill and `starlight kill` still work."
            )
        while True:
            try:
                line = (await asyncio.to_thread(console.read_request, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if await _repl_command(kernel, line):
                    break
                continue
            streamed: list[str] = []

            def on_token(token: str, streamed: list[str] = streamed) -> None:
                if not streamed:
                    print("starlight> ", end="", flush=True)
                streamed.append(token)
                print(token, end="", flush=True)

            result = await _ask(kernel, line, on_token)
            if result is None:
                print("\n[Stopped. Ask something else, or /quit to leave.]")
                continue
            # Streaming showed the answer (after any text from earlier tool steps).
            if streamed and "".join(streamed).strip().endswith(result.answer.strip()):
                print()
            else:
                if streamed:
                    print()
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
    print(f"  saves files:  {resolve_output_folder(config.output.folder, paths.root)}")
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


def _memory(paths: Paths) -> Memory:
    config = load_config(paths)
    paths.ensure()
    setup_logging(paths.logs, config.logging.level)
    return Memory(paths, config.memory)


async def cmd_memory(paths: Paths, args: argparse.Namespace) -> int:
    memory = _memory(paths)
    try:
        action, rest = args.action, " ".join(args.args)
        if action in ("show", "add", "search", "forget") and not rest:
            print(f"memory {action} needs an argument", file=sys.stderr)
            return 1
        if action == "list":
            entries = memory.store.all()
            for e in entries:
                print(f"  {e.id:<40} {e.date}  {e.topic}")
            print(f"{len(entries)} memories in {memory.store.root}")
        elif action == "show":
            entry = memory.store.get(rest)
            if entry is None:
                print(f"No memory '{rest}'.", file=sys.stderr)
                return 1
            print(entry.to_markdown())
        elif action == "add":
            entry, merged = await memory.remember(rest, args.topic or None)
            print(f"{'Updated' if merged else 'Saved'} {entry.id}")
        elif action == "search":
            for hit in await memory.recall(rest, top_k=10):
                print(f"  {hit.score:5.2f}  {hit.entry.id:<40} {hit.entry.content[:80]}")
        elif action == "forget":
            if not await memory.forget(rest):
                print(f"No memory '{rest}'.", file=sys.stderr)
                return 1
            print(f"Forgot {rest}")
        elif action == "export":
            target = memory.export(Path(rest or "memory-export.md"))
            print(f"Exported to {target}")
        elif action == "reindex":
            await memory.sync(force=True)
            print(f"Indexed {len(memory.index)} memories")
        return 0
    finally:
        memory.close()


def cmd_vault(paths: Paths, args: argparse.Namespace) -> int:
    vault = Vault(paths.config / "vault_index.yaml")
    try:
        if args.action == "list":
            for key in vault.names():
                print(f"  {key}")
        elif args.action == "set":
            if sys.stdin.isatty():
                value = getpass.getpass(f"Value for {args.key} (hidden): ")
            else:
                # e.g. a key file piped in; PowerShell adds Windows line endings
                value = sys.stdin.read().replace("\r\n", "\n").rstrip("\n")
            if not value:
                print("Empty value, nothing stored.", file=sys.stderr)
                return 1
            vault.set(args.key, value)
            print(f"Stored {args.key} in the OS keyring.")
        elif args.action == "delete":
            vault.delete(args.key)
            print(f"Deleted {args.key}.")
    except VaultError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_skills(paths: Paths, args: argparse.Namespace) -> int:
    config = load_config(paths)
    paths.ensure()
    setup_logging(paths.logs, config.logging.level)
    skills = SkillManager(paths, config.skills)
    action, name = args.action, args.name
    if action != "list" and not name:
        print(f"skills {action} needs a skill name", file=sys.stderr)
        return 1
    try:
        if action == "list":
            entries = skills.all()
            for s in entries:
                print(f"  {s.name:<28} {s.status:<9} v{s.version:<3} {s.description}")
            if not entries:
                print("No skills yet. They are drafted when a task succeeds again.")
        elif action == "show":
            skill = skills.get(name)
            if skill is None:
                print(f"No skill named '{name}'.", file=sys.stderr)
                return 1
            print(skill.to_markdown())
            if skill.example:
                print("--- example ---\n" + skill.example)
            for rel, code in skill.code.items():
                print(f"--- code/{rel} ---\n{code}")
        elif action == "approve":
            skill = skills.approve(name)
            print(f"{skill.name} v{skill.version} approved; it is used from now on.")
        elif action == "reject":
            skill = skills.reject(name)
            print(f"{skill.name} v{skill.version} rejected; it will not be used.")
        elif action == "history":
            for revision, date, message in skills.versions(name):
                print(f"  {revision}  {date}  {message}")
        elif action == "rollback":
            skill = skills.rollback(name, args.revision or None)
            print(f"{skill.name} restored to v{skill.version} ({skill.status}).")
        elif action == "promote":
            target = skills.promote(name, paths.modules)
            print(
                f"Module started at {target} (disabled). Turn the steps into tools, then "
                f"`starlight modules enable {target.name}`."
            )
    except (KeyError, FileExistsError, GitError) as exc:
        print(exc.args[0] if exc.args else str(exc), file=sys.stderr)
        return 1
    return 0


async def cmd_serve(paths: Paths, args: argparse.Namespace) -> int:
    """The core in the background: modules (WhatsApp webhook, watchers), kill hotkey,
    no terminal. Questions go to the channel a request came from."""
    import signal

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):  # Windows
            loop.add_signal_handler(sig, stop.set)
    from starlight.service import shutdown_file

    request = shutdown_file(paths.data, "core")
    request.unlink(missing_ok=True)  # a stale request must not stop this start
    async with Kernel(paths, hotkey=True) as kernel:
        print(f"Starlight core running from {paths.root}. Stop with Ctrl+C.", flush=True)
        if kernel.kill_switch.engaged:
            print(f"Note: the kill switch is engaged ({kernel.kill_switch.reason}).")
        while not stop.is_set():  # `starlight service stop` asks via the request file
            if request.exists():
                request.unlink(missing_ok=True)
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), 1.0)
    return 0


def cmd_viewer(paths: Paths, args: argparse.Namespace) -> int:
    from starlight.viewer.app import run

    config = load_config(paths)
    print(f"Statistics viewer on http://{config.viewer.host}:{config.viewer.port}", flush=True)
    run(paths)
    return 0


def cmd_logs(paths: Paths, args: argparse.Namespace) -> int:
    from starlight.core.secretscan import scan_logs

    vault = Vault(paths.config / "vault_index.yaml")
    try:
        secrets = [v for k in vault.names() if (v := vault.get(k))]
    except Exception:
        secrets = []
        print("(vault not readable: checking patterns only)", file=sys.stderr)
    findings = scan_logs(paths.logs, secrets)
    for finding in findings:
        print(f"  {finding.file.relative_to(paths.root)}:{finding.line}  {finding.kind}")
    if findings:
        print(
            f"{len(findings)} possible secret(s) in the logs. Delete those log files and "
            "rotate the affected secrets."
        )
        return 1
    print("No secrets found in the logs.")
    return 0


def cmd_service(paths: Paths, args: argparse.Namespace) -> int:
    from starlight import service
    from starlight.core.corelock import core_running

    if args.action == "status":
        config = load_config(paths)
        core = core_running(paths.data / "core.lock")
        url = f"http://{config.viewer.host}:{config.viewer.port}/api/summary"
        try:
            viewer = httpx.get(url, timeout=3).status_code == 200
        except httpx.HTTPError:
            viewer = False
        print(f"  core:    {'running' if core else 'not running'}")
        print(f"  viewer:  {'running at ' + url.rsplit('/api', 1)[0] if viewer else 'not running'}")
        if sys.platform == "win32":
            links = sorted(service.startup_folder().glob("Starlight *.lnk"))
            print(f"  autostart: {', '.join(p.stem for p in links) or 'off'}")
        else:
            subprocess.run(
                ["systemctl", "--user", "is-enabled", "starlight-core.service"], check=False
            )
        return 0
    service_plan = service.plan(paths.root, args.action, paths.data)
    for path, content in service_plan.files.items():
        print(f"# write {path}" + (f"\n{content}" if content.strip() != "stop" else ""))
    for path in service_plan.remove:
        print(f"# remove {path}")
    for command in service_plan.commands:
        print("$ " + " ".join(command))
    for command in service_plan.spawn:
        print("$ (background) " + " ".join(command))
    if args.print_only:
        return 0
    code = service.apply(service_plan)
    if args.action == "stop":
        print("Asked the core and the viewer to stop; they exit within a few seconds.")
    return code


async def cmd_model(paths: Paths, args: argparse.Namespace) -> int:
    config = load_config(paths)
    if args.action == "serve":
        try:
            cmd, env = serve_command(config.model)
        except ServeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for key, value in env.items():
            print(f"{key}={value}")
        print(" ".join(cmd))
        if args.print_only:
            return 0
        return run_server(config.model)
    if args.action == "info":
        print(json.dumps(config.model.model_dump(), indent=2))
        return 0
    # bench
    paths.ensure()
    setup_logging(paths.logs, config.logging.level)
    provider = create_provider(config.model)
    stats = Stats(paths.stats_db)
    try:
        warmup = getattr(provider, "warmup", None)
        if warmup is not None:
            await warmup()
        report = await benchmark(provider, runs=args.runs, stats=stats)
    finally:
        stats.close()
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
    print(json.dumps(report, indent=2))
    return 0


# -- entry point -------------------------------------------------------------------------


def _ensure_console(paths: Paths) -> None:
    """Started without a console (pythonw.exe at logon), Python has no stdout/stderr and
    anything that writes to them crashes (uvicorn does). Send them to logs/console.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    paths.logs.mkdir(parents=True, exist_ok=True)
    stream = open(paths.logs / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


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

    memory = sub.add_parser(
        "memory", help="the Memory folder: list, show, add, search, forget, export, reindex"
    )
    memory.add_argument(
        "action", choices=["list", "show", "add", "search", "forget", "export", "reindex"]
    )
    memory.add_argument("args", nargs="*", help="id, text, query or export file")
    memory.add_argument("--topic", default="", help="topic for `memory add`")

    vault = sub.add_parser("vault", help="secrets in the OS keyring: set, list, delete")
    vault.add_argument("action", choices=["set", "list", "delete"])
    vault.add_argument("key", nargs="?")

    skills = sub.add_parser(
        "skills", help="learned skills: list, show, approve, reject, history, rollback, promote"
    )
    skills.add_argument(
        "action",
        choices=["list", "show", "approve", "reject", "history", "rollback", "promote"],
    )
    skills.add_argument("name", nargs="?")
    skills.add_argument("revision", nargs="?", help="rollback: revision from `history`")

    sub.add_parser("serve", help="run the core in the background (WhatsApp, watchers)")
    sub.add_parser("viewer", help="the statistics viewer (a separate web app)")
    logs = sub.add_parser("logs", help="check the logs for leaked secrets")
    logs.add_argument("action", choices=["scan"])
    service_cmd = sub.add_parser("service", help="start core and viewer at logon")
    service_cmd.add_argument("action", choices=["install", "uninstall", "start", "stop", "status"])
    service_cmd.add_argument("--print-only", action="store_true", help="only show what it does")

    model = sub.add_parser("model", help="the local model: info, serve, bench")
    model.add_argument("action", choices=["info", "serve", "bench"])
    model.add_argument("--runs", type=int, default=3, help="benchmark runs")
    model.add_argument("--print-only", action="store_true", help="serve: only print the command")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "modules" and args.action != "list" and not args.name:
        parser.error(f"modules {args.action} needs a module name")
    if args.command == "vault" and args.action != "list" and not args.key:
        parser.error(f"vault {args.action} needs a key name")
    paths = Paths.discover(args.home)
    _ensure_console(paths)
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
            case "memory":
                return asyncio.run(cmd_memory(paths, args))
            case "vault":
                return cmd_vault(paths, args)
            case "serve":
                return asyncio.run(cmd_serve(paths, args))
            case "viewer":
                return cmd_viewer(paths, args)
            case "logs":
                return cmd_logs(paths, args)
            case "service":
                return cmd_service(paths, args)
            case "skills":
                return cmd_skills(paths, args)
            case "model":
                return asyncio.run(cmd_model(paths, args))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
