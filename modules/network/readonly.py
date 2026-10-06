"""Decide whether a shell command only reads. Read-only commands run without asking;
everything else needs your confirmation."""

from __future__ import annotations

import re
import shlex

# Characters that chain, redirect or substitute commands.
UNSAFE = re.compile(r"[;&|<>`$\n\\]|\(|\)")

READ_ONLY = {
    "cat",
    "date",
    "df",
    "dmesg",
    "du",
    "env",
    "free",
    "head",
    "hostname",
    "hostnamectl",
    "id",
    "iostat",
    "ip",
    "journalctl",
    "last",
    "ls",
    "lsblk",
    "lscpu",
    "lsof",
    "lsusb",
    "netstat",
    "nproc",
    "ping",
    "ps",
    "pwd",
    "sensors",
    "ss",
    "stat",
    "tail",
    "top",
    "uname",
    "uptime",
    "vmstat",
    "w",
    "wc",
    "who",
    "whoami",
    "findmnt",
    "grep",
}
# Commands that are read-only only with certain subcommands.
SUBCOMMANDS = {
    "systemctl": {
        "status",
        "is-active",
        "is-enabled",
        "is-failed",
        "list-units",
        "list-timers",
        "show",
    },
    "docker": {"ps", "images", "stats", "logs", "inspect", "version", "info"},
    "git": {"status", "log", "diff", "show", "branch"},
    "apt": {"list"},
    "zpool": {"status", "list"},
}
FORBIDDEN_ARGS = {"-f", "--follow"}  # would never return


def is_read_only(command: str) -> bool:
    if not command.strip() or UNSAFE.search(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words or words[0] == "sudo":
        return False
    program = words[0].rsplit("/", 1)[-1]
    if any(w in FORBIDDEN_ARGS for w in words[1:]):
        return False
    if program == "top":
        return "-b" in words or "-bn1" in words
    if program == "docker" and len(words) > 1 and words[1] == "stats":
        return "--no-stream" in words
    if program in SUBCOMMANDS:
        return len(words) > 1 and words[1] in SUBCOMMANDS[program]
    return program in READ_ONLY
