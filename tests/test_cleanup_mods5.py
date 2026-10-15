"""Fixes from the cleanup of office, pdf, recon, system and translate: text stays in order
when a huge paragraph is split, Office files outside the Starlight folder are only read
after a yes, close_app does not match by loose substring, CPU ranking measures over a
moment, recon scans a port once, and blocking file work stays off the event loop."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
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
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


def module_of(k: Kernel, name: str) -> Any:
    instance = k.modules.records[name].instance
    assert instance is not None, k.modules.records[name].error
    return instance


def namespace_of(k: Kernel, name: str) -> ModuleType:
    """The loaded module.py of a module, to reach its helper functions."""
    return sys.modules[type(module_of(k, name)).__module__]


# -- translate ---------------------------------------------------------------------------


async def test_split_keeps_the_order_around_a_huge_paragraph(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("translate")
    async with Kernel(paths, ui=User()) as k:
        split = namespace_of(k, "translate").split
    huge = " ".join(f"Sentence number {i} is here." for i in range(40))  # about 1200 characters
    parts = split(f"SHORT ONE\n\n{huge}\n\nLAST", 300)
    assert parts[0] == "SHORT ONE"  # the earlier paragraph comes out first
    assert parts[-1].endswith("LAST")
    assert " ".join(parts).count("Sentence number") == 40
    joined = " ".join(parts)
    assert all(
        joined.index(f"Sentence number {i} ") < joined.index(f"Sentence number {i + 1} ")
        for i in range(39)
    )
    assert split("one\n\ntwo\n\nthree", 100) == ["one\n\ntwo\n\nthree"]  # normal texts unchanged
    assert split("a" * 60 + "\n\n" + "b" * 60, 100) == ["a" * 60, "b" * 60]


# -- office ------------------------------------------------------------------------------


@pytest.fixture
def office(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    pytest.importorskip("docx")
    pytest.importorskip("openpyxl")
    use_module("office")
    return paths


async def test_reading_office_files_outside_the_starlight_folder_asks(
    office: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    import docx
    import openpyxl

    elsewhere = tmp_path_factory.mktemp("elsewhere")
    document = docx.Document()
    document.add_paragraph("Secret plan")
    document.save(str(elsewhere / "plan.docx"))
    book = openpyxl.Workbook()
    book.active.append(["a", 1])
    book.save(str(elsewhere / "sheet.xlsx"))

    refuse = User(False, False)
    async with Kernel(office, ui=refuse) as k:
        for tool_name, name in (("read_docx", "plan.docx"), ("read_xlsx", "sheet.xlsx")):
            result = await k.dispatcher.call(f"office.{tool_name}", {"path": str(elsewhere / name)})
            assert not result.ok and "did not allow" in str(result.error)
    assert len(refuse.questions) == 2

    allow = User(True, True)
    async with Kernel(office, ui=allow) as k:
        doc = await k.dispatcher.call("office.read_docx", {"path": str(elsewhere / "plan.docx")})
        xls = await k.dispatcher.call("office.read_xlsx", {"path": str(elsewhere / "sheet.xlsx")})
    assert doc.ok and "Secret plan" in doc.output["text"]
    assert xls.ok and xls.output["rows"] == [["a", 1]]
    assert len(allow.questions) == 2


async def test_reading_inside_the_starlight_folder_does_not_ask(office: Paths) -> None:
    user = User()
    async with Kernel(office, ui=user) as k:
        await k.dispatcher.call("office.write_xlsx", {"name": "mine", "rows": [["x", 2]]})
        out = await k.dispatcher.call("office.read_xlsx", {"path": "mine.xlsx"})
    assert out.ok and out.output["rows"] == [["x", 2]]
    assert user.questions == []


async def test_office_work_runs_off_the_event_loop(
    office: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    async with Kernel(office, ui=User()) as k:
        helpers = namespace_of(k, "office")
        real = helpers._save_xlsx

        def spy(*args: Any) -> None:
            seen.append(threading.current_thread().name)
            real(*args)

        monkeypatch.setattr(helpers, "_save_xlsx", spy)
        saved = await k.dispatcher.call("office.write_xlsx", {"name": "spy", "rows": [[1]]})
    assert saved.ok and seen and seen[0] != threading.main_thread().name


# -- pdf ---------------------------------------------------------------------------------


async def test_pdf_text_is_read_only_until_the_limit(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("pdf")
    calls: list[int] = []

    class Page:
        def __init__(self, number: int) -> None:
            self.number = number

        def extract_text(self) -> str:
            calls.append(self.number)
            return f" page {self.number} " + "x" * 60 if self.number != 1 else ""

    class Reader:
        def __init__(self, path: str) -> None:
            self.pages = [Page(n) for n in range(6)]

    async with Kernel(paths, ui=User()) as k:
        extract = namespace_of(k, "pdf")._extract_text
    count, text = extract(SimpleNamespace(PdfReader=Reader), Path("x.pdf"), 100)
    assert count == 6  # the real page count is still reported
    assert calls == [0, 1, 2]  # page 1 has no text; reading stopped once 100 characters were in
    assert text.startswith("page 0 ") and "page 2 " in text and "page 3" not in text


async def test_pdf_is_rendered_off_the_event_loop(
    paths: Paths, use_module: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("reportlab")
    use_module("pdf")
    seen: list[str] = []
    async with Kernel(paths, ui=User()) as k:
        helpers = namespace_of(k, "pdf")

        def spy(file: Path, title: str, content: str) -> None:
            seen.append(threading.current_thread().name)
            file.write_bytes(b"%PDF-1.4")

        monkeypatch.setattr(helpers, "_render_pdf", spy)
        result = await k.dispatcher.call("pdf.to_pdf", {"content": "hi", "path": "spy.pdf"})
    assert result.ok and seen and seen[0] != threading.main_thread().name


# -- recon -------------------------------------------------------------------------------


@pytest.fixture
def recon_project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("recon")
    (paths.config / "servers.yaml").write_text("servers: {}\n")
    return paths


async def test_recon_scans_a_port_once(recon_project: Paths) -> None:
    async with Kernel(recon_project, ui=User(), vault_backend=MemoryBackend()) as k:
        module = module_of(k, "recon")
        common = namespace_of(k, "recon").COMMON
        default = module._port_list("", False)
    ports = [p for p, _ in common]
    assert len(ports) == len(set(ports))  # no port is listed twice
    assert len(default) == len(set(default)) == module.config.top_ports
    assert module._port_list("22 22, 80 80 22", False) == [22, 80]


async def test_recon_resolves_names_off_the_event_loop(recon_project: Paths) -> None:
    seen: list[str] = []
    async with Kernel(recon_project, ui=User(), vault_backend=MemoryBackend()) as k:
        module = module_of(k, "recon")

        def resolve(host: str) -> list[str]:
            seen.append(threading.current_thread().name)
            return ["127.0.0.1"]

        module._resolve = resolve
        out = await module.dns("example.invalid")
    assert out["addresses"] == ["127.0.0.1"]
    assert seen and seen[0] != threading.main_thread().name


async def test_recon_http_probe_closes_cleanly(recon_project: Paths) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(200)
        writer.write(b"HTTP/1.1 200 OK\r\nServer: tiny\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    async with server, Kernel(recon_project, ui=User(), vault_backend=MemoryBackend()) as k:
        module = module_of(k, "recon")
        found = await module._http("127.0.0.1", "127.0.0.1", port)
        assert found["http"]["server"] == "tiny" and "200 OK" in found["http_status"]
        server.close()
        await server.wait_closed()
        assert await module._http("127.0.0.1", "127.0.0.1", port) == {}  # refused: no crash


# -- system ------------------------------------------------------------------------------


class FakeProc:
    def __init__(self, pid: int, name: str, rss: int = 1000, cpu: float = 0.0) -> None:
        self.pid = pid
        self.info = {"pid": pid, "name": name, "memory_info": SimpleNamespace(rss=rss)}
        self.cpu = cpu
        self.reads = 0
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def cpu_percent(self, interval: float | None = None) -> float:
        self.reads += 1
        return 0.0 if self.reads == 1 else self.cpu  # the first reading is always 0.0


class FakePsutil:
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    def __init__(self, *procs: FakeProc) -> None:
        self.procs = list(procs)

    def process_iter(self, attrs: list[str] | None = None) -> list[FakeProc]:
        return list(self.procs)


@pytest.fixture
def system_project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("system")
    return paths


async def test_close_app_prefers_the_exact_name_and_ignores_tiny_names(
    system_project: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    chrome = FakeProc(10, "chrome.exe")
    driver = FakeProc(11, "chromedriver.exe")
    word = FakeProc(12, "WINWORD.EXE")
    me = FakeProc(os.getpid(), "python")
    parent = FakeProc(os.getppid(), "bash")
    fake = FakePsutil(chrome, driver, word, me, parent)
    async with Kernel(system_project, ui=User()) as k:
        module = module_of(k, "system")
        monkeypatch.setattr(namespace_of(k, "system"), "_psutil", lambda: fake)
        assert module.close_app("Chrome")["pids"] == [10]  # not chromedriver
        assert module.close_app("word")["pids"] == [12]  # no exact match: a part of the name
        for short in ("e", "py", "ba"):  # too short to guess from, and never Starlight's own
            with pytest.raises(Exception, match="no running program"):
                module.close_app(short)
        for own in ("python", "bash"):
            with pytest.raises(Exception, match="no running program"):
                module.close_app(own)
    assert chrome.terminated and word.terminated
    assert not (driver.terminated or me.terminated or parent.terminated)


async def test_processes_by_cpu_measures_over_a_moment(
    system_project: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet = FakeProc(1, "quiet", rss=5_000_000, cpu=1.0)
    busy = FakeProc(2, "busy", rss=1_000, cpu=80.0)
    fake = FakePsutil(quiet, busy)
    async with Kernel(system_project, ui=User()) as k:
        module = module_of(k, "system")
        monkeypatch.setattr(namespace_of(k, "system"), "_psutil", lambda: fake)
        by_cpu = module.processes(by="cpu")
        by_memory = module.processes(by="memory")
    assert [p["name"] for p in by_cpu] == ["busy", "quiet"]
    assert by_cpu[0]["cpu_percent"] == 80.0
    assert [p["name"] for p in by_memory] == ["quiet", "busy"]


async def test_processes_fallback_sorts_by_memory_on_windows(
    system_project: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasklist = (
        '"System Idle Process","0","Services","0","8 K"\r\n'
        '"notepad.exe","20","Console","1","9.000 K"\r\n'
        '"chrome.exe","10","Console","1","120.512 K"\r\n'
    )
    async with Kernel(system_project, ui=User()) as k:
        module = module_of(k, "system")
        helpers = namespace_of(k, "system")
        with monkeypatch.context() as patch:  # no await inside: nothing else sees the patch
            patch.setattr(sys, "platform", "win32")
            patch.setattr(
                helpers.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=tasklist)
            )
            rows = module._processes_fallback(2)
        monkeypatch.setattr(helpers, "_psutil", lambda: None)
        with pytest.raises(Exception, match="needs the 'system' extra"):
            module.processes(by="cpu")
    assert [r["name"] for r in rows] == ["chrome.exe", "notepad.exe"]
    assert rows[0]["pid"] == 10 and rows[0]["memory"] == "117.7 MB"
