"""A support bundle: one zip with what someone helping you needs to see what went wrong —
the logs, the settings, `starlight doctor`'s checks and the versions — and nothing
private beyond that.

Left out: your memory, conversations, notes, the vault and everything else in data/.
Hidden in what goes in: the vault's secret values, anything that looks like a password,
token or key, and your user folder's path (shown as ~). The audit log (what Starlight
did, with the arguments of each action) goes in unless you leave it out.
"""

from __future__ import annotations

import json
import platform
import sys
import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from starlight import __version__
from starlight.core.logging import redact
from starlight.core.paths import Paths
from starlight.core.secretscan import PATTERNS

TAIL_BYTES = 3_000_000  # of each log: its most recent part
LOG_SUFFIXES = (".log", ".log.1")


def scrub(text: str, secrets: Iterable[str], home: str = "") -> str:
    """The text with secrets, credential-like strings and your home folder hidden."""
    for secret in sorted((s for s in secrets if s and len(s) >= 6), key=len, reverse=True):
        text = text.replace(secret, "<secret hidden>")
    for kind, pattern in PATTERNS.items():
        text = pattern.sub(f"<{kind} hidden>", text)
    if home and len(home) > 3:
        for form in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
            text = text.replace(form, "~")
    return text


def _tail(path: Path) -> str:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - TAIL_BYTES))
        data = handle.read().decode("utf-8", errors="replace")
    if size > TAIL_BYTES:
        data = "[... older lines left out ...]\n" + data.split("\n", 1)[-1]
    return data


def about(paths: Paths, checks: str) -> str:
    """The versions, the model settings and the doctor's checks, as text."""
    lines = [
        f"Starlight {__version__}",
        f"Python {sys.version.split()[0]} on {platform.platform()}",
        f"Made {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    try:
        from starlight.core.config import load_config

        config = load_config(paths)
        model = redact(config.model.model_dump())  # an api_key option shows as ***
        lines += ["Model:", json.dumps(model, indent=2, default=str), ""]
    except Exception as exc:  # a broken config is worth knowing about too
        lines += [f"Config could not be read: {type(exc).__name__}: {exc}", ""]
    lines += ["starlight doctor:", checks]
    return "\n".join(lines) + "\n"


def make_bundle(
    paths: Paths,
    out_dir: Path,
    secrets: Iterable[str] = (),
    checks: str = "",
    activity: bool = True,
) -> Path:
    """Write the zip into ``out_dir`` and return its path."""
    secrets = list(secrets)
    home = str(Path.home())
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"starlight-support-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("about.txt", scrub(about(paths, checks), secrets, home))
        for path in sorted(paths.config.glob("*.yaml")) + sorted(
            (paths.config / "modules").glob("*.yaml")
        ):
            if path.name.startswith("vault"):
                continue  # only the names of secrets, but they are nobody else's business
            text = path.read_text(encoding="utf-8", errors="replace")
            bundle.writestr(
                f"config/{path.relative_to(paths.config).as_posix()}", scrub(text, secrets, home)
            )
        for folder in (paths.logs, paths.logs / "modules"):
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if not path.is_file() or not path.name.endswith(LOG_SUFFIXES):
                    continue
                if not activity and path.name.startswith("audit."):
                    continue
                name = f"logs/{path.relative_to(paths.logs).as_posix()}"
                bundle.writestr(name, scrub(_tail(path), secrets, home))
        bundle.writestr(
            "README.txt",
            "A Starlight support bundle: logs, settings and checks. Your memory, "
            "conversations and secrets are not in it; secret values, things that look like "
            "passwords or keys, and your user folder's path are hidden. "
            + (
                "logs/audit.log lists what Starlight did, with each action's arguments."
                if activity
                else "The activity log was left out."
            )
            + "\n",
        )
    return target
