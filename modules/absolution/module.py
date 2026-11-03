"""Absolution: a cockpit for security tools you already have, for testing your OWN systems,
systems you are authorized to test, and files and captures you already hold.

It is defensive and reconnaissance tooling only (modules/absolution/tools.py): port and web
scanners, cloud and code checks, capture and memory analysis, and reverse-engineering
programs. It does NOT drive credential theft, lateral movement, command-and-control,
poisoning or evasion tooling - those are refused by name (``tools.BLOCKED``).

Three rules hold for everything here:

* **It asks first, every time.** Before any program runs, Starlight shows you the exact
  command and waits for your yes. There is no "run without asking" for this module.
* **Network targets need your authorization.** A host, IP, range or URL must be in your
  Absolution scope first (``absolution.authorize``), which warns you when a target is not
  on your own network. Files on this PC and local questions do not need that.
* **Nothing is installed for you.** Each tool is used only if it is already on this PC; a
  missing one is reported, not fetched.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool

from . import tools as catalog

SCOPE_FILE = "scope.json"  # hosts/ranges/domains the user authorized for network tools
# A single extra flag: letters, digits and the punctuation flags use. No spaces (so it
# stays one argv word), no shell metacharacters (it never reaches a shell anyway).
_EXTRA = re.compile(r"^[A-Za-z0-9._:/@=,+%-]+$")
_HOST = re.compile(r"^[A-Za-z0-9._:\[\]-]+(?:/\d{1,3})?$")  # host, IP, IPv6 in [...], or CIDR
_QUERY_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # control chars (not tab/newline)


class Config(BaseModel):
    run_timeout: float = Field(default=600, gt=0, le=24 * 3600)  # seconds a tool may run
    max_output: int = Field(default=20000, ge=200, le=2_000_000)  # chars kept from output
    max_extra: int = Field(default=40, ge=0, le=200)  # extra flags at most
    wordlist: str = ""  # a file the web fuzzers use ({w}); empty: those tools say to set it


class AbsolutionModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self._run = catalog.by_key()
        self._launch = catalog.launch_by_key()

    # -- the authorization scope ---------------------------------------------------------

    def _scope(self) -> list[str]:
        path = self.ctx.data_dir / SCOPE_FILE
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return sorted({str(h) for h in data}) if isinstance(data, list) else []

    def _save_scope(self, scope: list[str]) -> None:
        path = self.ctx.data_dir / SCOPE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(set(scope)), indent=2), encoding="utf-8")

    @staticmethod
    def _host_of(target: str) -> str:
        """The bare host of a target: the host of a URL, the address before a CIDR suffix or
        a port, with an IPv6 address's brackets taken off."""
        raw = target.strip()
        if "://" in raw:
            return (urlsplit(raw).hostname or "").strip("[]")
        host = raw.split("/")[0]  # drop a CIDR suffix
        return (urlsplit("//" + host).hostname or host).strip("[]")  # drop a :port

    def _check_target(self, primary: str, target: str) -> str:
        """Validate the one argument for its kind; returns it cleaned. Raises ToolError with
        a plain reason. Network targets are also checked against the scope."""
        value = target.strip()
        if primary == "none":
            if value:
                raise ToolError("this tool takes no target; put anything it needs in extra")
            return ""
        if not value:
            raise ToolError("give the target (a host, a file, an image or a query)")
        if value.startswith("-"):
            raise ToolError("the target cannot start with '-'")
        if primary == "path":
            path = Path(value).expanduser()
            if not path.exists():
                raise ToolError(f"there is no file or folder at {value}")
            return str(path)
        if primary == "image":
            if not _EXTRA.match(value) or " " in value:
                raise ToolError("an image reference is like repo/name:tag, no spaces")
            return value
        if primary == "query":
            if _QUERY_BAD.search(value) or len(value) > 500:
                raise ToolError("the query has odd characters or is too long")
            return value
        # a network host: a plain host/IP/range, or an http(s) URL whose host is in scope
        if "://" in value:
            parts = urlsplit(value)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ToolError("a URL must be http:// or https:// with a host")
            if " " in value:
                raise ToolError("a URL cannot contain a space")
        elif " " in value or not _HOST.match(value):
            raise ToolError(f"{value!r} is not one host, IP, range or URL")
        if not self._in_scope(value):
            raise ToolError(
                f"{self._host_of(value)} is not in your Absolution scope. Add it first with "
                "absolution.authorize (it warns you if the target is not on your own network)."
            )
        return value

    def _in_scope(self, target: str) -> bool:
        host = self._host_of(target).casefold()
        for entry in self._scope():
            allowed = entry.casefold()
            if host == allowed or host.endswith("." + allowed):
                return True
            with contextlib.suppress(ValueError):
                net = ipaddress.ip_network(entry, strict=False)
                address = ipaddress.ip_address(self._host_of(target))
                if address.version == net.version and address in net:
                    return True
                inside = ipaddress.ip_network(target, strict=False)  # the whole CIDR target
                if (
                    inside.version == net.version
                    and inside.network_address in net
                    and inside.broadcast_address in net
                ):
                    return True
        return False

    def _clean_extra(self, extra: list[str] | None) -> list[str]:
        items = extra or []
        if len(items) > self.config.max_extra:
            raise ToolError(f"too many extra flags (at most {self.config.max_extra})")
        cleaned = []
        for token in items:
            text = str(token)
            if not _EXTRA.match(text) or len(text) > 200:
                raise ToolError(f"{text!r} is not a plain flag (one word, no spaces)")
            cleaned.append(text)
        return cleaned

    # -- listing and scope tools ---------------------------------------------------------

    @tool(name="tools")
    def list_tools(self, category: str = "") -> dict[str, Any]:
        """The security tools Absolution can run, and which are installed on this PC.
        `category`: only one of recon, web, vuln, cloud, code, dfir, re (empty: all). It
        does not install anything; a missing tool is just reported."""
        want = category.strip().lower()
        runnable = []
        for spec in catalog.RUN:
            if want and spec.category != want:
                continue
            runnable.append({
                "key": spec.key, "name": spec.name, "category": spec.category,
                "takes": spec.primary, "installed": self._found(spec.exes) is not None,
                "what": spec.note,
            })  # fmt: skip
        launchable = [
            {"key": g.key, "name": g.name, "category": g.category,
             "installed": self._found(g.exes) is not None, "what": g.note}
            for g in catalog.LAUNCH if not want or g.category == want
        ]  # fmt: skip
        result: dict[str, Any] = {
            "run": runnable,
            "launch": launchable,
            "scope": self._scope(),
            "note": "Each run asks first and shows the exact command. Network targets must be "
            "in the scope (absolution.authorize). Offensive tooling is not included.",
        }
        if not want:
            result["not_wired"] = catalog.NOT_WIRED
        return result

    @tool
    def scope(self) -> dict[str, Any]:
        """The hosts, ranges and domains you authorized for Absolution's network tools."""
        return {"authorized": self._scope()}

    @tool
    async def authorize(self, target: str) -> dict[str, Any]:
        """Add a host, IP, range (CIDR) or domain to the Absolution scope, so the network
        tools may be pointed at it. Asks first, and warns you when the target is not on your
        own/local network. Only authorize systems you own or have written permission to
        test."""
        value = target.strip().rstrip("/")
        host = self._host_of(value)
        if not value or " " in value or value.startswith("-") or not _HOST.match(value):
            raise ToolError("give one host, IP, range (CIDR) or domain")
        if self._in_scope(value):
            return {"already": value, "scope": self._scope()}
        public = self._looks_public(host)
        where = (
            f"{host} looks like a PUBLIC target. Testing a system you do not own or have "
            "written permission to test can be illegal."
            if public
            else f"{host} looks like a private/local target."
        )
        if not await self.ctx.ui.confirm(
            f"Authorize Absolution to point network tools at {value}?\n  {where}\n"
            "  Only confirm for systems you own or are authorized to test."
        ):
            raise ToolError("not confirmed; the scope was not changed")
        self._save_scope([*self._scope(), value])
        self.ctx.logger.info("absolution scope added", target=value, public=public)
        return {"authorized": value, "public": public, "scope": self._scope()}

    @tool
    def forget(self, target: str) -> dict[str, Any]:
        """Remove a host, range or domain from the Absolution scope."""
        value = target.strip().rstrip("/")
        scope = self._scope()
        if value not in scope:
            raise ToolError(f"{value} is not in the scope; see absolution.scope")
        self._save_scope([e for e in scope if e != value])
        return {"forgotten": value, "scope": self._scope()}

    @staticmethod
    def _looks_public(host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return "." in host and not host.endswith((".local", ".lan", ".internal", ".home"))
        return not (ip.is_private or ip.is_loopback or ip.is_link_local)

    # -- running a tool ------------------------------------------------------------------

    @staticmethod
    def _found(exes: tuple[str, ...]) -> str | None:
        for name in exes:
            path = shutil.which(name)
            if path:
                return path
        return None

    @staticmethod
    def _refuse_if_blocked(key: str) -> None:
        name = key.strip().lower()
        reason = catalog.BLOCKED.get(name) or next(
            (why for blocked, why in catalog.BLOCKED.items() if blocked in name or name in blocked),
            None,
        )
        if reason:
            raise ToolError(f"Starlight will not run '{key}': {reason}.")

    @tool(name="run", own_timeout=True)
    async def run_tool(
        self, tool: str, target: str = "", extra: list[str] | None = None
    ) -> dict[str, Any]:
        """Run one security tool from absolution.tools on a target you are allowed to test.
        `tool` is a key from absolution.tools (e.g. "nmap", "nikto", "trivy", "semgrep").
        `target` is its one argument: a host/IP/range/URL (must be authorized first), a file
        or folder on this PC, a container image, or a query - see each tool's "takes".
        `extra` adds plain flags (each one word). Starlight shows the exact command and asks
        before anything runs; it never runs on its own. Offensive tooling is refused."""
        key = tool.strip().lower()
        self._refuse_if_blocked(key)
        spec = self._run.get(key)
        if spec is None:
            if key in self._launch:
                raise ToolError(f"'{key}' is opened with absolution.launch, not run")
            hint = catalog.NOT_WIRED.get(key)
            raise ToolError(
                f"no tool '{tool}' in Absolution"
                + (f" ({hint})" if hint else "; see absolution.tools")
            )
        exe = self._found(spec.exes)
        if exe is None:
            raise ToolError(
                f"{spec.name} is not installed (looked for {', '.join(spec.exes)}). Install it "
                "yourself; Starlight does not fetch security tools."
            )
        cleaned_extra = self._clean_extra(extra)
        if spec.need_extra and not cleaned_extra:
            raise ToolError(f"{spec.name} needs extra (e.g. {spec.note}).")
        primary = self._check_target(spec.primary, target)
        argv = self._build(spec, exe, primary, cleaned_extra)
        shown = " ".join(argv)
        if not await self.ctx.ui.confirm(
            f"Run this security tool now?\n  {shown}\n  Only on systems you are authorized to test."
        ):
            raise ToolError("not confirmed; nothing was run")
        self.ctx.kill_switch.check()
        self.ctx.logger.info("absolution run", tool=key, target=self._host_of(target) or primary)
        return await self._exec(spec, argv)

    def _build(self, spec: catalog.Tool, exe: str, primary: str, extra: list[str]) -> list[str]:
        argv = [exe]
        for part in spec.run:
            if part == "{t}":
                argv.append(primary)
            elif part == "{w}":
                if not self.config.wordlist.strip():
                    raise ToolError(
                        f"{spec.name} needs a wordlist: set 'wordlist' in "
                        "config/modules/absolution.yaml"
                    )
                argv.append(self.config.wordlist.strip())
            else:
                argv.append(part)
        argv += extra
        return argv

    async def _exec(self, spec: catalog.Tool, argv: list[str]) -> dict[str, Any]:
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=flags,
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            raise ToolError(f"could not start {spec.name}: {exc}") from exc
        timed_out = False
        try:
            async with asyncio.timeout(self.config.run_timeout):
                out, _ = await process.communicate()
        except TimeoutError:
            timed_out = True
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            out, _ = await process.communicate()
        text = out.decode("utf-8", errors="replace")
        if len(text) > self.config.max_output:
            text = "…" + text[-self.config.max_output :]
        if self.ctx.stats is not None:
            self.ctx.stats.record("absolution.runs", 1)
        return {
            "tool": spec.key,
            "name": spec.name,
            "command": " ".join(argv),
            "exit_code": process.returncode,
            "ok": process.returncode == 0 and not timed_out,
            "timed_out": timed_out,
            "output": text or "(the tool wrote nothing)",
        }

    @tool
    async def launch(self, tool: str) -> dict[str, Any]:
        """Open a security program that you drive yourself (Burp Suite, OWASP ZAP, Wireshark,
        Ghidra, IDA, x64dbg). Starlight only opens it, as it opens any program; it does not
        use it. Asks first."""
        key = tool.strip().lower()
        self._refuse_if_blocked(key)
        spec = self._launch.get(key)
        if spec is None:
            if key in self._run:
                raise ToolError(f"'{key}' is run with absolution.run, not opened")
            raise ToolError(f"no program '{tool}' to open; see absolution.tools")
        exe = self._found(spec.exes)
        if exe is None:
            raise ToolError(f"{spec.name} is not installed (looked for {', '.join(spec.exes)}).")
        if not await self.ctx.ui.confirm(f"Open {spec.name}?"):
            raise ToolError("not confirmed; nothing was opened")
        try:
            await asyncio.create_subprocess_exec(
                exe,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            raise ToolError(f"could not open {spec.name}: {exc}") from exc
        self.ctx.logger.info("absolution launch", program=spec.key)
        return {"opened": spec.name}
