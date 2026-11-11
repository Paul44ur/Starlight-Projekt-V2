"""Choosing the model on the Settings page.

The launcher (Starlight.exe) starts the model server and writes the model's part of the
configuration on every start, from ``config/launcher.json``. So the page changes that file,
never the configuration: the choice applies after the next start, and the page can ask the
launcher window for a restart through a file. The launcher has its own copy of the few lines
about the two files (it runs without Starlight's libraries, and this module must not import
it): ``data/launcher.alive.json`` (the launcher writes it while it runs) and
``data/launcher.restart.json`` (this side writes it to ask for a restart).

Only models that are already there are offered: the ``.gguf`` files in the models folder
(llama.cpp), or the names the model server reports (LM Studio, Ollama, another PC). New
models are downloaded in the launcher. There is no switch while Starlight runs: the
kernel plans its prompts for one model and context size from its start.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from starlight.core.fsutil import write_atomic
from starlight.core.paths import Paths
from starlight.models.lmstudio import parse_v0, parse_v1

LAUNCHER_FILE = "launcher.json"
ALIVE_FILE = "launcher.alive.json"
RESTART_FILE = "launcher.restart.json"
ALIVE_SECONDS = 15.0  # a heartbeat older than this: no launcher is running
KEEP_COPIES = 10  # earlier copies of launcher.json that are kept
LOOK_TIMEOUT = 3.0  # seconds a model server gets to list its models
# The launcher's own lists (launcher/starlight_launcher.py): context sizes, and the files of a
# model in several parts.
CONTEXT_STEPS = (8192, 16384, 32768, 49152, 65536, 98304, 131072)
DEFAULT_CONTEXT = 32768
SPLIT_PART = re.compile(r"-\d{5}-of-\d{5}\.gguf$")
SERVERS = ("llamacpp", "lmstudio", "ollama", "remote")
LMSTUDIO_URL = "http://127.0.0.1:1234"
OLLAMA_URL = "http://127.0.0.1:11434"
NAME_KEYS = {"lmstudio": "lmstudio_model", "ollama": "ollama_model", "remote": "remote_model"}
PROVIDER_SERVERS = {
    "llamacpp": "llamacpp",
    "lmstudio": "lmstudio",
    "ollama": "ollama",
    "openai_compat": "remote",
}


class ModelChoiceError(Exception):
    """Something the person can fix; the message is shown on the page."""


# -- the file the launcher keeps -----------------------------------------------------------


def launcher_file(paths: Paths) -> Path:
    return paths.config / LAUNCHER_FILE


def read_launcher_json(paths: Paths) -> dict[str, Any]:
    """The launcher's settings ({} if there are none). A file with a mistake is an error: it
    is never overwritten."""
    path = launcher_file(paths)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # (Notepad may add a BOM)
    except (OSError, ValueError) as exc:
        raise ModelChoiceError("config/launcher.json has a mistake: fix it first.") from exc
    if not isinstance(data, dict):
        raise ModelChoiceError("config/launcher.json has a mistake: fix it first.")
    return data


def _keep_copy(paths: Paths) -> None:
    """Keep the file as it is before it changes (config/history/launcher.json.<time>)."""
    source = launcher_file(paths)
    if not source.is_file():
        return
    folder = paths.config / "history"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = folder / f"{LAUNCHER_FILE}.{stamp}"
    number = 1
    while target.exists():
        number += 1
        target = folder / f"{LAUNCHER_FILE}.{stamp}-{number}"
    target.write_bytes(source.read_bytes())
    copies = sorted(folder.glob(f"{LAUNCHER_FILE}.*"))
    for old in copies[:-KEEP_COPIES]:
        old.unlink(missing_ok=True)


# -- what is there to choose ---------------------------------------------------------------


def models_folder(paths: Paths, saved: dict[str, Any]) -> Path:
    folder = saved.get("models_dir")
    return (
        Path(folder).expanduser() if isinstance(folder, str) and folder else paths.root / "models"
    )


def gguf_models(folder: Path) -> list[dict[str, Any]]:
    """The model files in ``folder``: not the vision add-ons (mmproj) and not a model in
    several files (the launcher's own rules)."""
    found: list[dict[str, Any]] = []
    if not folder.is_dir():
        return found
    for file in sorted(folder.glob("*.gguf"), key=lambda f: f.name.lower()):
        name = file.name.lower()
        if "mmproj" in name or SPLIT_PART.search(name) or not file.is_file():
            continue
        found.append({"id": file.name, "label": file.stem, "size": file.stat().st_size})
    return found


def _get(url: str, transport: httpx.BaseTransport | None) -> Any:
    with httpx.Client(timeout=LOOK_TIMEOUT, transport=transport) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def _listed(names: list[str]) -> list[dict[str, Any]]:
    return [{"id": n, "label": n, "size": 0} for n in dict.fromkeys(n for n in names if n)]


def server_models(
    kind: str, saved: dict[str, Any], transport: httpx.BaseTransport | None = None
) -> list[dict[str, Any]]:
    """The names a model server reports. Raises ModelChoiceError (with why) if it does not."""
    try:
        if kind == "lmstudio":
            root = str(saved.get("lmstudio_url") or LMSTUDIO_URL).rstrip("/").removesuffix("/v1")
            found = None
            for path, parse in (("/api/v1/models", parse_v1), ("/api/v0/models", parse_v0)):
                try:
                    found = parse(_get(root + path, transport))
                except httpx.HTTPStatusError:
                    continue  # (an older LM Studio has only the other one)
                if found is not None:
                    break
            if found is None:
                raise ModelChoiceError(NO_ANSWER)
            return _listed(list(found.available))
        if kind == "ollama":
            root = str(saved.get("ollama_url") or OLLAMA_URL).rstrip("/")
            data = _get(root + "/api/tags", transport)
            models = data.get("models") if isinstance(data, dict) else None
            return _listed([str(m.get("name", "")) for m in models or [] if isinstance(m, dict)])
        root = str(saved.get("remote_url") or "").rstrip("/")
        if not root.startswith(("http://", "https://")):
            raise ModelChoiceError("The address of the model server is not set.")
        data = _get(root + "/models", transport)
        models = data.get("data") if isinstance(data, dict) else None
        return _listed([str(m.get("id", "")) for m in models or [] if isinstance(m, dict)])
    except (httpx.HTTPError, ValueError) as exc:
        raise ModelChoiceError(NO_ANSWER) from exc


NO_ANSWER = "no answer"  # (options() says it with the server's name)
SERVER_NAMES = {"lmstudio": "LM Studio", "ollama": "Ollama", "remote": "The model server"}


def server_kind(saved: dict[str, Any], provider: str) -> str:
    """ "llamacpp", "lmstudio", "ollama", "remote", or "none" (Starlight's built-in test
    model). The launcher's file says it; without it, what runs does."""
    named = saved.get("model_server")
    if isinstance(named, str) and named in SERVERS:
        return named
    if provider in PROVIDER_SERVERS:
        return PROVIDER_SERVERS[provider]
    return "none"


def options(
    paths: Paths, saved: dict[str, Any], kind: str, transport: httpx.BaseTransport | None = None
) -> tuple[list[dict[str, Any]], str]:
    """(the models to choose from, why there are none or "")."""
    if kind == "none":
        return (
            [],
            "Starlight runs with its built-in test model: models are set up in the Starlight "
            "window.",
        )
    if kind == "llamacpp":
        found = gguf_models(models_folder(paths, saved))
        return found, "" if found else "There is no model file in the models folder yet."
    try:
        found = server_models(kind, saved, transport)
    except ModelChoiceError as exc:
        if str(exc) == NO_ANSWER:
            return [], f"{SERVER_NAMES[kind]} does not answer, so there is no list of models."
        return [], str(exc)
    return found, "" if found else f"{SERVER_NAMES[kind]} lists no models."


# -- the view the page shows ---------------------------------------------------------------


def _plain(name: object) -> str:
    text = str(name or "")
    return "" if text in ("auto", "local") else text


def running_model(model: Any) -> tuple[str, int]:
    """(the model's name, its context window) as Starlight runs now, from ``config.model``."""
    if getattr(model, "provider", "") == "llamacpp" and model.server.model_path:
        name = Path(model.server.model_path).name
    else:
        name = _plain(model.options.get("model"))
    return name, int(model.context_window)


def saved_choice(
    paths: Paths, running: tuple[str, int] = ("", 0), provider: str = ""
) -> dict[str, Any]:
    """What launcher.json says now: {"server", "model", "context"}. A model it does not name
    is the running one (the launcher then uses a preset, which is what runs)."""
    saved = read_launcher_json(paths)
    kind = server_kind(saved, provider)
    model = ""
    if kind == "llamacpp":
        file = saved.get("model_file")
        url = saved.get("model_url")
        if isinstance(file, str) and file:
            model = file
        elif isinstance(url, str) and url:
            model = url.rstrip("/").split("/")[-1].split("?")[0]
    elif kind in NAME_KEYS:
        model = _plain(saved.get(NAME_KEYS[kind]))
    context = saved.get("context", DEFAULT_CONTEXT)
    if not isinstance(context, int) or isinstance(context, bool):
        context = DEFAULT_CONTEXT
    return {"server": kind, "model": model or running[0], "context": context}


def view(
    paths: Paths,
    model_config: Any,
    start: dict[str, Any] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Everything the card shows. ``start``: the choice as it was when Starlight started
    (a saved choice that differs from it, or from what runs, is waiting for a restart)."""
    running = running_model(model_config)
    provider = str(model_config.provider)
    problem = ""
    try:
        saved = read_launcher_json(paths)
        choice = saved_choice(paths, running, provider)
    except ModelChoiceError as exc:
        saved, choice, problem = {}, {"server": "none", "model": running[0], "context": 0}, str(exc)
    kind = str(choice["server"])
    listed: list[dict[str, Any]] = []
    if not problem:
        listed, problem = options(paths, saved, kind, transport)
    pending = bool(
        (choice["model"] != running[0] and running[0])
        or (
            start is not None
            and (start["model"], start["context"]) != (choice["model"], choice["context"])
        )
    )
    local = paths.config / "starlight.local.yaml"
    overridden = False
    with contextlib.suppress(OSError):
        overridden = local.is_file() and bool(
            re.search(r"^model\s*:", local.read_text("utf-8"), re.M)
        )
    return {
        "server": kind,
        "current": {"model": choice["model"], "context": choice["context"]},
        "running": {"model": running[0], "context": running[1]},
        "options": listed,
        "contexts": [0, *CONTEXT_STEPS],
        "problem": problem,
        "pending": pending,
        "overridden": overridden,
        "restart": {"possible": launcher_alive(paths)},
    }


# -- saving the choice ---------------------------------------------------------------------


def save_choice(
    paths: Paths,
    model_config: Any,
    model: str,
    context: int | None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Write the chosen model (and context size) to launcher.json, keeping a copy of the file
    first. Only a model from the list the page offers is accepted. Returns what was saved."""
    saved = read_launcher_json(paths)  # (a file with a mistake stops here, untouched)
    kind = server_kind(saved, str(model_config.provider))
    listed, problem = options(paths, saved, kind, transport)
    if model not in {o["id"] for o in listed}:
        raise ModelChoiceError(problem or "That model is not in the list.")
    if context is not None and context not in (0, *CONTEXT_STEPS):
        raise ModelChoiceError("That context size is not one of the offered sizes.")
    changes: dict[str, Any] = {}
    if kind == "llamacpp":
        if not (models_folder(paths, saved) / model).is_file():
            raise ModelChoiceError("That model file is gone.")
        changes["model_file"] = model
    else:
        changes[NAME_KEYS[kind]] = model
    if context is not None:
        changes["context"] = context
    _keep_copy(paths)
    write_atomic(launcher_file(paths), json.dumps({**saved, **changes}, indent=2) + "\n")
    return changes


# -- asking the launcher to restart --------------------------------------------------------


def launcher_alive(paths: Paths, now: float | None = None) -> bool:
    """A launcher window runs: its heartbeat is younger than ALIVE_SECONDS."""
    try:
        data = json.loads((paths.data / ALIVE_FILE).read_text(encoding="utf-8"))
        beat = float(data["time"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    age = (time.time() if now is None else now) - beat
    return -ALIVE_SECONDS <= age < ALIVE_SECONDS


def request_restart(paths: Paths, by: str) -> None:
    """Ask the launcher window to restart Starlight (it deletes the file and does)."""
    path = paths.data / RESTART_FILE
    write_atomic(path, json.dumps({"by": by, "since": time.time()}))
