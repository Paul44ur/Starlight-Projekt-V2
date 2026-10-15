"""``starlight doctor``: is everything Starlight needs in place, and if not, what to do.

Each check returns ok / warn / fail with a plain-language detail and, when something is
wrong, one thing to try. Nothing here changes anything: it only looks (and writes a tiny
temporary file into each data folder to see that it can).
"""

from __future__ import annotations

import contextlib
import socket
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlight.core.config import ConfigError, StarlightConfig, load_config
from starlight.core.paths import Paths

GB = 1024**3
MB = 1024**2


@dataclass
class Check:
    name: str
    status: str  # "ok", "warn" or "fail"
    detail: str
    hint: str = ""

    @property
    def mark(self) -> str:
        return {"ok": "[ok]  ", "warn": "[!]   ", "fail": "[FAIL]"}[self.status]


def ok(name: str, detail: str) -> Check:
    return Check(name, "ok", detail)


def warn(name: str, detail: str, hint: str = "") -> Check:
    return Check(name, "warn", detail, hint)


def fail(name: str, detail: str, hint: str = "") -> Check:
    return Check(name, "fail", detail, hint)


# -- the checks ----------------------------------------------------------------------------------


def check_python() -> Check:
    return ok("Python", ".".join(str(part) for part in sys.version_info[:3]))


def check_folders(paths: Paths) -> list[Check]:
    """Can Starlight write where it keeps conversations, memory and logs?"""
    results = []
    for label, folder in (
        ("data folder", paths.data),
        ("memory folder", paths.memory),
        ("log folder", paths.logs),
    ):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / f".doctor-{uuid.uuid4().hex[:8]}"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            results.append(
                fail(label, f"{folder} cannot be written to ({exc})", "check the folder's rights")
            )
        else:
            results.append(ok(label, str(folder)))
    return results


def check_disk(paths: Paths, usage: Callable[[Path], Any] | None = None) -> Check:
    import shutil

    free = (usage or shutil.disk_usage)(paths.root).free
    text = f"{free / GB:.1f} GB free on the drive with Starlight"
    if free < 0.5 * GB:
        return fail("disk space", text, "free some space: conversations and logs need to be saved")
    if free < 5 * GB:
        return warn("disk space", text, "a model download needs 5-10 GB")
    return ok("disk space", text)


def check_config(paths: Paths) -> tuple[Check, StarlightConfig | None]:
    try:
        config = load_config(paths)
    except ConfigError as exc:
        return fail("settings", str(exc), "fix that file, or delete the setting it names"), None
    except Exception as exc:  # a file that is not even readable YAML
        return fail("settings", f"{type(exc).__name__}: {exc}", "check config/*.yaml"), None
    return ok("settings", f"loaded; model provider '{config.model.provider}'"), config


def _model_base(config: StarlightConfig) -> tuple[str, dict[str, Any]]:
    """(provider, options) of the main model, looking inside a router."""
    provider, options = config.model.provider, dict(config.model.options)
    if provider == "router":
        main = options.get("main") or {}
        provider, options = str(main.get("provider", "")), dict(main.get("options") or {})
    return provider, options


def _get(url: str, timeout: float = 3.0) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(getattr(exc, "reason", exc))


def check_model(
    config: StarlightConfig, paths: Paths, get: Callable[[str], tuple[int | None, str]] = _get
) -> list[Check]:
    provider, options = _model_base(config)
    if provider in ("", "echo"):
        return [
            warn(
                "model",
                "the built-in test model (it only repeats what you say)",
                "start Starlight.exe without --no-model to use a real model",
            )
        ]
    results: list[Check] = []
    server = config.model.server
    if provider == "llamacpp" and server.model_path and not Path(server.model_path).is_file():
        results.append(
            fail(
                "model file",
                f"{server.model_path} is missing",
                "start Starlight.exe: it downloads the model again",
            )
        )
    elif provider == "llamacpp" and server.model_path:
        size = Path(server.model_path).stat().st_size
        results.append(ok("model file", f"{Path(server.model_path).name} ({size / GB:.1f} GB)"))
    # The addresses the providers use when model.options.base_url is not set.
    default = {
        "ollama": "http://127.0.0.1:11434",
        "llamacpp": "http://127.0.0.1:8080/v1",
        "openai_compat": "http://127.0.0.1:8080/v1",
    }
    base = str(options.get("base_url") or default.get(provider, ""))
    base = base.rstrip("/").removesuffix("/v1")
    if not base:
        results.append(warn("model server", "no address configured", "set model.options.base_url"))
        return results
    # Only llama-server has /health. Another OpenAI-compatible server (LM Studio, vLLM)
    # may not: any answer from its model list means it is up.
    path = {"ollama": "/api/tags", "llamacpp": "/health"}.get(provider, "/v1/models")
    status, reason = get(base + path)
    answers = path == "/v1/models" and status is not None and status < 500
    if status == 200 or answers:
        results.append(ok("model server", f"answers at {base}"))
    elif status == 503:
        results.append(warn("model server", "is up but still loading the model", "wait a minute"))
    else:
        problem = _server_log_problem(paths)
        detail = f"does not answer at {base} ({reason or 'HTTP ' + str(status)})"
        results.append(
            fail(
                "model server",
                detail + (f"; its log says: {problem}" if problem else ""),
                "start Starlight.exe (it starts the server), or run `starlight model serve`",
            )
        )
    return results


def _server_log_problem(paths: Paths) -> str:
    """The last line in the model server's log that looks like a real problem."""
    log = paths.logs / "llama-server.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")[-6000:]
    except OSError:
        return ""
    words = ("error", "failed", "out of memory", "cannot", "unable", "abort")
    lines = [ln.strip() for ln in text.splitlines() if any(w in ln.lower() for w in words)]
    return lines[-1][:200] if lines else ""


def check_ports(config: StarlightConfig) -> Check:
    def listening(port: int) -> bool:
        with contextlib.closing(socket.socket()) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    web = config.web.port
    return ok("ports", f"chat page {web}: {'in use' if listening(web) else 'free'}")


def check_keyring() -> Check:
    try:
        import keyring

        backend = keyring.get_keyring()
    except Exception as exc:
        return warn("password store", f"unavailable ({exc})", "mail/login passwords can't be saved")
    name = type(backend).__name__
    if "fail" in name.lower() or "null" in name.lower():
        return warn(
            "password store",
            f"no usable keyring ({name})",
            "passwords for mail and logins can't be saved",
        )
    return ok("password store", name)


def check_modules(paths: Paths) -> Check:
    from starlight.core.modules import ModuleState, discover

    found, errors = discover(paths.modules)
    state = ModuleState(paths.module_state_file)
    enabled = [n for n, (m, _) in found.items() if state.is_enabled(m)]
    if errors:
        names = ", ".join(sorted(errors))
        return warn(
            "modules", f"{len(enabled)} enabled, {len(errors)} invalid: {names}",
            "see `starlight modules list` for each error",
        )  # fmt: skip
    return ok("modules", f"{len(enabled)} enabled")


def check_kill_switch(paths: Paths, config: StarlightConfig) -> Check:
    if (paths.root / config.kill_switch.stop_file).exists():
        return warn("kill switch", "is engaged: Starlight will not act", "run `starlight resume`")
    return ok("kill switch", "off")


def check_moved_aside(paths: Paths) -> Check:
    """Lists the app wrote aside because they could not be read (see core/jsonfile.py)."""
    aside = sorted(paths.data.rglob("*.unreadable-*.json")) if paths.data.is_dir() else []
    if aside:
        return warn(
            "damaged files",
            f"{len(aside)} list file(s) were unreadable and moved aside, e.g. {aside[0].name}",
            "open it in a text editor to repair it, then copy the entries back",
        )
    return ok("damaged files", "none")


def check_sizes(paths: Paths) -> Check:
    def folder_size(folder: Path) -> int:
        total = 0
        for item in folder.rglob("*") if folder.is_dir() else []:
            with contextlib.suppress(OSError):
                if item.is_file():
                    total += item.stat().st_size
        return total

    logs, data = folder_size(paths.logs), folder_size(paths.data)
    text = f"logs {logs / MB:.0f} MB, data {data / MB:.0f} MB"
    if logs + data > 2 * GB:
        return warn("growth", text, "old logs and statistics can be deleted (logs\\, data\\stats*)")
    return ok("growth", text)


# -- running them --------------------------------------------------------------------------------


def run_checks(
    paths: Paths,
    get: Callable[[str], tuple[int | None, str]] = _get,
) -> list[Check]:
    results = [check_python(), *check_folders(paths), check_disk(paths)]
    config_check, config = check_config(paths)
    results.append(config_check)
    if config is not None:
        results += check_model(config, paths, get)
        results.append(check_ports(config))
        results.append(check_kill_switch(paths, config))
    results += [
        check_keyring(),
        check_modules(paths),
        check_moved_aside(paths),
        check_sizes(paths),
    ]
    return results


def format_checks(results: list[Check]) -> str:
    lines = []
    for check in results:
        lines.append(f"{check.mark} {check.name:<15} {check.detail}")
        if check.hint and check.status != "ok":
            lines.append(f"{'':22} -> {check.hint}")
    failed = sum(c.status == "fail" for c in results)
    warned = sum(c.status == "warn" for c in results)
    if failed:
        summary = f"{failed} problem(s) to fix" + (f", {warned} to look at" if warned else "")
    elif warned:
        summary = f"working, {warned} thing(s) to look at"
    else:
        summary = "everything looks fine"
    return "\n".join([*lines, "", summary])


def exit_code(results: list[Check]) -> int:
    return 1 if any(c.status == "fail" for c in results) else 0
