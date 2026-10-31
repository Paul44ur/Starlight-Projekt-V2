"""Safety limits you set yourself: one level, then any single protection on top of it.

Settings -> Safety limits (the web chat, changeable at the PC only) chooses a level -
Strict, Balanced or Relaxed - and may set single protections differently. The choice is
kept in config/starlight.safety.yaml:

    level: strict
    settings:          # the protections set differently from the level
      guard: false
      limits: {mail.send: 50/hour}

Each switch names the real setting it changes, in config/starlight.yaml
("permissions.guard_after_untrusted") or in a module's settings ("desktop: failsafe",
config/modules/desktop.yaml). When Starlight starts, the choice is applied over
config/starlight.yaml and the module files (load_config, load_module_config);
config/starlight.local.yaml still wins, so hand edits there stay yours. A change on the
page also reaches the running Starlight at once, except where a switch says it needs a
restart (``live=False``).

Balanced changes nothing: it is Starlight as configured. Strict and Relaxed set the values
below; a value of None means "as configured" at that level too.

A switch's value is said as the protection: True means the protection is on, also when
the setting itself is the opposite (``invert``: code "allow_network" is the protection
"no internet in the sandbox" switched off).

Not switches, always on: the kill switch (Ctrl+Alt+J, the stop file, the Kill button),
secret masking in the logs, the audit log, and that these settings change only at the PC.
They never stop Starlight from doing anything, they only let you stop it or see what it did.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlight.core.config import (
    LIMIT_UNITS,
    ConfigError,
    RemoteConfig,
    load_config,
    load_module_config,
    parse_limit,
    read_yaml,
    write_yaml_config,
)

SAFETY_FILE = "starlight.safety.yaml"
LEVELS = ("strict", "balanced", "relaxed")
GROUPS = {
    "limits": "Limits and the guard",
    "refusals": "What Starlight refuses or asks about by itself",
    "outsiders": "Other devices and web pages",
}
MODULE = "module:"  # a setting in a module's file: "module:desktop.failsafe"
TOOL_PATTERN = re.compile(r"^[a-z0-9_.*?-]{1,80}$")  # "mail.send", "desktop.*", "*"
ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")  # no path, no trailing "/"


class _AsConfigured:
    """A setting the switch leaves as configured (it writes nothing)."""

    def __repr__(self) -> str:
        return "AS_CONFIGURED"


AS_CONFIGURED: Any = _AsConfigured()


@dataclass(frozen=True)
class Switch:
    key: str
    group: str  # a key of GROUPS
    title: str  # the protection, said as switched on
    help: str
    setting: str  # "permissions.guard_after_untrusted" or "module:desktop.failsafe"
    kind: str = "bool"  # bool, int, float, list, limits
    invert: bool = False  # bool: the setting is the opposite of the protection
    # bool switches whose setting is not a plain True/False: what it is set to while the
    # protection is on / off (AS_CONFIGURED: left as configured).
    on: Any = None
    off: Any = None
    strict: Any = None  # the value at Strict / Relaxed; None: as configured
    relaxed: Any = None
    warning: str = ""  # shown while the protection is off (or, for a list, not empty)
    low: float | None = None  # bounds for numbers
    high: float | None = None
    unit: str = ""
    items: re.Pattern[str] = TOOL_PATTERN  # what each entry of a list must look like
    live: bool = True  # False: takes effect when Starlight starts again

    @property
    def module(self) -> str | None:
        if not self.setting.startswith(MODULE):
            return None
        return self.setting.removeprefix(MODULE).split(".", 1)[0]

    @property
    def path(self) -> tuple[str, ...]:
        """Where the setting is: in the core config, or in the module's own config."""
        name = self.setting.removeprefix(MODULE)
        parts = tuple(name.split("."))
        return parts[1:] if self.module else parts

    def level_value(self, level: str) -> Any:
        return {"strict": self.strict, "relaxed": self.relaxed}.get(level)

    def to_setting(self, value: Any) -> Any:
        """The protection's value -> what the setting is set to."""
        if self.kind != "bool":
            return value
        if self.on is not None or self.off is not None:
            return self.on if value else self.off
        return (not value) if self.invert else bool(value)

    def from_setting(self, setting: Any) -> Any:
        """What a setting says about the protection."""
        if self.kind != "bool":
            return setting
        if self.on is not None or self.off is not None:
            return setting == self.on
        return (not setting) if self.invert else bool(setting)

    def check(self, value: Any) -> Any:
        """A value from the page, checked and tidied; ValueError says what is wrong."""
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError("on or off")
            return value
        if self.kind in ("int", "float"):
            try:
                number: float = int(value) if self.kind == "int" else float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("a number") from exc
            low = self.low if self.low is not None else number
            high = self.high if self.high is not None else number
            if not low <= number <= high:
                raise ValueError(f"between {low:g} and {high:g}")
            return number
        if self.kind == "list":
            entries = _entries(value)
            wrong = [e for e in entries if not self.items.match(e)]
            if wrong:
                raise ValueError(f"not understood: {', '.join(wrong[:3])}")
            return list(dict.fromkeys(entries))  # (each once)
        if self.kind == "limits":
            return _limits(value)
        raise ValueError(f"unknown kind {self.kind}")


def _entries(value: Any) -> list[str]:
    """A list from the page: a list, or text with one entry per line (or commas)."""
    if isinstance(value, str):
        value = re.split(r"[\n,]+", value)
    if not isinstance(value, list):
        raise ValueError("a list")
    return [str(v).strip() for v in value if str(v).strip()]


def _limits(value: Any) -> dict[str, str]:
    """{"mail.send": "20/hour"} from a mapping or from lines like "mail.send: 20/hour"."""
    if isinstance(value, Mapping):
        pairs = [(str(k), str(v)) for k, v in value.items()]
    else:
        pairs = []
        for line in _entries(value):
            pattern, sep, limit = line.rpartition(":")
            if not sep:
                raise ValueError(f"'{line}': write it like mail.send: 20/hour")
            pairs.append((pattern, limit))
    found: dict[str, str] = {}
    units = ", ".join(LIMIT_UNITS)
    for pattern, limit in pairs:
        pattern, limit = pattern.strip(), limit.strip()
        if not TOOL_PATTERN.match(pattern):
            raise ValueError(f"'{pattern}' is not a tool name or pattern")
        if parse_limit(limit) is None:
            raise ValueError(f"'{pattern}: {limit}' should look like 20/hour ({units})")
        found[pattern] = limit
    return found


# fmt: off
SWITCHES: tuple[Switch, ...] = (
    # -- limits and the guard ------------------------------------------------------------
    Switch(
        "guard", "limits", "Ask again after reading web pages or mail",
        "Once a request has read a web page, a mail, a PDF or the screen, the actions that "
        "normally ask ask again for the rest of that request, with a note why.",
        "permissions.guard_after_untrusted", strict=True, relaxed=False,
        warning="Hidden text in a page or a mail could make Starlight send mail or type for "
        "you without a question.",
    ),
    Switch(
        "quiet", "limits", "Ask before the usual actions too",
        "Clicking and typing, running code, closing programs, contacting servers and sending "
        "mail ask first. Off: they run on their own, as listed under permissions: "
        "auto_approve: in config/starlight.yaml. Your choices tool by tool (below) still apply.",
        "permissions.auto_approve", on=[], off=AS_CONFIGURED, strict=True,
        warning="These run without a question; the kill switch and the logs remain.",
    ),
    Switch(
        "limits", "limits", "How often actions may run on their own",
        "Past a limit Starlight asks you first, until there is room again. One per line: a "
        "tool or pattern, then a number per minute, hour or day, e.g. mail.send: 20/hour.",
        "permissions.limits", kind="limits",
        strict={"mail.*": "10/hour", "code.*": "30/hour", "network.*": "200/hour",
                "desktop.*": "300/hour"},
        relaxed={},
    ),
    Switch(
        "steps", "limits", "Steps per request",
        "How many tool steps one request may take; then Starlight stops and says so.",
        "agent.max_steps", kind="int", strict=8, relaxed=30, low=1, high=100,
    ),
    Switch(
        "timeout", "limits", "Time limit for one action",
        "A tool that runs longer is stopped.",
        "tools.timeout", kind="float", relaxed=600, low=5, high=3600,
        unit="seconds", live=False,
    ),
    Switch(
        "web_rate", "limits", "Web requests per second",
        "All sites together, from every module.",
        "http.max_requests_per_second", kind="int", strict=3, relaxed=20, low=1, high=100,
        live=False,
    ),
    Switch(
        "web_pause", "limits", "Pause between requests to the same site",
        "A site's own crawl-delay (robots.txt) still wins.",
        "http.per_domain_interval", kind="float", strict=2, relaxed=0.2, low=0, high=60,
        unit="seconds",
    ),
    Switch(
        "robots", "limits", "Follow robots.txt",
        "Sites can ask robots to stay away from pages. Starlight keeps to it when it reads "
        "several pages, watches a page or researches; a page you ask for by address is read "
        "like a browser would.",
        "http.respect_robots", strict=True, relaxed=False,
        warning="Some sites block visitors that ignore it.",
    ),
    # -- built-in refusals ---------------------------------------------------------------
    Switch(
        "own_folder", "refusals", "Keep Starlight's own folder off-limits",
        "Its settings, these limits, its data and its logs: files, documents and code "
        "Starlight runs can't read or change them.",
        "output.protect_starlight_folder", strict=True,
        warning="Starlight could change its own settings and these limits, for example after "
        "reading a web page that asks it to.",
    ),
    Switch(
        "outside", "refusals", "Ask before reading or changing your files elsewhere",
        "Outside the Starlight folder and your trusted folders: reading a file, replacing "
        "one or adding to it asks first.",
        "output.ask_outside", strict=True, relaxed=False,
        warning="Starlight reads and changes any of your files without a question.",
    ),
    Switch(
        "new_outside", "refusals", "Ask before saving a new file elsewhere",
        "A new file outside the Starlight folder asks first. Off: it is saved without a "
        "question (programs and scripts follow the next switch).",
        "output.write_anywhere", invert=True, strict=True, live=False,
    ),
    Switch(
        "runnable", "refusals", "Handle programs and scripts with care",
        "Saving a .exe, .bat, .ps1 or similar outside the Starlight folder asks first, and "
        "opening one as a file is refused.",
        "output.ask_runnable", strict=True,
        warning="A script saved in the wrong folder (Startup) runs at every login.",
    ),
    Switch(
        "sandbox_net", "refusals", "Code sandbox: no internet",
        "Programs Starlight writes and runs get no network, unless a task asks for it.",
        "module:code.allow_network", invert=True, strict=True, relaxed=False,
    ),
    Switch(
        "sandbox_folders", "refusals", "Code sandbox: your secrets stay out",
        "Code Starlight runs can't read Starlight's folder or ~/.ssh (in the Docker sandbox it "
        "sees no folders of yours at all).",
        "module:code.sandbox_protects_folders", strict=True,
        warning="Code the model writes could read Starlight's settings and your SSH keys.",
    ),
    Switch(
        "sandbox_programs", "refusals", "Code sandbox: no other programs",
        "Code Starlight runs can't start other programs.",
        "module:code.sandbox_blocks_programs", strict=True,
        warning="Code the model writes could start any program on this PC.",
    ),
    Switch(
        "failsafe", "refusals", "A screen corner stops desktop control",
        "Moving the mouse into a corner while Starlight clicks or types stops it, like the "
        "kill switch.",
        "module:desktop.failsafe", strict=True,
    ),
    Switch(
        "banner", "refusals", "Red banner while Starlight controls the PC",
        "A banner at the top of the screen while Starlight clicks or types.",
        "module:desktop.indicator", strict=True, live=False,
    ),
    Switch(
        "access_asks", "refusals", "Changing permissions from the chat asks first",
        "Letting an action run without asking, blocking one or removing a rule from the chat "
        "happens only after you say yes.",
        "module:access.ask_before_changes", strict=True,
        warning="Starlight could give itself permissions without asking you.",
    ),
    Switch(
        "first_open", "refusals", "Ask the first time a program is opened",
        "Each program asks once, the first time Starlight opens it.",
        "module:apps.ask_first_open", strict=True, relaxed=False,
    ),
    Switch(
        "servers", "refusals", "Only the servers you listed",
        "Ping, ports, HTTP, SSH and the other network tools reach only servers in "
        "servers.yaml (network.allow adds one).",
        "module:network.any_host", invert=True, strict=True, relaxed=False,
    ),
    Switch(
        "scan_asks", "refusals", "Ask before every scan",
        "Off: a host you allowed once is scanned again without a question. A public host you "
        "have not allowed always asks.",
        "module:recon.ask_again_for_known", strict=True, relaxed=False,
    ),
    # -- other devices and web pages -----------------------------------------------------
    Switch(
        "phone_asks", "outsiders", "On other devices, these always ask first",
        "Tool names or patterns, one per line (desktop.*, mail.send); * is every tool. They "
        "ask on the phone even when they run on their own at the PC.",
        "web.remote.confirm", kind="list", strict=["*"],
    ),
    Switch(
        "phone_never", "outsiders", "Never from other devices",
        "These never run from a phone or another PC; making rules and automations stays at "
        "this PC.",
        "web.remote.blocked", kind="list",
        strict=[*RemoteConfig().blocked, "commands.*", "code.*", "desktop.*", "system.*"],
    ),
    Switch(
        "pairing", "outsiders", "Devices must be paired",
        "A phone or laptop needs a one-time code from this page before it can use Starlight "
        "over Tailscale.",
        "web.remote.require_pairing", strict=True,
        warning="Every device that reaches this PC's Tailscale address can use Starlight as "
        "you (only these settings stay at the PC).",
    ),
    Switch(
        "code_minutes", "outsiders", "A pairing code works for",
        "After that a new code is needed.",
        "web.remote.pairing_minutes", kind="int", strict=2, relaxed=15, low=1, high=60,
        unit="minutes",
    ),
    Switch(
        "wrong_codes", "outsiders", "Wrong codes before a 15-minute lockout",
        "Counted for each device that tries.",
        "web.remote.max_wrong_codes", kind="int", strict=3, relaxed=10, low=1, high=50,
    ),
    Switch(
        "origins", "outsiders", "Other web pages that may use Starlight",
        "Addresses such as http://homeassistant.local:8123, one per line: pages from there may "
        "show the chat and use it as you, in a browser on this PC. Empty: only Starlight's own "
        "pages.",
        "web.allowed_origins", kind="list", items=ORIGIN,
        warning="A page from these addresses can do anything you can do in the chat.",
    ),
)
# fmt: on
BY_KEY = {s.key: s for s in SWITCHES}

# The same in German (the page shows these when it is in German): title, help, warning.
GERMAN_GROUPS = {
    "limits": "Grenzen und der Wächter",
    "refusals": "Was Starlight von sich aus ablehnt oder erfragt",
    "outsiders": "Andere Geräte und Webseiten",
}
GERMAN: dict[str, tuple[str, str, str]] = {
    "guard": (
        "Nach Webseiten oder Mails erneut fragen",
        "Hat eine Anfrage eine Webseite, eine Mail, ein PDF oder den Bildschirm gelesen, fragen "
        "die Aktionen, die sonst fragen, für den Rest dieser Anfrage wieder, mit einem Hinweis "
        "warum.",
        "Versteckter Text in einer Seite oder Mail könnte Starlight dazu bringen, ohne Nachfrage "
        "Mails zu senden oder für dich zu tippen.",
    ),
    "quiet": (
        "Auch bei den üblichen Aktionen fragen",
        "Klicken und Tippen, Code ausführen, Programme schließen, Server ansprechen und Mails "
        "senden fragen vorher. Aus: Sie laufen von selbst, wie unter permissions: auto_approve: "
        "in config/starlight.yaml eingetragen. Deine Einstellungen Tool für Tool (unten) gelten "
        "weiter.",
        "Diese laufen ohne Nachfrage; Not-Aus und Protokolle bleiben.",
    ),
    "limits": (
        "Wie oft Aktionen von selbst laufen dürfen",
        "Über einer Grenze fragt Starlight erst, bis wieder Platz ist. Eine pro Zeile: ein Tool "
        "oder Muster, dann eine Zahl pro minute, hour oder day, z. B. mail.send: 20/hour.",
        "",
    ),
    "steps": (
        "Schritte pro Anfrage",
        "Wie viele Tool-Schritte eine Anfrage nehmen darf; dann hört Starlight auf und sagt es.",
        "",
    ),
    "timeout": ("Zeitlimit für eine Aktion", "Ein Tool, das länger läuft, wird gestoppt.", ""),
    "web_rate": ("Webanfragen pro Sekunde", "Alle Seiten zusammen, aus allen Modulen.", ""),
    "web_pause": (
        "Pause zwischen Anfragen an dieselbe Seite",
        "Ein eigenes Crawl-Delay der Seite (robots.txt) hat Vorrang.",
        "",
    ),
    "robots": (
        "robots.txt beachten",
        "Seiten können Robotern sagen, welche Seiten tabu sind. Starlight hält sich daran, wenn "
        "es mehrere Seiten liest, eine Seite beobachtet oder recherchiert; eine Seite, die du "
        "per Adresse anforderst, wird gelesen wie im Browser.",
        "Manche Seiten sperren Besucher, die sich nicht daran halten.",
    ),
    "own_folder": (
        "Starlights eigenen Ordner sperren",
        "Seine Einstellungen, diese Grenzen, seine Daten und Protokolle: Dateien, Dokumente und "
        "Code, den Starlight ausführt, können sie weder lesen noch ändern.",
        "Starlight könnte seine eigenen Einstellungen und diese Grenzen ändern, etwa nachdem es "
        "eine Webseite gelesen hat, die das verlangt.",
    ),
    "outside": (
        "Fragen, bevor deine Dateien anderswo gelesen oder geändert werden",
        "Außerhalb des Starlight-Ordners und deiner vertrauten Ordner: eine Datei lesen, "
        "ersetzen oder etwas anhängen fragt vorher.",
        "Starlight liest und ändert jede deiner Dateien ohne Nachfrage.",
    ),
    "new_outside": (
        "Fragen, bevor eine neue Datei anderswo gespeichert wird",
        "Eine neue Datei außerhalb des Starlight-Ordners fragt vorher. Aus: Sie wird ohne "
        "Nachfrage gespeichert (Programme und Skripte folgen dem nächsten Schalter).",
        "",
    ),
    "runnable": (
        "Vorsicht bei Programmen und Skripten",
        "Eine .exe, .bat, .ps1 oder Ähnliches außerhalb des Starlight-Ordners zu speichern "
        "fragt vorher, und eine als Datei zu öffnen wird abgelehnt.",
        "Ein Skript im falschen Ordner (Autostart) läuft bei jeder Anmeldung.",
    ),
    "sandbox_net": (
        "Code-Sandbox: kein Internet",
        "Programme, die Starlight schreibt und ausführt, bekommen kein Netzwerk, außer eine "
        "Aufgabe verlangt es.",
        "",
    ),
    "sandbox_folders": (
        "Code-Sandbox: deine Geheimnisse bleiben draußen",
        "Code, den Starlight ausführt, kann Starlights Ordner und ~/.ssh nicht lesen (in der "
        "Docker-Sandbox sieht er gar keine deiner Ordner).",
        "Code, den das Modell schreibt, könnte Starlights Einstellungen und deine SSH-Schlüssel "
        "lesen.",
    ),
    "sandbox_programs": (
        "Code-Sandbox: keine anderen Programme",
        "Code, den Starlight ausführt, kann keine anderen Programme starten.",
        "Code, den das Modell schreibt, könnte jedes Programm auf diesem PC starten.",
    ),
    "failsafe": (
        "Eine Bildschirmecke stoppt die Desktop-Steuerung",
        "Fährst du mit der Maus in eine Ecke, während Starlight klickt oder tippt, hört es auf, "
        "wie beim Not-Aus.",
        "",
    ),
    "banner": (
        "Rotes Banner, während Starlight den PC steuert",
        "Ein Banner oben am Bildschirm, während Starlight klickt oder tippt.",
        "",
    ),
    "access_asks": (
        "Rechte im Chat ändern fragt vorher",
        "Eine Aktion ohne Nachfrage erlauben, eine sperren oder eine Regel entfernen geschieht "
        "im Chat erst, wenn du ja sagst.",
        "Starlight könnte sich selbst Rechte geben, ohne dich zu fragen.",
    ),
    "first_open": (
        "Beim ersten Öffnen eines Programms fragen",
        "Jedes Programm fragt einmal, wenn Starlight es zum ersten Mal öffnet.",
        "",
    ),
    "servers": (
        "Nur die Server, die du eingetragen hast",
        "Ping, Ports, HTTP, SSH und die anderen Netzwerk-Tools erreichen nur Server aus "
        "servers.yaml (network.allow fügt einen hinzu).",
        "",
    ),
    "scan_asks": (
        "Vor jedem Scan fragen",
        "Aus: Ein Host, den du einmal erlaubt hast, wird ohne erneute Nachfrage gescannt. Ein "
        "öffentlicher Host, den du nicht erlaubt hast, fragt immer.",
        "",
    ),
    "phone_asks": (
        "Auf anderen Geräten immer vorher fragen",
        "Tool-Namen oder Muster, eins pro Zeile (desktop.*, mail.send); * heißt alle. Sie fragen "
        "auf dem Handy, auch wenn sie am PC von selbst laufen.",
        "",
    ),
    "phone_never": (
        "Nie von anderen Geräten",
        "Diese laufen nie vom Handy oder einem anderen PC aus; Regeln und Automationen anlegen "
        "bleibt an diesem PC.",
        "",
    ),
    "pairing": (
        "Geräte müssen gekoppelt sein",
        "Ein Handy oder Laptop braucht einen Einmal-Code von dieser Seite, bevor es Starlight "
        "über Tailscale nutzen kann.",
        "Jedes Gerät, das die Tailscale-Adresse dieses PCs erreicht, kann Starlight als du "
        "nutzen (nur diese Einstellungen bleiben am PC).",
    ),
    "code_minutes": ("Ein Kopplungscode gilt", "Danach braucht es einen neuen Code.", ""),
    "wrong_codes": (
        "Falsche Codes bis zu 15 Minuten Sperre",
        "Gezählt für jedes Gerät, das es versucht.",
        "",
    ),
    "origins": (
        "Andere Webseiten, die Starlight nutzen dürfen",
        "Adressen wie http://homeassistant.local:8123, eine pro Zeile: Seiten von dort dürfen "
        "den Chat zeigen und ihn als du nutzen, in einem Browser auf diesem PC. Leer: nur "
        "Starlights eigene Seiten.",
        "Eine Seite von diesen Adressen kann alles tun, was du im Chat tun kannst.",
    ),
}


# -- the choice, on disk ---------------------------------------------------------------------


@dataclass
class Choice:
    level: str = "balanced"
    settings: dict[str, Any] = field(default_factory=dict)  # key -> protection value

    def value(self, switch: Switch) -> Any:
        """The protection's value chosen for a switch; None: as configured."""
        if switch.key in self.settings:
            return self.settings[switch.key]
        return switch.level_value(self.level)


def read_choice(config_dir: Path) -> Choice:
    """The saved choice. Anything that does not fit (edited by hand, an older version) is
    left out, so a mistake there never stops Starlight from starting (a file that is not
    YAML at all counts as Balanced)."""
    try:
        data = read_yaml(config_dir / SAFETY_FILE)
    except ConfigError:
        data = {}
    level = str(data.get("level")) if data.get("level") in LEVELS else "balanced"
    settings: dict[str, Any] = {}
    raw = data.get("settings")
    for key, value in raw.items() if isinstance(raw, dict) else []:
        switch = BY_KEY.get(str(key))
        if switch is None:
            continue
        try:
            settings[switch.key] = switch.check(value)
        except ValueError:
            continue
    return Choice(level, settings)


def save_choice(config_dir: Path, choice: Choice) -> None:
    write_yaml_config(
        config_dir / SAFETY_FILE,
        {"level": choice.level, "settings": dict(sorted(choice.settings.items()))},
        "# Safety limits, chosen on the Settings page (starlight/core/safety.py). A level\n"
        "# (strict, balanced, relaxed), then the protections set differently from it.\n"
        "# Applied over starlight.yaml and the module settings; starlight.local.yaml wins.",
    )


# -- applying it to the settings ---------------------------------------------------------------


def _put(data: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    """A copy of ``data`` with ``value`` at ``path`` (replacing, not merging)."""
    out = dict(data)
    head, *rest = path
    if rest:
        inner = out.get(head)
        out[head] = _put(inner if isinstance(inner, dict) else {}, tuple(rest), value)
    else:
        out[head] = value
    return out


def _layer(data: dict[str, Any], config_dir: Path, module: str | None) -> dict[str, Any]:
    choice = read_choice(config_dir)
    for switch in SWITCHES:
        if switch.module != module:
            continue
        value = choice.value(switch)
        if value is None:
            continue
        setting = switch.to_setting(value)
        if setting is not AS_CONFIGURED:
            data = _put(data, switch.path, setting)
    return data


def core_layer(data: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    """starlight.yaml's settings with the chosen safety limits applied (load_config)."""
    return _layer(data, config_dir, None)


def module_layer(data: dict[str, Any], config_dir: Path, module: str) -> dict[str, Any]:
    """A module's settings with the chosen safety limits applied (load_module_config)."""
    return _layer(data, config_dir, module)


# -- what the page shows -----------------------------------------------------------------------


def page_state(
    config_dir: Path, config: Any, modules: Mapping[str, Any], local_file: bool = False
) -> dict[str, Any]:
    """Everything the Settings page's Safety card shows: the level, then each group with
    its switches (value now, whether it was set apart from the level, what Strict and
    Relaxed would set ...). ``modules``: module name -> its settings object."""
    choice = read_choice(config_dir)
    groups = []
    for group, title in GROUPS.items():
        rows = []
        for switch in (s for s in SWITCHES if s.group == group):
            chosen = choice.value(switch)
            running = current(switch, config, modules)
            german = GERMAN.get(switch.key, ("", "", ""))
            rows.append(
                {
                    "key": switch.key,
                    "title": switch.title,
                    "help": switch.help,
                    "warning": switch.warning,
                    "de": {"title": german[0], "help": german[1], "warning": german[2]},
                    "kind": switch.kind,
                    # what the page shows: the choice (the running value can lag behind a
                    # restart); as configured: the running value
                    "value": chosen if chosen is not None else running,
                    "changed": switch.key in choice.settings,
                    "strict": switch.strict,
                    "relaxed": switch.relaxed,
                    "low": switch.low,
                    "high": switch.high,
                    "unit": switch.unit,
                    "live": switch.live,
                    "module": switch.module,
                    "module_on": switch.module is None or modules.get(switch.module) is not None,
                }
            )
        rows_title = {"title": title, "de": GERMAN_GROUPS.get(group, title)}
        groups.append({"key": group, **rows_title, "switches": rows})
    return {
        "level": choice.level,
        "levels": list(LEVELS),
        "changed": len(choice.settings),
        "groups": groups,
        # starlight.local.yaml is applied last: a setting there wins over this page
        "local_file": local_file,
    }


def change(config_dir: Path, data: Mapping[str, Any]) -> tuple[Choice, dict[str, Any]]:
    """The choice after one change from the page, and what changed (for the audit log):
    {"level": "strict"} starts again from that level; {"key": .., "value": ..} sets one
    switch; {"key": .., "reset": true} puts it back to the level's value.
    ValueError says what is wrong."""
    choice = read_choice(config_dir)
    if "level" in data:
        level = str(data["level"])
        if level not in LEVELS:
            raise ValueError(f"unknown level '{level}' (strict, balanced or relaxed)")
        return Choice(level), {"level": level}
    switch = BY_KEY.get(str(data.get("key", "")))
    if switch is None:
        raise ValueError("unknown safety switch")
    if data.get("reset"):
        choice.settings.pop(switch.key, None)
        return choice, {"switch": switch.key, "reset": True}
    try:
        choice.settings[switch.key] = switch.check(data.get("value"))
    except ValueError as exc:
        raise ValueError(f"{switch.title}: {exc}") from exc
    return choice, {"switch": switch.key, "value": choice.settings[switch.key]}


# -- the running Starlight ---------------------------------------------------------------------


def _get(obj: Any, path: Iterable[str]) -> Any:
    for name in path:
        obj = obj[name] if isinstance(obj, dict) else getattr(obj, name)
    return obj


def _set(obj: Any, path: tuple[str, ...], value: Any) -> None:
    parent = _get(obj, path[:-1])
    if isinstance(parent, dict):
        parent[path[-1]] = value
    else:
        setattr(parent, path[-1], value)


def current(switch: Switch, config: Any, modules: Mapping[str, Any]) -> Any:
    """The protection's value in the running Starlight; None: its module is not running.
    ``modules``: module name -> its settings object (or None)."""
    if switch.module is None:
        return switch.from_setting(_get(config, switch.path))
    settings = modules.get(switch.module)
    if settings is None:
        return None
    try:
        return switch.from_setting(_get(settings, switch.path))
    except (AttributeError, KeyError):
        return None


def refresh(paths: Any, configs: Iterable[Any], modules: Mapping[str, Any]) -> None:
    """Bring the running settings in line with the files again (after a change on the page):
    the core config objects in ``configs`` (the kernel's, the web app's) and the modules'
    settings objects. Read from the files, so the order of precedence is the same as at a
    start (starlight.local.yaml still wins)."""
    fresh = load_config(paths)
    targets = list(configs)
    fresh_modules: dict[str, Any] = {}
    for switch in SWITCHES:
        if switch.module is None:
            value = _get(fresh, switch.path)
            for config in targets:
                _set(config, switch.path, _copy(value))
            continue
        settings = modules.get(switch.module)
        if settings is None:
            continue
        if switch.module not in fresh_modules:
            fresh_modules[switch.module] = load_module_config(paths, switch.module, type(settings))
        try:
            _set(settings, switch.path, _copy(_get(fresh_modules[switch.module], switch.path)))
        except (AttributeError, KeyError):
            continue  # an older module without this setting


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value
