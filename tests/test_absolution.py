"""The Absolution module (modules/absolution): a cockpit for security tools you already have,
for your own or authorized systems. It asks before every run and shows the exact command;
network targets must be authorized first; it never installs anything; and it refuses the
offensive credential-theft / lateral-movement / C2 / evasion cluster by name."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.vault import MemoryBackend


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False

    async def ask(self, q: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("absolution")  # the module ships off by default; enable it for the tests
    paths.module_state_file.write_text("enabled: [absolution]\n")
    return paths


class FakeProc:
    def __init__(self, out: bytes = b"ran", code: int = 0) -> None:
        self._out, self.returncode = out, code

    async def communicate(self) -> tuple[bytes, None]:
        return self._out, None

    def kill(self) -> None:  # (for the timeout path)
        self.returncode = -9


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record the argv of each program 'started', without starting anything."""
    calls: list[list[str]] = []

    async def fake_exec(*argv: str, **kwargs: Any) -> FakeProc:
        calls.append([str(a) for a in argv])
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def installed(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Make only these program names look present on PATH."""
    want = set(names)
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}" if name in want else None
    )


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


# -- the catalogue is data, and the offensive cluster is excluded --------------------------


def _catalogue() -> Any:
    import importlib.util
    import sys

    from tests.conftest import REPO

    name = "absolution_tools"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, REPO / "modules" / "absolution" / "tools.py"
        )
        assert spec is not None and spec.loader is not None
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded  # (dataclass resolves annotations through sys.modules)
        spec.loader.exec_module(loaded)
    return sys.modules[name]


def test_the_catalogue_is_consistent_and_leaves_offensive_tooling_out() -> None:
    catalog = _catalogue()

    keys = [t.key for t in catalog.RUN] + [g.key for g in catalog.LAUNCH]
    assert len(keys) == len(set(keys))  # every key once
    for spec in catalog.RUN:
        assert spec.exes and spec.primary in ("host", "path", "image", "query", "none")
        assert all(part in ("{t}", "{w}") or not part.startswith("{") for part in spec.run)
    assert all(g.exes for g in catalog.LAUNCH)
    # none of the run/launch keys is an offensive tool
    assert not (set(keys) & set(catalog.BLOCKED))
    for name in ("mimikatz", "responder", "crackmapexec", "metasploit", "cobaltstrike",
                 "hashcat", "evil-winrm", "bettercap", "chisel", "proxychains"):  # fmt: skip
        assert name in catalog.BLOCKED


async def test_list_tools_shows_what_is_installed(
    project: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed(monkeypatch, "nmap", "semgrep")
    async with Kernel(project, ui=User(), vault_backend=MemoryBackend()) as k:
        listed = await call(k, "absolution.tools")
        by_key = {t["key"]: t for t in listed["run"]}
        assert by_key["nmap"]["installed"] is True and by_key["nikto"]["installed"] is False
        assert by_key["semgrep"]["installed"] is True
        assert listed["scope"] == [] and "offensive" in listed["note"].lower()
        assert "mimikatz" not in by_key and "metasploit" not in by_key
        assert "kali" in listed["not_wired"]  # an OS, named with the reason
        web = await call(k, "absolution.tools", category="web")
        assert all(t["category"] == "web" for t in web["run"])


# -- authorization scope -------------------------------------------------------------------


async def test_a_network_tool_needs_an_authorized_target(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch, "nmap")
    async with Kernel(project, ui=User(True, True), vault_backend=MemoryBackend()) as k:
        out = await k.dispatcher.call("absolution.run", {"tool": "nmap", "target": "10.0.0.5"})
        assert not out.ok and "not in your Absolution scope" in str(out.error)
        assert ran == []  # nothing ran

        user = User(True)  # authorize a private host (a note, not a legality warning)
        async with Kernel(project, ui=user, vault_backend=MemoryBackend()) as k2:
            done = await call(k2, "absolution.authorize", target="10.0.0.5")
            assert done["authorized"] == "10.0.0.5" and done["public"] is False
        assert "private/local" in user.questions[0]


async def test_authorizing_a_public_target_warns_about_legality(
    project: Paths,
) -> None:
    user = User(False)  # say no
    async with Kernel(project, ui=user, vault_backend=MemoryBackend()) as k:
        refused = await k.dispatcher.call("absolution.authorize", {"target": "scanme.nmap.org"})
        assert not refused.ok and "not confirmed" in str(refused.error)
    assert "PUBLIC target" in user.questions[0] and "illegal" in user.questions[0]
    assert (project.data / "modules" / "absolution" / "scope.json").exists() is False

    user2 = User(True)
    async with Kernel(project, ui=user2, vault_backend=MemoryBackend()) as k:
        out = await call(k, "absolution.authorize", target="scanme.nmap.org")
        assert out["public"] is True and "scanme.nmap.org" in out["scope"]
        assert (await call(k, "absolution.forget", target="scanme.nmap.org"))["scope"] == []


# -- running a tool ------------------------------------------------------------------------


async def test_run_asks_then_runs_and_shows_the_command(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch, "nmap")
    user = User(True, True)  # authorize, then run
    async with Kernel(project, ui=user, vault_backend=MemoryBackend()) as k:
        await call(k, "absolution.authorize", target="192.168.0.0/24")
        out = await call(
            k, "absolution.run", tool="nmap", target="192.168.0.10", extra=["-p", "443"]
        )
        assert out["ok"] and out["exit_code"] == 0
        assert ran == [["/usr/bin/nmap", "-sV", "-Pn", "192.168.0.10", "-p", "443"]]
        assert "nmap -sV -Pn 192.168.0.10 -p 443" in user.questions[-1]  # the exact command
        assert "authorized to test" in user.questions[-1]


async def test_run_refused_without_a_yes_does_nothing(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch, "nikto")
    async with Kernel(project, ui=User(True, False), vault_backend=MemoryBackend()) as k:
        await call(k, "absolution.authorize", target="10.1.2.3")
        refused = await k.dispatcher.call("absolution.run", {"tool": "nikto", "target": "10.1.2.3"})
        assert not refused.ok and "not confirmed" in str(refused.error)
    assert ran == []


async def test_run_refuses_offensive_tools_by_name(project: Paths) -> None:
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        for bad in ("mimikatz", "crackmapexec", "metasploit", "responder", "evil-winrm"):
            out = await k.dispatcher.call("absolution.run", {"tool": bad, "target": "10.0.0.1"})
            assert not out.ok and "will not run" in str(out.error), bad
        sneaky = await k.dispatcher.call("absolution.run", {"tool": "run-mimikatz-now"})
        assert not sneaky.ok and "will not run" in str(sneaky.error)


async def test_run_reports_a_missing_tool_without_installing(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch)  # nothing installed
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        await call(k, "absolution.authorize", target="10.0.0.9")
        out = await k.dispatcher.call("absolution.run", {"tool": "nmap", "target": "10.0.0.9"})
        assert not out.ok and "not installed" in str(out.error)
    assert ran == []


async def test_a_file_tool_needs_a_file_that_exists(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]], tmp_path: Path
) -> None:
    installed(monkeypatch, "tshark")
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        gone = await k.dispatcher.call(
            "absolution.run", {"tool": "tshark", "target": str(tmp_path / "nope.pcap")}
        )
        assert not gone.ok and "no file or folder" in str(gone.error)
        capture = tmp_path / "c.pcap"
        capture.write_bytes(b"\xd4\xc3\xb2\xa1")
        out = await call(k, "absolution.run", tool="tshark", target=str(capture))
        assert out["ok"] and ran[-1] == ["/usr/bin/tshark", "-r", str(capture)]


async def test_a_wordlist_tool_asks_for_the_wordlist(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch, "gobuster")
    async with Kernel(project, ui=User(True, True), vault_backend=MemoryBackend()) as k:
        await call(k, "absolution.authorize", target="10.0.0.0/8")
        no_list = await k.dispatcher.call(
            "absolution.run", {"tool": "gobuster", "target": "http://10.0.0.1"}
        )
        assert not no_list.ok and "wordlist" in str(no_list.error)
    (project.module_configs / "absolution.yaml").write_text("wordlist: /tmp/words.txt\n")
    async with Kernel(project, ui=User(True, True), vault_backend=MemoryBackend()) as k:
        await call(k, "absolution.authorize", target="10.0.0.0/8")
        out = await call(k, "absolution.run", tool="gobuster", target="http://10.0.0.1")
        assert "/tmp/words.txt" in ran[-1] and out["ok"]


async def test_targets_and_extra_flags_cannot_smuggle_in_arguments(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch, "nmap")
    async with Kernel(project, ui=User(True, True, True, True), vault_backend=MemoryBackend()) as k:
        await call(k, "absolution.authorize", target="10.0.0.0/8")
        for bad in ("-oN /etc/passwd", "10.0.0.1 10.0.0.2", "-sS"):
            out = await k.dispatcher.call("absolution.run", {"tool": "nmap", "target": bad})
            assert not out.ok, bad
        flag = await k.dispatcher.call(
            "absolution.run", {"tool": "nmap", "target": "10.0.0.1", "extra": ["a b"]}
        )
        assert not flag.ok and "plain flag" in str(flag.error)
    assert ran == []


async def test_non_network_tools_run_without_a_scope(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]], tmp_path: Path
) -> None:
    installed(monkeypatch, "searchsploit", "osqueryi", "trivy")
    async with Kernel(project, ui=User(True, True, True), vault_backend=MemoryBackend()) as k:
        terms = await call(k, "absolution.run", tool="searchsploit", extra=["apache", "2.4"])
        assert terms["ok"] and ran[-1] == ["/usr/bin/searchsploit", "apache", "2.4"]
        await call(k, "absolution.run", tool="osquery", target="select * from users")
        assert ran[-1] == ["/usr/bin/osqueryi", "--json", "select * from users"]
        img = await call(k, "absolution.run", tool="trivy", target="nginx:1.27")
        assert ran[-1] == ["/usr/bin/trivy", "image", "nginx:1.27"] and img["ok"]


async def test_searchsploit_needs_terms(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch, "searchsploit")
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        out = await k.dispatcher.call("absolution.run", {"tool": "searchsploit"})
        assert not out.ok and "needs extra" in str(out.error)
    assert ran == []


# -- launching GUI tools -------------------------------------------------------------------


async def test_launch_opens_a_gui_tool_and_refuses_the_rest(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch, "wireshark")
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        out = await call(k, "absolution.launch", tool="wireshark")
        assert out["opened"] == "Wireshark" and ran[-1] == ["/usr/bin/wireshark"]
        blocked = await k.dispatcher.call("absolution.launch", {"tool": "mimikatz"})
        assert not blocked.ok and "will not run" in str(blocked.error)
        wrong = await k.dispatcher.call("absolution.launch", {"tool": "nmap"})
        assert not wrong.ok and "absolution.run" in str(wrong.error)


async def test_launch_reports_a_missing_program(
    project: Paths, monkeypatch: pytest.MonkeyPatch, ran: list[list[str]]
) -> None:
    installed(monkeypatch)
    async with Kernel(project, ui=User(True), vault_backend=MemoryBackend()) as k:
        out = await k.dispatcher.call("absolution.launch", {"tool": "ghidra"})
        assert not out.ok and "not installed" in str(out.error)
    assert ran == []


# -- the launcher's report -----------------------------------------------------------------


def test_the_launcher_reports_which_tools_are_installed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_launcher import L

    monkeypatch.setattr(
        shutil, "which", lambda n, *a, **k: "/usr/bin/nmap" if n == "nmap" else None
    )
    report = dict(L.security_tools_report())
    assert report["Nmap"] == "/usr/bin/nmap" and report["Nikto"] is None
    assert set(report) == set(L.SECURITY_TOOLS)
    assert not ({"Mimikatz", "Metasploit", "Responder"} & set(report))  # no offensive tools
    assert L.main(["--security-tools"]) == 0  # prints and exits, installs nothing
    out = capsys.readouterr().out
    assert "Nmap" in out and "will not drive" in out
    assert "mimikatz" not in out.lower() and "metasploit" not in out.lower()
