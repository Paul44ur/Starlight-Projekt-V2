"""Using the web chat from your phone, privately, through Tailscale.

Tailscale puts your phone and this PC in one private network; nothing is opened on your
router and nobody else can reach the chat. `tailscale serve` gives the chat a proper HTTPS
address (https://<pc>.<tailnet>.ts.net) and forwards it to 127.0.0.1, so Starlight itself
keeps listening on this PC only. On top of that, every other device must be paired with a
one-time code before it can do anything (see starlight/web/auth.py).

`starlight remote enable` runs `tailscale serve` and writes the address into
config/starlight.remote.yaml; `starlight remote disable` undoes both.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from starlight.core.config import read_yaml, write_yaml_config
from starlight.core.paths import Paths

REMOTE_FILE = "starlight.remote.yaml"
HEADER = (
    "# Written by `starlight remote enable/disable`: using the web chat from your other\n"
    "# devices through Tailscale. Change it with those commands, not by hand."
)


class RemoteError(Exception):
    pass


def tailscale() -> str:
    found = shutil.which("tailscale")
    if found is None:
        for candidate in (
            Path(r"C:\Program Files\Tailscale\tailscale.exe"),
            Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
        ):
            if candidate.is_file():
                return str(candidate)
        raise RemoteError(
            "Tailscale is not installed. Install it from https://tailscale.com/download on "
            "this PC and on your phone, sign in on both with the same account, then try again."
        )
    return found


def _run(*args: str, timeout: float = 30) -> str:
    done = subprocess.run(
        [tailscale(), *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip()
        raise RemoteError(f"tailscale {' '.join(args)} failed: {message[:500]}")
    return done.stdout


def address() -> str:
    """This PC's name in your tailnet, e.g. my-pc.tail1234.ts.net."""
    status: dict[str, Any] = json.loads(_run("status", "--json"))
    if status.get("BackendState") != "Running":
        raise RemoteError("Tailscale is not connected; open Tailscale and sign in first")
    name = str((status.get("Self") or {}).get("DNSName", "")).rstrip(".")
    if not name:
        raise RemoteError(
            "this PC has no Tailscale name; turn on MagicDNS and HTTPS certificates in the "
            "Tailscale admin console (DNS page)"
        )
    return name


def enable(paths: Paths, port: int) -> str:
    """Serve the chat over Tailscale HTTPS and allow that address. Returns the URL."""
    name = address()
    _run("serve", "--bg", "--https=443", f"http://127.0.0.1:{port}", timeout=60)
    write_yaml_config(
        paths.config / REMOTE_FILE, {"web": {"remote": {"enabled": True, "hosts": [name]}}}, HEADER
    )
    return f"https://{name}/"


def disable(paths: Paths) -> None:
    # not serving, or Tailscale gone: the config change below is what matters
    with contextlib.suppress(RemoteError):
        _run("serve", "--https=443", "off", timeout=60)
    write_yaml_config(
        paths.config / REMOTE_FILE, {"web": {"remote": {"enabled": False, "hosts": []}}}, HEADER
    )


def status(paths: Paths) -> dict[str, Any]:
    saved = read_yaml(paths.config / REMOTE_FILE).get("web", {}).get("remote", {})
    hosts = saved.get("hosts") or []
    return {
        "enabled": bool(saved.get("enabled")),
        "url": f"https://{hosts[0]}/" if hosts else None,
    }
