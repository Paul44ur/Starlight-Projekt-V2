"""Review logs for leaked secrets.

Finds the vault's actual values and things that look like credentials (private keys,
bearer tokens, API keys, ``password=...``). Reports file, line and kind, never the secret.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "Meta/WhatsApp token": re.compile(r"\bEAA[A-Za-z0-9]{40,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "API key (sk-)": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "password in text": re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\b\"?\s*[:=]\s*\"?(?!\*\*\*)[^\s\",}]{6,}"
    ),
}


@dataclass(frozen=True)
class Finding:
    file: Path
    line: int
    kind: str


def scan_text(text: str, secrets: Iterable[str] = ()) -> list[tuple[int, str]]:
    known = [s for s in secrets if s and len(s) >= 6]
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if any(secret in line for secret in known):
            found.append((number, "vault secret"))
            continue
        for kind, pattern in PATTERNS.items():
            if pattern.search(line):
                found.append((number, kind))
                break
    return found


def scan_logs(logs_dir: Path, secrets: Iterable[str] = ()) -> list[Finding]:
    """Every log file under ``logs_dir``, including rotated ones (``*.log.1`` ...)."""
    secrets = list(secrets)
    findings: list[Finding] = []
    for path in sorted(logs_dir.rglob("*.log*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        findings += [Finding(path, line, kind) for line, kind in scan_text(text, secrets)]
    return findings
