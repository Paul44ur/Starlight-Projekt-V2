"""Decide whether a shell command only reads. Read-only commands run without asking;
everything else needs your confirmation."""

from __future__ import annotations

import re
import shlex

# Characters that chain, redirect or substitute commands.
UNSAFE = re.compile(r"[;&|<>`$\n\\]|\(|\)")

READ_ONLY = {
    "cat",
    "df",
    "du",
    "free",
    "head",
    "id",
    "iostat",
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
    "git": {"status", "log", "diff", "show", "branch"},  # branch: see _ONLY_THESE
    "apt": {"list"},
    "zpool": {"status", "list"},
}
FORBIDDEN_ARGS = {"-f", "--follow"}  # would never return

# Programs that read with some arguments and change things with others ("date -s",
# "ip link set", "journalctl --vacuum-time"): read-only only with no arguments or with
# these.
_ONLY_THESE: dict[str, re.Pattern[str]] = {
    "env": re.compile(r"-0|--null"),  # "env reboot" would run reboot
    "date": re.compile(r"\+.*|-u|--utc|-R|--rfc-email|-I\w*|--iso-8601(=\w+)?"),
    "dmesg": re.compile(
        r"-[HTxkuLrdet]+|--(human|ctime|decode|kernel|userspace|color\S*|level=\S+)"
    ),
    "hostname": re.compile(
        r"-[AIdfis]|--(all-fqdns|all-ip-addresses|domain|fqdn|short|ip-address)"
    ),
    "hostnamectl": re.compile(r"status|--static|--transient|--pretty"),
    "sensors": re.compile(r"-[Aujf]+|--no-adapter|--fahrenheit"),
    "git branch": re.compile(
        r"-[arv]+|-vv|--(list|all|remotes|verbose|show-current|contains|merged|no-merged)"
    ),
}
# Read with any arguments except these words, which change something.
_NOT_THESE: dict[str, re.Pattern[str]] = {
    "journalctl": re.compile(
        r"--(vacuum\S*|rotate|flush|sync|relinquish-var|smart-relinquish-var|setup-keys|"
        r"update-catalog)(=.*)?"
    ),
}


# `ip`: options, an object (addr, link, route ... or any abbreviation), then at most a
# reading verb. iproute2 takes abbreviations ("ip a a ..." is "ip addr add"), so the words
# that change things can't be listed - only the ones that read.
_IP_OPTIONS = re.compile(
    r"-(4|6|s|stats?|br|brief|c|colou?r|j|json|d|details|o|oneline|p|pretty|h|human)"
)
_IP_READ = {"show", "sh", "list", "ls", "lst", "get"}


def _ip_reads(args: list[str]) -> bool:
    rest = list(args)
    while rest and rest[0].startswith("-"):
        if not _IP_OPTIONS.fullmatch(rest.pop(0)):
            return False
    if not rest:
        return True  # "ip" alone prints its help
    obj = rest.pop(0)
    if len(obj) >= 2 and "monitor".startswith(obj):  # would never return
        return False
    return not rest or rest[0] in _IP_READ


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
        if not (len(words) > 1 and words[1] in SUBCOMMANDS[program]):
            return False
        key, rest = f"{program} {words[1]}", words[2:]
        return key not in _ONLY_THESE or all(_ONLY_THESE[key].fullmatch(w) for w in rest)
    if program == "ip":
        return _ip_reads(words[1:])
    if program in _ONLY_THESE:
        return all(_ONLY_THESE[program].fullmatch(w) for w in words[1:])
    if program in _NOT_THESE:
        return not any(_NOT_THESE[program].fullmatch(w) for w in words[1:])
    return program in READ_ONLY
