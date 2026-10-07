"""Starlight launcher: downloads what Starlight needs, then starts the AI.

Compiled into Starlight.exe (see .github/workflows/launcher.yml), so it uses only the
Python standard library. Put Starlight.exe in the Starlight folder and double-click it:

* first run: shows what it will download and how big it is, asks once, then installs
  uv, Python 3.12 with every package, the Chromium browser, the llama.cpp model server
  (Vulkan build: AMD, NVIDIA and Intel GPUs) and the model into the Starlight folder
  (uv keeps Python, and Playwright keeps Chromium, in the user profile); no administrator
  rights needed
* every run: starts the model server on the GPU, the statistics viewer, and the chat;
  closing the window stops everything it started

Downloads are checked against published SHA-256 checksums when the source offers them,
and interrupted downloads resume where they stopped.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
USER_AGENT = f"StarlightLauncher/{VERSION}"

UV_DOWNLOAD = "https://github.com/astral-sh/uv/releases/latest/download"
LLAMA_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
LLAMA_RELEASES = "https://github.com/ggml-org/llama.cpp/releases"

# 16 GB VRAM: a 14B model at Q4_K_M (~9 GB) plus a 32k context with q8 KV cache.
DEFAULT_MODEL_URL = (
    "https://huggingface.co/bartowski/Qwen2.5-14B_Uncensored_Instruct-GGUF/resolve/main/"
    "Qwen2.5-14B_Uncensored_Instruct-Q4_K_M.gguf"
)
GB = 1024**3
WEB_PORT = 8700  # the local web chat (matches config web.port)
MB = 1024**2


class LauncherError(Exception):
    """A problem the user can act on; shown without a traceback."""


# -- settings --------------------------------------------------------------------------


@dataclass
class Settings:
    root: Path
    model_url: str = DEFAULT_MODEL_URL
    context: int = 32768
    variant: str = "vulkan"  # llama.cpp build: vulkan (any GPU) or cpu
    llama_release: str = "latest"  # or a tag such as "b6500"
    port: int = 8080
    viewer: bool = True
    web: bool = True  # open the browser chat; False: the terminal chat
    use_model: bool = True  # False: Starlight's built-in test model, nothing downloaded
    assume_yes: bool = False
    update: bool = False
    tesseract: bool | None = None  # None: ask (Windows)
    github_token: str | None = field(default=None, repr=False)

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def model_file(self) -> Path:
        name = self.model_url.rstrip("/").split("/")[-1].split("?")[0]
        return self.models / name


def find_root(start: Path) -> Path:
    """The Starlight folder: where pyproject.toml names the project "starlight"."""
    for folder in (start, *start.parents):
        project = folder / "pyproject.toml"
        if project.is_file() and re.search(
            r'^name\s*=\s*"starlight"', project.read_text(encoding="utf-8"), re.MULTILINE
        ):
            return folder
    raise LauncherError(
        f"No Starlight folder found at or above {start}. Put Starlight.exe into the "
        "Starlight folder (next to pyproject.toml) and start it from there."
    )


def load_settings(args: argparse.Namespace) -> Settings:
    if args.root:
        start = Path(args.root).resolve()
    elif getattr(sys, "frozen", False):  # Starlight.exe
        start = Path(sys.executable).resolve().parent
    else:
        start = Path(__file__).resolve().parent
    settings = Settings(root=find_root(start))
    saved = settings.root / "config" / "launcher.json"  # optional, for your own defaults
    if saved.is_file():
        try:
            for key, value in json.loads(saved.read_text(encoding="utf-8")).items():
                if key in (
                    "model_url",
                    "context",
                    "variant",
                    "llama_release",
                    "port",
                    "viewer",
                    "web",
                    "tesseract",
                ):
                    setattr(settings, key, value)
        except (OSError, ValueError) as exc:
            raise LauncherError(f"{saved} is not valid JSON: {exc}") from exc
    for key in ("model_url", "context", "variant", "llama_release", "port"):
        value = getattr(args, key)
        if value is not None:
            setattr(settings, key, value)
    settings.viewer = settings.viewer and not args.no_viewer
    if args.terminal:
        settings.web = False
    settings.use_model = not args.no_model
    settings.assume_yes = args.yes
    settings.update = args.update
    if args.tesseract:
        settings.tesseract = True
    settings.github_token = os.environ.get("GITHUB_TOKEN") or None
    # Keep uv's cache on the same drive as the install, so it can hardlink packages into .venv
    # instead of copying them (which is slow and prints "Failed to hardlink files" when the
    # cache is on another drive, e.g. the default C:\Users\...\AppData while Starlight is on D:).
    # The cache then lives inside the Starlight folder. A user-set UV_CACHE_DIR is respected.
    os.environ.setdefault("UV_CACHE_DIR", str(settings.root / ".uv-cache"))
    return settings


# -- output ----------------------------------------------------------------------------


class Ui:
    def __init__(self, assume_yes: bool) -> None:
        self.assume_yes = assume_yes

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def step(self, text: str) -> None:
        print(f"\n== {text}", flush=True)

    def progress(self, done: int, total: int | None, started: float) -> None:
        speed = done / max(time.monotonic() - started, 1e-6) / MB
        if total:
            line = f"   {done / MB:8.1f} / {total / MB:.1f} MB  ({speed:.1f} MB/s)"
        else:
            line = f"   {done / MB:8.1f} MB  ({speed:.1f} MB/s)"
        print("\r" + line, end="", flush=True)

    def ask(self, question: str, default: bool) -> bool:
        if self.assume_yes:
            return default
        hint = "[Y/n]" if default else "[y/N]"
        try:
            answer = input(f"{question} {hint} ").strip().lower()
        except EOFError:
            return default
        return default if not answer else answer in ("y", "yes", "j", "ja")


# -- downloads -------------------------------------------------------------------------


def _request(
    url: str, token: str | None = None, method: str = "GET", **headers: str
) -> urllib.request.Request:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **headers}, method=method
    )
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", f"Bearer {token}")
    return request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass
class RemoteFile:
    size: int | None
    sha256: str | None


def probe(url: str) -> RemoteFile:
    """Size and checksum without downloading. Hugging Face tells both on the first
    (redirecting) response: X-Linked-Size and X-Linked-Etag (the SHA-256)."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = _request(url, method="HEAD")
    try:
        with opener.open(request, timeout=30) as response:
            headers = response.headers
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise LauncherError(f"{url}: HTTP {exc.code}") from exc
        headers = exc.headers
        if not headers.get("X-Linked-Size"):  # a plain redirect: ask the target
            location = headers.get("Location")
            if location:
                with urllib.request.urlopen(_request(location, method="HEAD"), timeout=30) as r:
                    return RemoteFile(_int(r.headers.get("Content-Length")), None)
    size = _int(headers.get("X-Linked-Size") or headers.get("Content-Length"))
    etag = (headers.get("X-Linked-Etag") or "").strip('"').lower()
    return RemoteFile(size, etag if re.fullmatch(r"[0-9a-f]{64}", etag) else None)


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * MB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    dest: Path,
    ui: Ui,
    sha256: str | None = None,
    token: str | None = None,
    attempts: int = 4,
) -> Path:
    """Download to ``dest``, resuming a previous ``.part`` file, and check the SHA-256."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with urllib.request.urlopen(_request(url, token, **headers), timeout=60) as response:
                if have and response.status != 206:  # the server ignored the range
                    have = 0
                length = _int(response.headers.get("Content-Length"))
                total = have + length if length is not None else None
                started = time.monotonic()
                done = have
                last = 0.0
                with part.open("ab" if have else "wb") as out:
                    while chunk := response.read(MB):
                        out.write(chunk)
                        done += len(chunk)
                        if time.monotonic() - last > 0.5:
                            ui.progress(done - have, (total - have) if total else None, started)
                            last = time.monotonic()
                ui.progress(done - have, (total - have) if total else None, started)
                ui.say()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have:  # already complete
                break
            if exc.code in (401, 403, 404) or attempt == attempts:
                raise LauncherError(f"download failed: {url} (HTTP {exc.code})") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt == attempts:
                raise LauncherError(f"download failed: {url} ({exc})") from exc
        ui.say(f"   connection problem, resuming (try {attempt + 1} of {attempts}) ...")
        time.sleep(2 * attempt)
    if sha256:
        ui.say("   checking the SHA-256 checksum ...")
        actual = sha256_of(part)
        if actual != sha256.lower():
            part.unlink(missing_ok=True)
            raise LauncherError(
                f"checksum mismatch for {dest.name}: expected {sha256}, got {actual}. "
                "The broken file was deleted; start again to re-download it."
            )
    os.replace(part, dest)
    return dest


def extract_member(archive: Path, name: str, target: Path) -> Path:
    """Unpack the folder of the file called ``name`` (e.g. llama-server.exe) into
    ``target``, flattening whatever folder structure the archive has."""
    with tempfile.TemporaryDirectory(dir=archive.parent) as tmp:
        tmp_path = Path(tmp)
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                _safe_extract_zip(zf, tmp_path)
        else:
            with tarfile.open(archive) as tf:
                tf.extractall(tmp_path, filter="data")
        found = next((p for p in tmp_path.rglob(name) if p.is_file()), None)
        if found is None:
            raise LauncherError(f"{archive.name} does not contain {name}")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(found.parent, target)
    binary = target / name
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | 0o111)
    return binary


def _safe_extract_zip(archive: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for member in archive.infolist():
        path = (dest / member.filename).resolve()
        if not path.is_relative_to(root):
            raise LauncherError(f"unsafe path in archive: {member.filename}")
    archive.extractall(dest)


# -- what to install -------------------------------------------------------------------


def exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def uv_asset() -> str:
    machine = platform.machine().lower()
    if os.name == "nt":
        return (
            "uv-aarch64-pc-windows-msvc.zip"
            if "arm" in machine
            else ("uv-x86_64-pc-windows-msvc.zip")
        )
    if sys.platform == "darwin":
        return (
            "uv-aarch64-apple-darwin.tar.gz"
            if machine == "arm64"
            else ("uv-x86_64-apple-darwin.tar.gz")
        )
    return (
        "uv-aarch64-unknown-linux-gnu.tar.gz"
        if machine in ("aarch64", "arm64")
        else ("uv-x86_64-unknown-linux-gnu.tar.gz")
    )


def ensure_uv(settings: Settings, ui: Ui) -> Path:
    """Starlight's own copy of uv in tools/uv (it installs Python and the packages)."""
    binary = settings.tools / "uv" / exe("uv")
    if binary.is_file() and not settings.update:
        return binary
    ui.step("Downloading uv (installs Python and Starlight's packages)")
    asset = uv_asset()
    with urllib.request.urlopen(_request(f"{UV_DOWNLOAD}/{asset}.sha256"), timeout=30) as r:
        expected = r.read().decode().split()[0]
    archive = download(f"{UV_DOWNLOAD}/{asset}", settings.tools / asset, ui, sha256=expected)
    extract_member(archive, exe("uv"), settings.tools / "uv")
    archive.unlink()
    return binary


def llama_asset_names(variant: str) -> tuple[str, ...]:
    """The end of the llama.cpp release file name for this computer and build, newest
    naming first (the Linux builds moved from .zip to .tar.gz)."""
    if os.name == "nt":
        return (f"bin-win-{variant}-x64.zip",)
    stem = "bin-ubuntu-x64" if variant == "cpu" else f"bin-ubuntu-{variant}-x64"
    return (f"{stem}.tar.gz", f"{stem}.zip")


def pick_llama_asset(release: dict[str, Any], variant: str) -> tuple[str, str | None]:
    """(download URL, SHA-256 or None) of the right file in a GitHub release."""
    wanted = llama_asset_names(variant)
    for asset in release.get("assets") or []:
        name = str(asset.get("name", ""))
        if name.startswith("llama-") and name.endswith(wanted):
            digest = str(asset.get("digest") or "")
            sha = digest.removeprefix("sha256:") if digest.startswith("sha256:") else None
            return str(asset["browser_download_url"]), sha
    names = ", ".join(str(a.get("name")) for a in release.get("assets") or [])
    raise LauncherError(f"no llama.cpp download matching *{wanted[0]} (found: {names or 'none'})")


def _read_text(url: str, token: str | None = None) -> str:
    with urllib.request.urlopen(_request(url, token), timeout=30) as response:
        return str(response.read().decode("utf-8", "replace"))


def _nightly_tag(text: str) -> str | None:
    tag = text.strip()
    return tag if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", tag) else None


def resolve_llama(settings: Settings) -> tuple[str, str | None]:
    """Ask GitHub's API for the release; if that is blocked or rate-limited, fall back
    to the release page and the known file naming.

    llama.cpp's versioned releases (the one GitHub calls "latest") carry no programs,
    only nightly-tag.txt: the name of the b#### build release that has them."""
    tag, token = settings.llama_release, settings.github_token
    try:
        api = f"{LLAMA_API}/latest" if tag == "latest" else f"{LLAMA_API}/tags/{tag}"
        release = json.loads(_read_text(api, token))
        try:
            return pick_llama_asset(release, settings.variant)
        except LauncherError:
            pointer = next(
                (a for a in release.get("assets") or [] if a.get("name") == "nightly-tag.txt"),
                None,
            )
            if pointer is None:
                raise
            nightly = _nightly_tag(_read_text(str(pointer["browser_download_url"])))
            if nightly is None:
                raise
            build = json.loads(_read_text(f"{LLAMA_API}/tags/{nightly}", token))
            return pick_llama_asset(build, settings.variant)
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass
    folder = "latest/download" if tag == "latest" else f"download/{tag}"
    try:
        nightly = _nightly_tag(_read_text(f"{LLAMA_RELEASES}/{folder}/nightly-tag.txt"))
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        nightly = None
    if nightly:
        tag = nightly
    elif tag == "latest":  # an older release that has the programs itself
        with urllib.request.urlopen(_request(f"{LLAMA_RELEASES}/latest"), timeout=30) as r:
            tag = r.geturl().rstrip("/").split("/")[-1]
    name = f"llama-{tag}-{llama_asset_names(settings.variant)[0]}"
    return f"{LLAMA_RELEASES}/download/{tag}/{name}", None


def ensure_llama(settings: Settings, ui: Ui) -> Path:
    target = settings.tools / "llama.cpp" / settings.variant
    binary = target / exe("llama-server")
    if binary.is_file() and not settings.update:
        return binary
    ui.step(f"Downloading the llama.cpp model server ({settings.variant} build)")
    url, sha = resolve_llama(settings)
    ui.say(f"   {url}")
    archive = download(url, settings.tools / url.split("/")[-1], ui, sha256=sha)
    extract_member(archive, exe("llama-server"), target)
    archive.unlink()
    return binary


def ensure_model(settings: Settings, ui: Ui, remote: RemoteFile | None) -> Path:
    dest = settings.model_file
    if dest.is_file() and (remote is None or remote.size in (None, dest.stat().st_size)):
        return dest
    ui.step(f"Downloading the model {dest.name}")
    return download(settings.model_url, dest, ui, sha256=remote.sha256 if remote else None)


def write_launcher_config(settings: Settings, server: Path | None, model: Path | None) -> Path:
    """config/starlight.launcher.yaml: the model server for Starlight. JSON is valid YAML,
    and your own config/starlight.local.yaml still overrides it."""
    if settings.use_model and server is not None and model is not None:
        data: dict[str, Any] = {
            "model": {
                "provider": "llamacpp",
                "options": {
                    "base_url": f"http://127.0.0.1:{settings.port}/v1",
                    "model": model.stem,
                },
                "context_window": settings.context,
                "server": {
                    "binary": str(server),
                    "model_path": str(model),
                    "port": settings.port,
                    "gpu_layers": 999,
                },
            }
        }
    else:
        data = {"model": {"provider": "echo"}}
    path = settings.root / "config" / "starlight.launcher.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Written by Starlight.exe on every start. Put your own changes into\n"
        "# config/starlight.local.yaml, which overrides this file.\n"
        + json.dumps(data, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


# -- running ---------------------------------------------------------------------------


class ProcessGroup:
    """Everything the launcher starts ends when the launcher ends, however it ends.
    On Windows a job object kills the whole process tree when the window is closed."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen[bytes]] = []
        self._logs: list[Any] = []
        self._job: Any = None
        if sys.platform == "win32":
            self._job = _create_kill_on_close_job()

    def start(self, command: list[str], log: Path | None, cwd: Path) -> subprocess.Popen[bytes]:
        output: Any = subprocess.DEVNULL
        if log is not None:
            log.parent.mkdir(parents=True, exist_ok=True)
            output = log.open("ab")
            self._logs.append(output)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=sys.platform != "win32",
        )
        self.processes.append(process)
        return process

    def stop(self) -> None:
        for process in reversed(self.processes):
            if process.poll() is not None:
                continue
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                import signal

                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(10)
        for handle in self._logs:
            handle.close()
        self._logs.clear()


def _create_kill_on_close_job() -> Any:
    """A job object that the launcher itself joins: every process it starts afterwards
    belongs to it, and Windows ends them all when the launcher's last handle closes."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kill_on_close, breakaway_ok = 0x2000, 0x0800
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    # BREAKAWAY_OK: apps Starlight opens for you (Notepad ...) may leave the job and stay.
    limits.BasicLimitInformation.LimitFlags = kill_on_close | breakaway_ok
    extended_limit_information = 9
    ok = kernel32.SetInformationJobObject(
        wintypes.HANDLE(job),
        extended_limit_information,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    )
    if not ok or not kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(job), wintypes.HANDLE(kernel32.GetCurrentProcess())
    ):
        return None  # e.g. already in a job that forbids nesting: fall back to taskkill
    return job


def port_in_use(port: int) -> bool:
    import socket

    with socket.socket() as probe_socket:
        probe_socket.settimeout(0.5)
        return probe_socket.connect_ex(("127.0.0.1", port)) == 0


def wait_for_model(
    port: int, process: subprocess.Popen[bytes] | None, log: Path, ui: Ui, timeout: float = 900
) -> None:
    """llama-server answers /health with 503 while loading and 200 when ready."""
    started = time.monotonic()
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() - started < timeout:
        if process is not None and process.poll() is not None:
            tail = log.read_text(encoding="utf-8", errors="replace")[-2500:] if log.exists() else ""
            raise LauncherError(
                f"the model server stopped (exit code {process.returncode}). Last lines of "
                f"{log}:\n{tail}"
            )
        with (
            contextlib.suppress(urllib.error.URLError, ConnectionError, TimeoutError, OSError),
            urllib.request.urlopen(url, timeout=5) as response,
        ):
            if response.status == 200:
                ui.say(f"\r   model ready after {time.monotonic() - started:.0f} s".ljust(40))
                return
        print(f"\r   loading the model ... {time.monotonic() - started:4.0f} s", end="", flush=True)
        time.sleep(1)
    raise LauncherError(f"the model server did not become ready within {timeout:.0f} s")


def starlight(uv: Path, *args: str) -> list[str]:
    return [str(uv), "run", "--no-sync", "starlight", *args]


def setup(settings: Settings, ui: Ui) -> tuple[Path, Path | None, Path | None]:
    """Install whatever is missing. Returns (uv, llama-server, model)."""
    first_run = not (settings.tools / "uv" / exe("uv")).is_file()
    remote = None
    if settings.use_model and not settings.model_file.is_file():
        remote = probe(settings.model_url)
    if first_run or (remote is not None):
        model_size = (remote.size or 0) if remote else 0
        need = (1.6 * GB if first_run else 0) + (0.2 * GB if settings.use_model else 0) + model_size
        free = shutil.disk_usage(settings.root).free
        ui.say("Starlight will download and set up, inside " + str(settings.root) + ":")
        if first_run:
            ui.say("   - uv, Python 3.12 and Starlight's packages      ~1.3 GB")
            ui.say("   - the Chromium browser for web pages and logins ~0.3 GB")
        if settings.use_model:
            ui.say(f"   - the llama.cpp model server ({settings.variant})         ~0.2 GB")
        if remote is not None:
            ui.say(f"   - the model {settings.model_file.name:<34} {model_size / GB:.1f} GB")
        ui.say(f"   Needed: about {need / GB:.1f} GB. Free on this drive: {free / GB:.1f} GB.")
        if free < need + GB:
            raise LauncherError("not enough free disk space for the download")
        if not ui.ask("Continue?", default=True):
            raise LauncherError("cancelled; nothing was changed")

    uv = ensure_uv(settings, ui)
    ui.step("Setting up Python and Starlight's packages")
    run_checked([str(uv), "sync", "--extra", "all", "--locked", "--python", "3.12"], settings.root)
    marker = settings.tools / ".chromium-installed"
    if not marker.exists() or settings.update:
        ui.step("Installing the Chromium browser")
        run_checked(
            [str(uv), "run", "--no-sync", "playwright", "install", "chromium"], settings.root
        )
        marker.write_text("ok\n", encoding="utf-8")
    server = model = None
    if settings.use_model:
        server = ensure_llama(settings, ui)
        model = ensure_model(settings, ui, remote)
    maybe_tesseract(settings, ui)
    write_launcher_config(settings, server, model)
    return uv, server, model


def run_checked(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode != 0:
        raise LauncherError(f"command failed ({result.returncode}): {' '.join(command)}")


def maybe_tesseract(settings: Settings, ui: Ui) -> None:
    """Screen reading (OCR) is optional and needs an installer with administrator rights,
    so it is only offered, never installed silently."""
    if (
        os.name != "nt"
        or shutil.which("tesseract")
        or Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists()
    ):
        return
    wanted = settings.tesseract
    if wanted is None:
        if settings.assume_yes:
            return
        wanted = ui.ask(
            "Install Tesseract OCR for reading the screen? Windows will ask for "
            "administrator approval.",
            default=False,
        )
    if not wanted:
        return
    if shutil.which("winget") is None:
        ui.say(
            "   winget is not available; install Tesseract from "
            "https://github.com/UB-Mannheim/tesseract/wiki"
        )
        return
    subprocess.run(
        [
            "winget",
            "install",
            "-e",
            "--id",
            "UB-Mannheim.TesseractOCR",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ],
        check=False,
    )


def start(settings: Settings, ui: Ui, uv: Path, ask: str | None) -> int:
    group = ProcessGroup()
    logs = settings.root / "logs"
    try:
        if settings.use_model:
            ui.step("Starting the model server on the GPU")
            log = logs / "llama-server.log"
            if port_in_use(settings.port):
                ui.say(f"   something already listens on port {settings.port}; using it")
                server_process = None
            else:
                server_process = group.start(starlight(uv, "model", "serve"), log, settings.root)
            wait_for_model(settings.port, server_process, log, ui)
        if settings.viewer and ask is None:
            viewer_port = 8765
            if not port_in_use(viewer_port):
                group.start(starlight(uv, "viewer"), logs / "viewer.log", settings.root)
            ui.say(f"\nStatistics viewer: http://127.0.0.1:{viewer_port}")
        if ask is not None:
            result = subprocess.run(starlight(uv, "ask", "-y", ask), cwd=settings.root, check=False)
            return result.returncode
        if settings.web:
            url = f"http://127.0.0.1:{WEB_PORT}"
            ui.say(f"\nOpening the chat in your browser: {url}")
            ui.say("Close this window (or press Ctrl+C) to stop everything.\n")
            threading.Timer(2.5, _open_browser, args=(url,)).start()
            return subprocess.run(starlight(uv, "web"), cwd=settings.root, check=False).returncode
        ui.say("Kill switch: Ctrl+Alt+J, a screen corner, or /kill. Leave with /quit.\n")
        return subprocess.run(starlight(uv, "run"), cwd=settings.root, check=False).returncode
    finally:
        group.stop()


def _open_browser(url: str) -> None:
    with contextlib.suppress(Exception):
        import webbrowser

        webbrowser.open(url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Starlight", description="Set up and start Starlight, the local AI assistant."
    )
    parser.add_argument("--version", action="version", version=f"Starlight launcher {VERSION}")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask; use defaults")
    parser.add_argument("--setup-only", action="store_true", help="install, then exit")
    parser.add_argument("--ask", metavar="TEXT", help="one request, print the answer, exit")
    parser.add_argument("--update", action="store_true", help="download uv and llama.cpp again")
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="use Starlight's built-in test model; download no model",
    )
    parser.add_argument("--no-viewer", action="store_true", help="do not start the viewer")
    parser.add_argument(
        "--terminal", action="store_true", help="chat in this window instead of the browser"
    )
    parser.add_argument("--tesseract", action="store_true", help="install Tesseract OCR")
    parser.add_argument("--model-url", help="GGUF model to use (a direct download link)")
    parser.add_argument("--context", type=int, help="context window in tokens")
    parser.add_argument("--variant", choices=["vulkan", "cpu"], help="llama.cpp build")
    parser.add_argument("--llama-release", help='llama.cpp release tag (default "latest")')
    parser.add_argument("--port", type=int, help="model server port (default 8080)")
    parser.add_argument("--root", help="the Starlight folder (default: where Starlight.exe is)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui = Ui(args.yes)
    code = 1
    try:
        settings = load_settings(args)
        ui.say(f"Starlight launcher {VERSION} - {settings.root}")
        uv, _, _ = setup(settings, ui)
        if args.setup_only:
            ui.say("\nSetup complete. Start Starlight.exe again to begin.")
            code = 0
        else:
            code = start(settings, ui, uv, args.ask)
        return code
    except LauncherError as exc:
        ui.say(f"\nStarlight could not start: {exc}")
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        ui.say(f"\nStarlight could not start: network problem ({exc}). Check the internet")
        ui.say("connection and start again; finished downloads are kept.")
        return 1
    except KeyboardInterrupt:
        ui.say("\nStopped.")
        return 130
    finally:
        # Double-clicked Starlight.exe: keep the window open on errors so they can be read.
        if getattr(sys, "frozen", False) and code != 0 and not args.yes and sys.stdin.isatty():
            with contextlib.suppress(EOFError):
                input("\nPress Enter to close this window.")


if __name__ == "__main__":
    raise SystemExit(main())
