"""Who may use the web chat from another device (your phone over Tailscale).

On this PC (a loopback Host) nothing changes: the chat works as before. Any other Host
needs a paired device:

- On the PC you ask for a pairing code (`starlight remote pair`, or the button on the local
  settings page). It is 6 digits, works once, and for 5 minutes.
- The phone opens the chat, enters the code and gets a random 256-bit device token as an
  HttpOnly, Secure, SameSite=Strict cookie. Only the token's SHA-256 is stored
  (data/devices.json), so the file is useless to someone who copies it.
- Wrong codes or tokens are counted; after 5 failures from one address, that address waits
  15 minutes (and every failure is in the audit log).
- Devices are listed and revoked on the settings page or with `starlight remote devices`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlight.core.fsutil import write_atomic

COOKIE = "starlight_device"
CODE_SECONDS = 300
MAX_FAILURES = 5
LOCKOUT_SECONDS = 900
CODE_FILE = "pairing.json"
DEVICES_FILE = "devices.json"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Device:
    id: str
    name: str
    token_hash: str
    paired: float
    last_seen: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "paired": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.paired)),
            "last_seen": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.last_seen))
            if self.last_seen
            else None,
        }


@dataclass
class Throttle:
    """Counts failures per client address and locks it out for a while after too many."""

    failures: dict[str, list[float]] = field(default_factory=dict)

    def locked(self, client: str, now: float | None = None) -> bool:
        moment = now or time.time()
        recent = [t for t in self.failures.get(client, []) if moment - t < LOCKOUT_SECONDS]
        self.failures[client] = recent
        return len(recent) >= MAX_FAILURES

    def fail(self, client: str, now: float | None = None) -> None:
        self.failures.setdefault(client, []).append(now or time.time())

    def clear(self, client: str) -> None:
        self.failures.pop(client, None)


class Devices:
    """Paired devices and the one-time pairing code, stored under data/."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()
        self.throttle = Throttle()

    # -- storage ---------------------------------------------------------------------------

    def _load(self) -> list[Device]:
        try:
            raw = json.loads((self.folder / DEVICES_FILE).read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [Device(**d) for d in raw if isinstance(d, dict)]

    def _store(self, devices: list[Device]) -> None:
        write_atomic(
            self.folder / DEVICES_FILE, json.dumps([d.__dict__ for d in devices], indent=1)
        )

    # -- pairing ---------------------------------------------------------------------------

    def new_code(self) -> str:
        """A fresh one-time code (replaces any earlier one). Shown on the PC only."""
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"hash": _hash(code), "expires": time.time() + CODE_SECONDS}
        (self.folder / CODE_FILE).write_text(json.dumps(data), encoding="utf-8")
        return code

    def pair(self, code: str, name: str, client: str) -> str | None:
        """Exchange a valid code for a device token (returned once, never stored)."""
        with self._lock:
            if self.throttle.locked(client):
                return None
            path = self.folder / CODE_FILE
            try:
                pending = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                pending = {}
            valid = (
                isinstance(pending, dict)
                and pending.get("expires", 0) > time.time()
                and hmac.compare_digest(str(pending.get("hash", "")), _hash(code.strip()))
            )
            if not valid:
                self.throttle.fail(client)
                return None
            path.unlink(missing_ok=True)  # one use only
            self.throttle.clear(client)
            token = secrets.token_urlsafe(32)
            devices = self._load()
            label = " ".join(name.split())[:60] or "Phone"
            devices.append(Device(uuid.uuid4().hex[:10], label, _hash(token), time.time()))
            self._store(devices)
            return token

    def check(self, token: str | None, client: str) -> Device | None:
        """The paired device a token belongs to, or None. A 256-bit token can't be guessed,
        so a wrong one is not counted: a revoked phone polling with its old cookie must not
        lock itself out of pairing again."""
        if not token or len(token) > 200:
            return None
        with self._lock:
            wanted = _hash(token)
            devices = self._load()
            for device in devices:
                if hmac.compare_digest(device.token_hash, wanted):
                    if time.time() - device.last_seen > 60:  # don't rewrite the file per request
                        device.last_seen = time.time()
                        self._store(devices)
                    return device
            return None

    def list(self) -> list[dict[str, Any]]:
        return [d.public() for d in self._load()]

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            devices = self._load()
            kept = [d for d in devices if d.id != device_id]
            if len(kept) == len(devices):
                return False
            self._store(kept)
            return True
