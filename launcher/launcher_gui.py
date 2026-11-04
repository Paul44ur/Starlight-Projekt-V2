"""The Starlight window.

Starlight.exe shows this when it is started plainly (double-click): a setup page where you
choose the model and options, a progress page while things download, and a status page
with buttons while Starlight runs. It drives the same code as the text version through the
launcher's ``Ui`` interface, so what happens is identical; only how it is shown differs.

Everything that touches tkinter runs on the main thread. Slow work runs in short daemon
threads (setup, lookups, hf, poll, restart, restart-model, kill, shutdown, and one per
`starlight` command) that report by putting (name, *args) on App.events (see App._pump).
A few plain attributes are shared without a lock: App.services (set by the setup thread
before it posts "running", cleared by _stop_services) and App._poll_busy (cleared by the
poll thread).
"""

from __future__ import annotations

import contextlib
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.error
import webbrowser
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any, ClassVar

# The Starlight logo (a gold sparkle with purple rays on a purple square), 64 x 64 PNG: the
# window icon. The header draws the same logo as vectors, so it is sharp at any scale.
ICON_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAMAAACdt4HsAAAA+VBMVEX///84FX5GGpdGGpZHGphNHKJSHqo3FH1LG55ZIbZV"
    "HrFlM7V6SrdeIsCaaLSUaaZiJMe0i6vDnqLYt5ZyNsTnzJOKVbfx1pP74YRlI9C7l5prNMSDTcj33ISmd61WJahiLLaOV7p4"
    "R8XKo5/DnpiBUs18TMmUadj523f833zLpZijfOGhds5bH8Csg7Tqy4rvzHTEm4eGU6f512vSrYuhc5l5SaZdL61nNartyWzS"
    "q3eyiYuSZZDwzGzkvW2keIp1Rpm8kntgKq7MpHmLY8ucds6Qas7as3RBGI1zSbuIWJ08FoVlOZJTJpe0i3ifdn6DV4xtQoo3"
    "FHyQrX1FAAAAAXRSTlMAQObYZgAAA+JJREFUWMONl4lW2kAUhoMEaCZGYxMkMY2TpgoIrVVRoLiUVlrRlC6+/8N0tiSzJXqP"
    "Bw/L981/ZzJhMIyiGltNs8WqrSv2ntnsNAy1Om9Ms44WFOabjoQ3TTPny49btABooz9ZYTZ5fstkAoUuSjTgj29J45vc+MDS"
    "VHWGLZm39AUqMjRfyYsGAtEMtbxt1xrI+hUCXfvb25qZKAUdfgXV8R3H2dkFqqA0oB4a1QEcx3X29hxSVREacgci77pv36KH"
    "GkNH6kDCXdfzyD/X0TRBexACKLzf7fqyQRCYhrCGnIBi+73evutKitxQCIpNBGTe3QmCnUpBS0mg8OFBEByErqaJliLgO2AT"
    "4IaRFwReFIaaCLnB0ExBwYfvekEQvwuZoVbQVgWIOwxQHSoCWCFAc5hwMxCSDrgeqCBhe0ojILsvSlgCNO77GAvi95wgsvke"
    "pDkgA/hp5OaCDwGpI9aD40apb70ksKLj44gKdmIqyCNE/ePIqhJwOzE6HmBFmHoUH554PhKQl9VLUSOw3HQw6Lv+iPLxcDgc"
    "+WF/MEhdzaWoW0bL8QfjjyM6POaR4eN44Gs3g/5K/BSd9iif1+fT6BMvqLuUAUj2R7HID4dn5xcJoE2Cys0EUFn29qHHpp/j"
    "h5PJ2eXVNEF60FYSsAhwNp/uHni9IC9RgOrL4vpmOodKAiSYTXf3jrxuCQex0MGkqNu7xfXXmyXk70hMcMAL9DQVfNMIyBxA"
    "0kKcr74qwC0scQvFLZEJylsCAN+nV5fa+Gf3V9Pv7KSgCLhvFlTJxVF8IvF0GTleI6AGiFc6Oe2eCMN/Pk0Au5UIAQoBZ8B3"
    "hdX4x4gXnP8Yr2wLcHyNoG2lP3+mln3O8TZ9TeVLQWmY9VepBSwwv8z5hzl6OktX6UzhTWO9lgz242MCcb/g4oyt/BN+2gbJ"
    "Y9/mDjik1rIA2o82zDfWORVcozwkPXlT5LFgzXUB5zNYHPTAE4lwuyxOiXi7QIEngjIDhJA/a84WWHBfKElE9JFKgXLW/YoF"
    "V9IpVeDXRiYYytM20Sxv0fW/rMAJzwQaA7HAh8lkATla4TMjkyKIimu0BvxzU+ZzgWDgFDeTyY0Wp4KMCKhBr1jefllqYDo8"
    "5olAF4Jpft3dbVpmS3mn4DOjUWcwN4v7janHKY8Omllp0Ci+/a7gKZZ1WIJKw58/dePjBIWBKSTJ3791OObzHioM/36pdMln"
    "5Mdbo1Gj2GwUusQz+vvxmTMUCrmVtQ5vPBuqgVeolfF4zhMDr8hepkVeNSBFVkdjnOepQXKwKOghk2iCizxeTa1BWzqcLGfn"
    "+TUKBPM///8DwwVbngACNVcAAAAASUVORK5CYII="
)


def bezier(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    steps: int = 14,
) -> list[float]:
    """Points along a cubic curve, as a flat list (x1, y1, x2, y2, ...), without the first."""
    out: list[float] = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        for axis in (0, 1):
            out.append(
                u**3 * p0[axis]
                + 3 * u * u * t * p1[axis]
                + 3 * u * t * t * p2[axis]
                + t**3 * p3[axis]
            )
    return out


def sparkle(cx: float, cy: float, r: float, pinch: float = 0.22) -> list[float]:
    """The four-pointed star of the logo (concave sides) as polygon points."""
    p = r * pinch
    corners = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    points: list[float] = [cx, cy - r]
    for i, start in enumerate(corners):
        end = corners[(i + 1) % 4]
        sx, sy = start[0] - cx, start[1] - cy
        ex, ey = end[0] - cx, end[1] - cy
        # control points: along the start direction, pinched towards the middle
        c1 = (cx + (sx * p / r) + (ex * p / r) * 0.45, cy + (sy * p / r) + (ey * p / r) * 0.45)
        c2 = (cx + (ex * p / r) + (sx * p / r) * 0.45, cy + (ey * p / r) + (sy * p / r) * 0.45)
        points += bezier(start, c1, c2, end)
    return points


def rounded_square(size: float, radius: float, steps: int = 8) -> list[float]:
    """A square with rounded corners as polygon points."""
    points: list[float] = []
    for cx, cy, start in (
        (size - radius, radius, -90), (size - radius, size - radius, 0),
        (radius, size - radius, 90), (radius, radius, 180),
    ):  # fmt: skip
        for i in range(steps + 1):
            angle = math.radians(start + 90 * i / steps)
            points += [cx + radius * math.cos(angle), cy + radius * math.sin(angle)]
    return points


@dataclass(frozen=True)
class Palette:
    bg: str
    panel: str
    panel2: str
    border: str
    text: str
    dim: str
    accent: str
    on_accent: str
    ok: str
    warn: str
    bad: str  # red for text and status dots
    danger: str  # red behind white text (the stop button): dark enough to read on


# The same colors as the web pages (starlight/web/static/style.css: --accent, --on-accent,
# button.danger), so the window and the chat look like one program.
DARK = Palette(
    bg="#0f1115", panel="#171a21", panel2="#1e222b", border="#2a2f3a", text="#e6e8ec",
    dim="#9aa3b2", accent="#6ea8fe", on_accent="#0b1220", ok="#3fb950", warn="#d29922",
    bad="#f85149", danger="#b3231d",
)  # fmt: skip
LIGHT = Palette(
    bg="#f6f7f9", panel="#ffffff", panel2="#eef0f4", border="#d8dce3", text="#1f2328",
    dim="#5a6372", accent="#2563eb", on_accent="#ffffff", ok="#1a7f37", warn="#9a6700",
    bad="#cf222e", danger="#b3231d",
)  # fmt: skip


def mix(colour: str, other: str, amount: float) -> str:
    """``colour`` moved ``amount`` (0 to 1) of the way towards ``other``: the hover shades,
    like color-mix() on the web pages."""
    a = [int(colour[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b, strict=True))


def system_uses_light_theme() -> bool:
    """Whether Windows is set to light apps. Dark everywhere else (and when unknown)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return bool(winreg.QueryValueEx(handle, "AppsUseLightTheme")[0])
    except OSError:
        return False


def flash_window(root: tk.Tk) -> None:
    """Make the taskbar button blink until the window is looked at (Windows), when it is
    not in front: a model that took minutes to load is ready."""
    if sys.platform != "win32" or root.focus_displayof() is not None:
        return
    import ctypes
    from ctypes import wintypes

    class FlashInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD)]  # fmt: skip

    with contextlib.suppress(Exception):
        handle = ctypes.windll.user32.GetParent(root.winfo_id())
        info = FlashInfo(ctypes.sizeof(FlashInfo), handle, 0x3 | 0xC, 0, 0)  # all, until focus
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def selected(box: tk.Listbox) -> int | None:
    """The chosen line of a list box, or None."""
    chosen = box.curselection()  # type: ignore[no-untyped-call]
    return int(chosen[0]) if chosen else None


def shorten(text: str, limit: int = 40) -> str:
    """A name cut to ``limit`` characters with an ellipsis in the middle (the start and the
    end of a model name say the most: maker, size, quantization)."""
    if len(text) <= limit:
        return text
    keep = limit - 1
    return text[: keep - keep // 2] + "…" + text[-(keep // 2) :]


def open_folder(path: Path) -> None:
    """Show a folder in the file manager."""
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined,unused-ignore]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


LMSTUDIO = "lmstudio"  # the model card for LM Studio (it runs the model; nothing is downloaded)
LMS_AUTO = "Whichever is loaded in LM Studio"  # the LM Studio model choice for "auto"
STYLE_NAMES = {
    "precise": "Precise: sticks closely to the facts",
    "balanced": "Balanced (the usual)",
    "creative": "Creative: more varied wording",
}
CONTEXT_NAMES = {
    0: "Auto: as much as fits your graphics card",
    8192: "8k tokens: fastest, remembers the least",
    16384: "16k tokens",
    32768: "32k tokens (the usual)",
    49152: "48k tokens",
    65536: "64k tokens",
    98304: "96k tokens",
    131072: "128k tokens: remembers the most, needs much video memory",
}


def window_language() -> str:
    """ "de" when Windows (or the system) speaks German, else "en"; STARLIGHT_LANG=de/en
    chooses."""
    chosen = os.environ.get("STARLIGHT_LANG", "").strip().lower()[:2]
    if chosen in ("de", "en"):
        return chosen
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes

            windll: Any = ctypes.windll  # type: ignore[attr-defined,unused-ignore]
            return "de" if windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x07 else "en"
    import locale

    system = (locale.getlocale()[0] or os.environ.get("LANG", "")).lower()
    return "de" if system.startswith("de") else "en"


GERMAN = {
    "Downloading uv (installs Python and Starlight's packages)": (
        "Lade uv herunter (installiert Python und Starlights Pakete)"
    ),
    "Downloading whisper.cpp (speech to text)": "Lade whisper.cpp herunter (Sprache zu Text)",
    "Downloading Piper (text to speech)": "Lade Piper herunter (Text zu Sprache)",
    "Setting up Python and Starlight's packages": "Richte Python und Starlights Pakete ein",
    "Installing the Chromium browser": "Installiere den Chromium-Browser",
    "Getting LM Studio ready": "Bereite LM Studio vor",
    "Checking the model server": "Prüfe den Modell-Server",
    "Starting the model server on the GPU": "Starte den Modell-Server auf der Grafikkarte",
    "Precise: sticks closely to the facts": "Genau: bleibt eng bei den Fakten",
    "Balanced (the usual)": "Ausgewogen (üblich)",
    "Creative: more varied wording": "Kreativ: abwechslungsreichere Worte",
    "Auto: as much as fits your graphics card": (
        "Automatisch: so viel, wie auf deine Grafikkarte passt"
    ),
    "8k tokens: fastest, remembers the least": (
        "8k Tokens: am schnellsten, merkt sich am wenigsten"
    ),
    "16k tokens": "16k Tokens",
    "32k tokens (the usual)": "32k Tokens (üblich)",
    "48k tokens": "48k Tokens",
    "64k tokens": "64k Tokens",
    "96k tokens": "96k Tokens",
    "128k tokens: remembers the most, needs much video memory": (
        "128k Tokens: merkt sich am meisten, braucht viel Grafikspeicher"
    ),
    "MODEL": "MODELL",
    "OPTIONS": "OPTIONEN",
    (
        "Starlight's built-in test model is used (started with --no-model). It only repeats "
        "what you say, so nothing is downloaded."
    ): (
        "Starlights eingebautes Testmodell wird benutzt (gestartet mit --no-model). Es "
        "wiederholt nur, was du sagst; es wird nichts heruntergeladen."
    ),
    "Graphics card: looking ...": "Grafikkarte: suche ...",
    "Model in LM Studio:": "Modell in LM Studio:",
    "Find more models on Hugging Face ...": "Weitere Modelle auf Hugging Face finden ...",
    "Search Hugging Face for other GGUF models and use one of them": (
        "Andere GGUF-Modelle auf Hugging Face suchen und eines davon nutzen"
    ),
    "Your choices are saved in config/launcher.json.": (
        "Deine Auswahl wird in config/launcher.json gespeichert."
    ),
    "Cancel": "Abbrechen",
    "Quit": "Beenden",
    "Install and start": "Installieren und starten",
    "Run the model on the graphics card (turn off only if there is none: much slower)": (
        "Das Modell auf der Grafikkarte laufen lassen (nur ausschalten, wenn keine da ist: viel "
        "langsamer)"
    ),
    "Speech: talk to Starlight and hear its answers (adds about 0.6 GB)": (
        "Sprache: mit Starlight sprechen und die Antworten hören (etwa 0,6 GB mehr)"
    ),
    "Read the screen: install Tesseract OCR (Windows asks for administrator approval)": (
        "Bildschirm lesen: Tesseract OCR installieren (Windows fragt nach Administrator-Zustimmung)"
    ),
    "Statistics page with charts about what Starlight is doing": (
        "Statistikseite mit Diagrammen dazu, was Starlight tut"
    ),
    "Let the model think before it answers (better at hard questions, slower)": (
        "Das Modell vor der Antwort nachdenken lassen (besser bei schweren Fragen, langsamer)"
    ),
    "Start Starlight when I log in to Windows": (
        "Starlight starten, wenn ich mich bei Windows anmelde"
    ),
    "Open the chat in Starlight's own window, not in the browser": (
        "Den Chat in Starlights eigenem Fenster öffnen, nicht im Browser"
    ),
    "Answers:": "Antworten:",
    "Context:": "Kontext:",
    (
        "How freely the model chooses its words: precise for facts and tools, creative for stories "
        "and ideas."
    ): (
        "Wie frei das Modell seine Worte wählt: genau für Fakten und Werkzeuge, kreativ für "
        "Geschichten und Ideen."
    ),
    (
        "How much of the conversation (and of long texts) the model keeps in mind at once. More "
        "needs more video memory; Auto works it out from the model file and your graphics card."
    ): (
        "Wie viel vom Gespräch (und von langen Texten) das Modell auf einmal im Kopf behält. "
        "Mehr braucht mehr Grafikspeicher; Automatisch rechnet es aus Modelldatei und "
        "Grafikkarte aus."
    ),
    "Find a model on Hugging Face": "Ein Modell auf Hugging Face finden",
    "Models (most downloaded first)": "Modelle (meistgeladene zuerst)",
    "Files (pick one size)": "Dateien (eine Größe wählen)",
    "Asking Hugging Face ...": "Frage Hugging Face ...",
    "Pick a file first": "Erst eine Datei wählen",
    "Search": "Suchen",
    "Use this model": "Dieses Modell nutzen",
    "Models not from the list are not tested with Starlight: try the tool-use exam once it runs.": (
        "Modelle außerhalb der Liste sind nicht mit Starlight getestet: Probier den "
        "Werkzeug-Test, sobald es läuft."
    ),
    "From Hugging Face. Downloaded when you start.": (
        "Von Hugging Face. Wird beim Start heruntergeladen."
    ),
    "Download link not found": "Download-Link nicht gefunden",
    "Recommended for your PC": "Empfohlen für deinen PC",
    "Too big for your graphics card": "Zu groß für deine Grafikkarte",
    "Downloaded": "Heruntergeladen",
    "Remove": "Entfernen",
    "Running": "Läuft",
    "Settings": "Einstellungen",
    "Changes apply after Starlight restarts.": (
        "Änderungen gelten nach einem Neustart von Starlight."
    ),
    "Save and restart": "Speichern und neu starten",
    "Show details": "Details zeigen",
    "Pause download": "Download pausieren",
    "Resume download": "Download fortsetzen",
    "Open chat": "Chat öffnen",
    # the status page (on_status)
    "Model": "Modell",
    "Chat": "Chat",
    "Kill switch": "Not-Aus",
    "TOOLS": "WERKZEUGE",
    "kill switch on": "Not-Aus aktiv",
    "ready": "bereit",
    "starting": "startet",
    "Ready: {model}": "Bereit: {model}",
    "The built-in test model (it only repeats what you say)": (
        "Das eingebaute Testmodell (es wiederholt nur, was du sagst)"
    ),
    "Stopped by itself; starting it again ...": "Von selbst angehalten; startet neu ...",
    "Stopped: {reason}": "Angehalten: {reason}",
    "see the logs": "siehe die Logs",
    "Paused, the graphics card is free ({detail})": (
        "Pausiert, die Grafikkarte ist frei ({detail})"
    ),
    "Loading into video memory ... {elapsed}": "Lädt in den Grafikspeicher ... {elapsed}",
    "Starting ...": "Startet ...",
    "Off": "Aus",
    "Ready once the chat is up": "Bereit, sobald der Chat läuft",
    "ENGAGED: Starlight will not do anything until you resume": (
        "AKTIV: Starlight tut nichts, bis du fortsetzt"
    ),
    "Off: Starlight can act": "Aus: Starlight kann handeln",
    "Resume": "Fortsetzen",
    "Resume now": "Jetzt fortsetzen",
    "Reload the model": "Modell neu laden",
    "Something needs attention": "Etwas braucht deine Aufmerksamkeit",
    "The model server stopped and could not be restarted.": (
        "Der Modell-Server ist stehen geblieben und ließ sich nicht neu starten."
    ),
    "Starlight is stopped": "Starlight ist angehalten",
    "The kill switch is engaged. Press Resume to let it act again.": (
        "Der Not-Aus ist aktiv. Drücke auf Fortsetzen, damit Starlight wieder handeln kann."
    ),
    "Starlight is paused for a game": "Starlight pausiert für ein Spiel",
    "The model comes back when the game closes, or press Resume now.": (
        "Das Modell kommt zurück, sobald das Spiel beendet ist, oder drücke auf Jetzt fortsetzen."
    ),
    "Starlight is starting ...": "Starlight startet ...",
    "The model takes a minute to load; the chat opens by itself when it is ready.": (
        "Das Modell braucht eine Minute zum Laden; der Chat öffnet sich von selbst, sobald er "
        "bereit ist."
    ),
    "Starlight is running": "Starlight läuft",
    "Your chat is at {url}. Close this window to stop Starlight.": (
        "Dein Chat ist unter {url}. Schließe dieses Fenster, um Starlight zu beenden."
    ),
    "The chat is in its own window (Open chat brings it back). Close this window to stop "
    "Starlight.": (
        "Der Chat ist in einem eigenen Fenster („Chat öffnen“ holt es zurück). Schließe dieses "
        "Fenster, um Starlight zu beenden."
    ),
    "Starlight's own window could not open ({reason}); the chat is in the browser.": (
        "Starlights eigenes Fenster ließ sich nicht öffnen ({reason}); der Chat ist im Browser."
    ),
    "Folder: {path}": "Ordner: {path}",
    "Statistics": "Statistik",
    "Stop everything": "Alles anhalten",
    "Restart model server": "Modell-Server neu starten",
    "Check everything": "Alles prüfen",
    "Open logs folder": "Log-Ordner öffnen",
    "Speed test": "Geschwindigkeitstest",
    "How fast the model answers (tokens per second)": (
        "Wie schnell das Modell antwortet (Tokens pro Sekunde)"
    ),
    "Tool-use exam": "Werkzeug-Test",
    "Eight small tasks: does this model use Starlight's tools well?": (
        "Acht kleine Aufgaben: Nutzt dieses Modell Starlights Werkzeuge gut?"
    ),
    "Disk space": "Speicherplatz",
    "What takes space: models and folders; remove unused models": (
        "Was Platz braucht: Modelle und Ordner; ungenutzte Modelle entfernen"
    ),
    "Open Starlight's folder": "Starlights Ordner öffnen",
    "Settings ...": "Einstellungen ...",
    "Quit Starlight": "Starlight beenden",
    "Copy": "Kopieren",
    "Close": "Schließen",
    "Open": "Öffnen",
    "Starlight could not start": "Starlight konnte nicht starten",
    "Copy details": "Details kopieren",
    "Try again": "Nochmal versuchen",
    "Stopping ...": "Halte an ...",
    "setup": "Einrichtung",
    "getting ready": "wird vorbereitet",
    "could not start": "konnte nicht starten",
}
LANGUAGE = window_language()


def tr(text: str) -> str:
    """The window's words in German when Windows speaks German; English otherwise."""
    if LANGUAGE != "de":
        return text
    if text.startswith("What's new in "):
        return "Neu in " + text.removeprefix("What's new in ")
    return GERMAN.get(text, text)


def trf(template: str, **values: object) -> str:
    """A text with blanks ({name}) in the window's language, then filled in."""
    return tr(template).format(**values)


if LANGUAGE == "de":  # (the choice lists show these; they are matched back by value)
    STYLE_NAMES.update({k: tr(v) for k, v in STYLE_NAMES.items()})
    CONTEXT_NAMES.update({k: tr(v) for k, v in CONTEXT_NAMES.items()})


class GuiUi:
    """The launcher's Ui for the window: everything goes into a queue the window reads."""

    captures_output = True

    def __init__(self, events: queue.Queue[tuple[Any, ...]], log: Path | None = None) -> None:
        self.events = events
        self.log = log

    def _write_log(self, text: str) -> None:
        if self.log is None or not text.strip():
            return
        with contextlib.suppress(OSError):
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text.strip()}\n")

    def say(self, text: str = "") -> None:
        text = text.lstrip("\r").rstrip()
        self._write_log(text)
        self.events.put(("say", text))

    def step(self, text: str) -> None:
        self._write_log(f"== {text}")
        self.events.put(("step", tr(text)))

    def progress(self, done: int, total: int | None, started: float) -> None:
        self.events.put(("progress", done, total, started))

    def waiting(self, text: str) -> None:
        self.events.put(("waiting", text.lstrip("\r").strip()))

    def run_output(self, line: str) -> None:
        self._write_log(line)
        self.events.put(("say", line))

    def news(self, title: str, text: str) -> None:
        """What's new after an update: in a box of its own."""
        self._write_log(title)
        self.events.put(("note", f"{tr(title)}\n\n{text}"))

    def ask(self, question: str, default: bool) -> bool:
        return default  # the setup page asked everything: no second question in a box


def rounded_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, r: float,
                 **options: Any) -> int:  # fmt: skip
    """A rectangle with rounded corners on a canvas (a smoothed polygon)."""
    points = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
              x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]  # fmt: skip
    return canvas.create_polygon(points, smooth=True, **options)


class Tooltip:
    """A short note that appears when the pointer rests on a widget for a moment."""

    def __init__(self, app: App, widget: tk.Misc, text: str) -> None:
        self.app, self.widget, self.text = app, widget, tr(text)
        self.window: tk.Toplevel | None = None
        self.job: str | None = None
        widget.bind("<Enter>", self._soon, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _soon(self, _event: object = None) -> None:
        self._hide()
        self.job = self.widget.after(450, self._show)

    def _show(self) -> None:
        self.job = None
        c, px = self.app.c, self.app.px
        x, y = self.widget.winfo_pointerxy()
        self.window = window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.configure(bg=c.border)
        tk.Label(window, text=self.text, font=self.app.f_small, bg=c.panel2, fg=c.text,
                 padx=px(8), pady=px(4), justify="left",
                 wraplength=px(320)).pack(padx=1, pady=1)  # fmt: skip
        window.wm_geometry(f"+{x + px(12)}+{y + px(16)}")

    def _hide(self, _event: object = None) -> None:
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class CheckRow(tk.Frame):
    """A check box drawn like the rest of the window (the model cards' round marks): a
    rounded square that fills with a check mark, and its text. A click or Space toggles it."""

    def __init__(
        self,
        app: App,
        parent: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        command: Any = None,
    ) -> None:
        c = app.c
        super().__init__(parent, bg=c.bg, cursor="hand2", takefocus=1, highlightthickness=1,
                         highlightbackground=c.bg, highlightcolor=c.accent)  # fmt: skip
        self.app, self.variable, self.command = app, variable, command
        self.disabled = False
        size = app.px(18)
        self.mark = tk.Canvas(self, width=size, height=size, bg=c.bg, highlightthickness=0)
        self.mark.pack(side="left", padx=(app.px(2), app.px(8)), pady=app.px(3))
        self.label = tk.Label(self, text=tr(text), bg=c.bg, fg=c.text, font=app.f, anchor="w",
                              justify="left")  # fmt: skip
        self.label.pack(side="left", fill="x")
        for widget in (self, self.mark, self.label):
            widget.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        variable.trace_add("write", lambda *_: self.draw())
        app.check_rows.append(self)
        self.draw()

    def _toggle(self, _event: object = None) -> str:
        if not self.disabled:
            self.variable.set(not self.variable.get())
            if self.command is not None:
                self.command()
        return "break"

    def draw(self) -> None:
        c, canvas = self.app.c, self.mark
        canvas.delete("all")
        size = int(canvas.cget("width"))
        pad, radius = size * 0.08, size * 0.24
        colour = c.dim if self.disabled else c.accent
        if self.variable.get():
            rounded_rect(canvas, pad, pad, size - pad, size - pad, radius, fill=colour, outline="")
            points = [0.27, 0.52, 0.43, 0.68, 0.74, 0.34]
            width = max(2, size // 8)
            canvas.create_line(*[p * size for p in points], fill=c.on_accent, width=width,
                               capstyle="round", joinstyle="round")  # fmt: skip
        else:
            rounded_rect(canvas, pad, pad, size - pad, size - pad, radius, fill=c.panel,
                         outline=c.dim, width=2)  # fmt: skip
        self.label.configure(fg=c.dim if self.disabled else c.text)
        self.configure(cursor="arrow" if self.disabled else "hand2")

    @property
    def text(self) -> str:
        return str(self.label.cget("text"))

    def set_text(self, text: str) -> None:
        self.label.configure(text=text)

    def state(self, spec: list[str]) -> None:
        """As ttk widgets: ["disabled"] or ["!disabled"]."""
        if "disabled" in spec or "!disabled" in spec:
            self.disabled = "disabled" in spec
            self.draw()

    def instate(self, spec: list[str]) -> bool:
        return all(self.disabled == (s == "disabled") for s in spec if s.endswith("disabled"))


class App:
    """The window and everything on it."""

    def __init__(self, launcher: Any, args: Any) -> None:
        self.L = launcher
        self.args = args
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._dpi_aware()
        self.root = tk.Tk()  # raises TclError when there is no screen
        self.root.withdraw()
        self.c = LIGHT if system_uses_light_theme() else DARK
        # 96 dots per inch is 100 %. px() scales pixel sizes; fonts in points scale by themselves.
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.services: Any = None
        self.group: Any = None
        self.page = ""
        self.pages: dict[str, ttk.Frame] = {}
        self._poll_busy = False
        self.opened_browser = False
        self.chat_window: Any = None  # the chat in Starlight's own window (or the browser)
        self.window_was_open = False
        self.polling = False
        self._poll_job: str | None = None  # the timer of the status polling
        self.closing = False
        self.exited = False
        self.in_settings = False
        self.sizes: dict[str, int | None] = {}  # download size per model link; None: unknown
        self.missing: set[str] = set()  # model links that answered "not found"
        self.lmstudio: Any = None  # what LM Studio said (launcher.LMStudioState); None: not asked
        self.gpu: Any = None
        self.fatal = ""
        try:
            self.settings = launcher.load_settings(args)
        except launcher.LauncherError as exc:
            self.fatal = str(exc)
            self.settings = launcher.Settings(root=Path.cwd())
        self.settings.assume_yes = True  # (maybe_tesseract: the setup page offered OCR)
        self.ui = GuiUi(self.events, self.settings.root / "logs" / "launcher.log")
        self.root.report_callback_exception = self._callback_failed
        self._init_window()
        self._init_fonts()
        self._init_style()
        self._build_header()
        self._build_scroller()
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self._build_setup()
        self.root.bind("<Up>", lambda _event: self._move_selection(-1))
        self.root.bind("<Down>", lambda _event: self._move_selection(1))
        self._build_progress()
        self._build_running()
        self._build_error()

    # -- window, fonts, colors ----------------------------------------------------------------

    @staticmethod
    def _dpi_aware() -> None:
        if sys.platform == "win32":
            import ctypes

            process_system_dpi_aware = 1  # sharp text; must come before tk.Tk()
            with contextlib.suppress(Exception):  # (Windows without shcore)
                ctypes.windll.shcore.SetProcessDpiAwareness(process_system_dpi_aware)

    def px(self, value: int) -> int:
        return int(value * self.scale)

    def _init_window(self) -> None:
        root = self.root
        root.title("Starlight")
        root.configure(bg=self.c.bg)
        with contextlib.suppress(tk.TclError):
            root.iconphoto(True, tk.PhotoImage(data=ICON_PNG))
        # Never bigger than the screen: a scaled laptop screen would hide the buttons at the
        # bottom. The title bar and the taskbar take about 100 px; the pages scroll instead.
        width = min(self.px(820), root.winfo_screenwidth() - 40)
        height = min(self.px(700), root.winfo_screenheight() - 100)
        root.minsize(min(self.px(720), width), min(self.px(600), height))
        x = max((root.winfo_screenwidth() - width) // 2, 0)
        y = max((root.winfo_screenheight() - height) // 3, 0)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.protocol("WM_DELETE_WINDOW", self.quit_app)

    def _build_scroller(self) -> None:
        """The pages sit in a canvas with a scrollbar that shows only when a page is taller
        than the window, so the buttons at the bottom of a page stay reachable."""
        holder = self.scroll_holder = ttk.Frame(self.root)
        holder.pack(fill="both", expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.scroller = tk.Canvas(holder, bg=self.c.bg, highlightthickness=0, borderwidth=0)
        self.scroller.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.scroller.yview)
        self.scroller.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.scrollbar.grid_remove()
        self._scrollable = False
        self.wrapping: list[ttk.Label] = []  # labels that wrap at the page width
        self.check_rows: list[CheckRow] = []  # (their text wraps beside the box)
        self._wrapped_at = 0  # the page width they were wrapped at
        self._cards_pending = False  # a layout of the model cards is waiting (_layout_cards)
        self.cards: dict[str, dict[str, Any]] = {}
        self.status_rows: dict[str, tuple[tk.Canvas, int, tk.Label]] = {}  # the status page's
        self.status_title_width = 0
        self.body = ttk.Frame(
            self.scroller, padding=(self.px(28), self.px(18), self.px(28), self.px(18))
        )
        self.body_item = self.scroller.create_window(0, 0, window=self.body, anchor="nw")
        self.scroller.bind("<Configure>", self._fit_body)
        self.body.bind("<Configure>", self._fit_body)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(sequence, self._scroll_wheel)

    def _fit_body(self, _event: object = None) -> None:
        """The page is as wide as the window and as tall as the window or its content, and the
        scrollbar shows only when the content is taller than the window."""
        window_height = self.scroller.winfo_height()
        content = self.body.winfo_reqheight()
        self.scroller.itemconfigure(
            self.body_item, width=self.scroller.winfo_width(), height=max(window_height, content)
        )
        self.scroller.configure(
            scrollregion=(0, 0, self.scroller.winfo_width(), max(window_height, content))
        )
        width = self._page_width()
        if width != self._wrapped_at:  # the window got wider or narrower: rewrap the text
            self._wrapped_at = width
            for label in self.wrapping:
                label.configure(wraplength=width)
            for check in self.check_rows:
                check.label.configure(wraplength=width - self.px(36))
            self._layout_cards_soon()
            self._wrap_status(width)
        scrollable = content > window_height
        if scrollable != self._scrollable:
            self._scrollable = scrollable
            if scrollable:
                self.scrollbar.grid()
            else:
                self.scrollbar.grid_remove()

    def _check_layout(self) -> None:
        """Re-check the fit every 200 ms: a label that grew (say, the graphics card's name)
        makes the page taller, and a resize is not the only thing that changes it."""
        if self.exited:
            return
        self._fit_body()
        self.root.after(200, self._check_layout)

    def _scroll_wheel(self, event: tk.Event[tk.Misc]) -> None:
        """Scroll the page with the mouse wheel. Text boxes scroll themselves, and a page that
        fits is not scrolled."""
        if not self._scrollable or isinstance(event.widget, tk.Text):
            return
        up = event.num == 4 or (event.num != 5 and event.delta > 0)
        self.scroller.yview_scroll(-1 if up else 1, "units")

    def _init_fonts(self) -> None:
        family = (
            "Segoe UI"
            if sys.platform == "win32"
            else tkfont.nametofont("TkDefaultFont").actual("family")
        )
        self.f = tkfont.Font(family=family, size=10)
        self.f_bold = tkfont.Font(family=family, size=10, weight="bold")
        self.f_small = tkfont.Font(family=family, size=9)
        self.f_link = tkfont.Font(family=family, size=9, underline=True)
        self.f_big = tkfont.Font(family=family, size=18, weight="bold")
        self.f_head = tkfont.Font(family=family, size=12, weight="bold")
        self.f_mono = tkfont.Font(
            family="Consolas" if sys.platform == "win32" else "monospace", size=9
        )

    def _init_style(self) -> None:
        c = self.c
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=c.bg, foreground=c.text, fieldbackground=c.panel2,
                        bordercolor=c.border, lightcolor=c.border, darkcolor=c.border,
                        troughcolor=c.panel2, focuscolor=c.accent, focusthickness=0,
                        focussolid=1, font=self.f)  # fmt: skip
        # (the focus ring is 0 thick until the keyboard is used: _show_focus)
        style.configure("TFrame", background=c.bg)
        style.configure("Card.TFrame", background=c.panel)
        style.configure("TLabel", background=c.bg, foreground=c.text)
        style.configure("Dim.TLabel", foreground=c.dim)
        style.configure("Small.TLabel", foreground=c.dim, font=self.f_small)
        style.configure("Big.TLabel", font=self.f_big)
        style.configure("Head.TLabel", font=self.f_head)
        style.configure("Section.TLabel", foreground=c.dim, font=self.f_small)
        style.configure("TButton", background=c.panel2, foreground=c.text, borderwidth=1,
                        padding=(self.px(14), self.px(7)), relief="flat")  # fmt: skip
        # Pointed at: a light accent tint and an accent border (as the web pages' buttons).
        style.map("TButton", background=[("active", mix(c.panel2, c.accent, 0.14)),
                                         ("disabled", c.panel)],
                  foreground=[("disabled", c.dim)], bordercolor=[("active", c.accent)])  # fmt: skip
        accent_hover = mix(c.accent, "#ffffff" if c is DARK else "#000000", 0.14)
        style.configure("Accent.TButton", background=c.accent, foreground=c.on_accent,
                        bordercolor=c.accent, focuscolor=c.on_accent, font=self.f_bold)  # fmt: skip
        style.map("Accent.TButton", background=[("active", accent_hover), ("disabled", c.panel2)],
                  bordercolor=[("active", accent_hover)],
                  foreground=[("disabled", c.dim)])  # fmt: skip
        # c.danger, not c.bad: white text on the bright red of the dark look was too faint
        danger_hover = mix(c.danger, "#ffffff", 0.12)
        style.configure("Danger.TButton", background=c.danger, foreground="#ffffff",
                        bordercolor=c.danger, focuscolor="#ffffff")  # fmt: skip
        style.map("Danger.TButton", background=[("active", danger_hover), ("disabled", c.panel2)],
                  bordercolor=[("active", danger_hover)],
                  foreground=[("disabled", c.dim)])  # fmt: skip
        # The status page: its main buttons large, its tools quieter (outlined, no fill).
        for name in ("Big.TButton", "Big.Accent.TButton", "Big.Danger.TButton"):
            style.configure(name, padding=(self.px(16), self.px(11)))
        # (clam draws no border around a "flat" button; "solid" is one thin line)
        style.configure("Tool.TButton", background=c.bg, lightcolor=c.bg, darkcolor=c.bg,
                        bordercolor=c.border, relief="solid",
                        padding=(self.px(10), self.px(6)))  # fmt: skip
        style.map("Tool.TButton", background=[("active", mix(c.bg, c.accent, 0.12)),
                                              ("disabled", c.bg)],
                  bordercolor=[("active", c.accent)], relief=[("pressed", "solid")],
                  foreground=[("disabled", c.dim)])  # fmt: skip
        style.configure("TCheckbutton", background=c.bg, foreground=c.text, indicatorcolor=c.panel2,
                        indicatorbackground=c.panel2, padding=(0, self.px(3)))  # fmt: skip
        style.map("TCheckbutton",
                  indicatorbackground=[("disabled", c.panel2), ("selected", c.accent)],
                  indicatorforeground=[("disabled", c.dim), ("selected", c.on_accent)],
                  foreground=[("disabled", c.dim)], background=[("active", c.bg)])  # fmt: skip
        style.configure("TCombobox", fieldbackground=c.panel2, background=c.panel2,
                        foreground=c.text, arrowcolor=c.text, bordercolor=c.border,
                        selectbackground=c.panel2, selectforeground=c.text,
                        padding=(self.px(6), self.px(3)))  # fmt: skip
        style.map("TCombobox", fieldbackground=[("readonly", c.panel2)],
                  foreground=[("readonly", c.text)], background=[("active", c.panel)],
                  selectbackground=[("readonly", c.panel2)],
                  selectforeground=[("readonly", c.text)])  # fmt: skip
        # its drop-down list is a plain Tk listbox, styled through the option database
        for option, value in (
            ("background", c.panel2),
            ("foreground", c.text),
            ("selectBackground", c.accent),
            ("selectForeground", c.on_accent),
        ):
            self.root.option_add(f"*TCombobox*Listbox.{option}", value)  # fmt: skip
        style.configure("Horizontal.TProgressbar", background=c.accent, troughcolor=c.panel2,
                        bordercolor=c.panel2, lightcolor=c.accent, darkcolor=c.accent,
                        thickness=self.px(10))  # fmt: skip
        # A thin scrollbar without arrow buttons, as in the browser.
        style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
            "sticky": "ns",
            "children": [("Vertical.Scrollbar.thumb", {"sticky": "nswe"})],
        })])  # fmt: skip
        style.configure("Vertical.TScrollbar", troughcolor=c.bg, background=c.border,
                        bordercolor=c.bg, lightcolor=c.border, darkcolor=c.border,
                        arrowsize=self.px(9), gripsize=0)  # fmt: skip
        pointed = [("pressed", c.dim), ("active", c.dim)]
        style.map("Vertical.TScrollbar", background=pointed, lightcolor=pointed, darkcolor=pointed)
        # Show the focus ring after Tab (or Shift+Tab), hide it after a click: keyboard users
        # see where they are, and a clicked button gets no ring (the web's :focus-visible).
        self._keyboard_focus = False
        for sequence in ("<<NextWindow>>", "<<PrevWindow>>"):  # (Tab and Shift+Tab)
            self.root.bind_all(sequence, lambda _event: self._show_focus(True), add="+")
        self.root.bind_all("<ButtonPress>", lambda _event: self._show_focus(False), add="+")

    def _show_focus(self, keyboard: bool) -> None:
        if keyboard != self._keyboard_focus:
            self._keyboard_focus = keyboard
            ttk.Style(self.root).configure(".", focusthickness=self.px(2) if keyboard else 0)

    def _build_header(self) -> None:
        c = self.c
        bar = tk.Frame(self.root, bg=c.panel, highlightthickness=0)
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=c.panel)
        inner.pack(fill="x", padx=self.px(28), pady=self.px(12))
        self._draw_logo(inner)
        tk.Label(inner, text="Starlight", font=self.f_head, bg=c.panel, fg=c.text).pack(
            side="left", padx=(self.px(10), 0)
        )
        tk.Label(
            inner, text=f"launcher {self.L.VERSION}", font=self.f_small, bg=c.panel, fg=c.dim
        ).pack(side="right")
        tk.Frame(self.root, bg=c.border, height=1).pack(fill="x")

    def _draw_logo(self, parent: tk.Misc) -> None:
        """The logo, drawn as vectors: purple square (lavender in the light look), the gold
        sparkle and its rays."""
        light = self.c is LIGHT
        size = self.px(34)
        canvas = tk.Canvas(parent, width=size, height=size, bg=self.c.panel, highlightthickness=0)
        scale = size / 512
        bg, ray, edge = ("#f3efff", "#c9a6ff", "#d9a63a") if light else ("#4a1d9c", "#d7c2ff", "")
        canvas.create_polygon(rounded_square(size, 112 * scale), fill=bg, outline="")
        for angle in (0, 32, -32):
            cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            points: list[float] = []
            for x, y in ((-226, 0), (0, -16), (226, 0), (0, 16)):
                points += [(256 + x * cos - y * sin) * scale, (256 + x * sin + y * cos) * scale]
            canvas.create_polygon(points, fill=ray, outline="")
        canvas.create_polygon(
            [v * scale for v in sparkle(256, 256, 214, 0.36)], fill="#f8d56b", outline=edge
        )
        canvas.pack(side="left")

    # -- pages ----------------------------------------------------------------------------------

    def show(self, name: str) -> None:
        for page_name, frame in self.pages.items():
            if page_name == name:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_remove()
        if name == "setup":
            self.setup_bar.pack(side="bottom", fill="x", before=self.scroll_holder)
        else:
            self.setup_bar.pack_forget()
        # A page opens at its top (the last one may have been scrolled down), and the
        # scrollbar fits it at once.
        changed = name != self.page
        self.page = name
        titles = {"setup": "setup", "progress": "getting ready", "error": "could not start"}
        if name in titles:
            self.root.title(f"Starlight · {tr(titles[name])}")
        if changed:
            self.scroller.yview_moveto(0)
            self.root.after_idle(self._fit_body)

    def _new_page(self, name: str) -> ttk.Frame:
        frame = ttk.Frame(self.body)
        frame.columnconfigure(0, weight=1)
        self.pages[name] = frame
        return frame

    def _label(
        self,
        parent: tk.Misc,
        text: str = "",
        style: str = "TLabel",
        wrap: bool = False,
        **kwargs: Any,
    ) -> ttk.Label:
        """A label; ``wrap``: its lines wrap at the width of the page (see _fit_body)."""
        if wrap:
            kwargs.setdefault("wraplength", self._page_width())
            kwargs.setdefault("justify", "left")
        label = ttk.Label(parent, text=tr(text), style=style, **kwargs)
        if wrap:
            self.wrapping.append(label)
        return label

    def _page_width(self) -> int:
        """How wide the text of a page may be: the window less the page's margins."""
        width = self.scroller.winfo_width() if self.scroller.winfo_ismapped() else 0
        return max(width or self.root.winfo_width(), self.px(400)) - 2 * self.px(28) - 4

    def _button(
        self, parent: tk.Misc, text: str, command: Any, style: str = "TButton"
    ) -> ttk.Button:
        return ttk.Button(parent, text=tr(text), command=command, style=style, cursor="hand2")

    # -- setup page -----------------------------------------------------------------------------

    # The download size of each preset in GB, until the real one is known (self.sizes).
    # Not the video memory a model needs: that is launcher MODEL_INFO.
    APPROX_GB: ClassVar[dict[str, float]] = {
        "qwen14b-uncensored": 9.0,
        "qwen14b": 9.0,
        "qwen3-14b": 9.0,
        "ministral14b": 8.2,
        "mistral-nemo": 7.5,
        "gemma4-12b": 7.5,
        "qwen14b-coder": 9.0,
        "qwen3.5-9b": 5.7,
        "qwen7b": 4.7,
        "qwen3-8b": 5.0,
        "gemma4-e4b": 5.0,
        "qwen3-14b-abliterated": 9.0,
        "josiefied-qwen3-14b": 9.0,
        "hermes4-14b": 9.0,
        "qwen3-8b-abliterated": 5.0,
        "dolphin3-8b": 4.9,
        "gpt-oss-20b": 12.1,
        "mistral-small-24b": 14.3,
        "gemma4-26b": 16.8,
        "qwen3.8-27b": 16.5,
        "qwen3-30b-a3b": 18.6,
        "qwen3-coder-30b": 18.6,
        "granite4-small": 19.5,
        "gemma4-31b": 18.3,
        "qwen3.5-35b-a3b": 21.5,
        "qwen3-32b": 19.8,
        "gpt-oss-20b-abliterated": 12.1,
        "dolphin-mistral-24b": 14.3,
        "qwen3.8-27b-abliterated": 17.0,
        "qwen3-30b-a3b-abliterated": 18.6,
    }

    def _build_setup(self) -> None:
        page = self._new_page("setup")
        self.model_touched = False
        self.setup_title = self._label(page, "", "Big.TLabel")
        self.setup_title.grid(row=0, column=0, sticky="w")
        self.setup_sub = self._label(page, "", "Dim.TLabel")
        self.setup_sub.grid(row=1, column=0, sticky="w", pady=(self.px(2), self.px(14)))

        models = ttk.Frame(page)
        models.grid(row=2, column=0, sticky="ew")
        models.columnconfigure(0, weight=1)
        head = ttk.Frame(models)
        head.grid(row=0, column=0, sticky="ew", pady=(0, self.px(6)))
        head.columnconfigure(0, weight=1)
        self._label(head, "MODEL", "Section.TLabel").grid(row=0, column=0, sticky="w")
        # Show only the models with one tag (the list is long); "All" shows them all again.
        chips = ttk.Frame(head)
        chips.grid(row=0, column=1, sticky="e")
        self.tag_filter = ""
        self.chips: dict[str, tk.Label] = {}
        for tag in ("", *self.L.TAG_INFO):
            chip = tk.Label(chips, text=tag.capitalize() if tag else "All", font=self.f_small,
                            padx=self.px(8), pady=self.px(1), cursor="hand2")  # fmt: skip
            chip.pack(side="left", padx=(self.px(4), 0))
            chip.bind("<Button-1>", partial(self._filter_by, tag))
            if tag:
                Tooltip(self, chip, f'Show the models tagged "{tag}": {self.L.TAG_INFO[tag]}')
            self.chips[tag] = chip
        self._paint_chips()
        self.model_var = tk.StringVar(value="")
        keys = [*self.L.MODEL_PRESETS, "custom", LMSTUDIO]  # custom: shown only when in use
        # Other people's changes of a model, and models from elsewhere, each under a heading.
        headings = {
            self.L.LARGE[0]: "FOR GRAPHICS CARDS WITH 24 TO 32 GB",
            self.L.MODIFIED[0]: "CHANGED BY OTHERS: ABLITERATED AND FINE-TUNED",
            keys[len(self.L.MODEL_PRESETS)]: "YOUR OWN MODEL",
        }
        # (its heading, its cards): a heading shows only while one of its cards does
        self.sections: list[tuple[ttk.Label | None, list[str]]] = [(None, [])]
        row = 1
        for key in keys:
            if key in headings:
                heading = self._label(models, headings[key], "Section.TLabel")
                heading.grid(row=row, column=0, sticky="w", pady=(self.px(10), self.px(4)))
                self.sections.append((heading, []))
                row += 1
            self._make_card(models, key, row)
            self.sections[-1][1].append(key)
            row += 1
        self.model_var.set(self._current_model_key())
        self.test_model_note = self._label(
            models,
            "Starlight's built-in test model is used (started with --no-model). It only "
            "repeats what you say, so nothing is downloaded.",
            "Dim.TLabel",
            wrap=True,
        )
        self.gpu_label = self._label(
            models, "Graphics card: looking ...", "Small.TLabel", wrap=True
        )
        self.gpu_label.grid(row=row + 1, column=0, sticky="w", pady=(self.px(6), 0))
        # Models too big for the graphics card found are folded away (they would run partly on
        # the processor, many times slower); this shows them again.
        self.show_big_var = tk.BooleanVar(value=False)
        self.show_big_check = CheckRow(self, models, "", self.show_big_var, self._apply_visibility)
        self.show_big_check.grid(row=row + 2, column=0, sticky="w")
        self.show_big_check.grid_remove()
        # With LM Studio: which of its models Starlight loads (it has several, none loaded);
        # right below its card, the last one.
        self.lms_row = ttk.Frame(models)
        self.lms_row.grid(
            row=row, column=0, sticky="w", pady=(self.px(4), 0), padx=(self.px(14), 0)
        )
        self._label(self.lms_row, "Model in LM Studio:").pack(side="left")
        self.lms_model_var = tk.StringVar(value=self.settings.lmstudio_model or LMS_AUTO)
        self.lms_combo = ttk.Combobox(
            self.lms_row, textvariable=self.lms_model_var, state="readonly", width=48
        )
        self.lms_combo.pack(side="left", padx=(self.px(8), 0))
        self.lms_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_lms_choice())
        self.lms_row.grid_remove()

        options = ttk.Frame(page)
        options.grid(row=3, column=0, sticky="ew", pady=(self.px(16), 0))
        heading = self._label(options, "OPTIONS", "Section.TLabel")
        heading.grid(row=0, column=0, sticky="w", pady=(0, self.px(4)))
        self.gpu_var = tk.BooleanVar(value=self.settings.variant != "cpu")
        self.voice_var = tk.BooleanVar(value=bool(self.settings.voice))
        self.ocr_var = tk.BooleanVar(value=bool(self.settings.tesseract))
        self.viewer_var = tk.BooleanVar(value=bool(self.settings.viewer))
        self.think_var = tk.BooleanVar(value=bool(self.settings.think))
        rows = [
            (
                self.gpu_var,
                "Run the model on the graphics card (turn off only if there is none: much slower)",
            ),
            (self.voice_var, "Speech: talk to Starlight and hear its answers (adds about 0.6 GB)"),
        ]
        self.has_ocr_option = sys.platform == "win32" and not self._tesseract_installed()
        if self.has_ocr_option:
            rows.append(
                (
                    self.ocr_var,
                    "Read the screen: install Tesseract OCR "
                    "(Windows asks for administrator approval)",
                )
            )
        rows.append((self.viewer_var, "Statistics page with charts about what Starlight is doing"))
        rows.append(
            (
                self.think_var,
                "Let the model think before it answers (better at hard questions, slower)",
            )
        )
        for index, (variable, text) in enumerate(rows, start=1):
            check = CheckRow(self, options, text, variable, self._refresh_summary)
            check.grid(row=index, column=0, sticky="w")
            if variable is self.gpu_var:
                self.gpu_check = check  # off with LM Studio: it decides where the model runs
            if variable is self.think_var:
                self.think_check = check  # only for the models that think first
        self.autostart_var = tk.BooleanVar(value=False)
        self.app_var = tk.BooleanVar(value=bool(self.settings.app_window))
        next_row = len(rows) + 1
        if sys.platform == "win32":
            self.autostart_var.set(self.L.autostart_enabled())
            CheckRow(
                self, options, "Start Starlight when I log in to Windows", self.autostart_var,
                self._refresh_summary,
            ).grid(row=next_row, column=0, sticky="w")  # fmt: skip
            next_row += 1
            CheckRow(
                self, options, "Open the chat in Starlight's own window, not in the browser",
                self.app_var, self._refresh_summary,
            ).grid(row=next_row, column=0, sticky="w")  # fmt: skip
            next_row += 1
        # Two choices that are more than on or off: how the model words its answers, and how
        # much of the conversation it keeps in mind at once.
        picks = ttk.Frame(options)
        picks.grid(row=next_row, column=0, sticky="w", pady=(self.px(8), 0))
        self.style_var = tk.StringVar(value=STYLE_NAMES[self.settings.style])
        self.context_names = dict(CONTEXT_NAMES)
        if self.settings.context not in self.context_names:
            self.context_names[self.settings.context] = f"{self.settings.context} tokens"
        self.context_var = tk.StringVar(value=self.context_names[self.settings.context])
        for row_index, (label, choice_var, values, tip) in enumerate((
            ("Answers:", self.style_var, list(STYLE_NAMES.values()),
             "How freely the model chooses its words: precise for facts and tools, creative "
             "for stories and ideas."),
            ("Context:", self.context_var, list(self.context_names.values()),
             "How much of the conversation (and of long texts) the model keeps in mind at "
             "once. More needs more video memory; Auto works it out from the model file and "
             "your graphics card."),
        )):  # fmt: skip
            name = self._label(picks, label)
            name.grid(row=row_index, column=0, sticky="w", pady=self.px(2))
            combo = ttk.Combobox(picks, textvariable=choice_var, values=values,
                                 state="readonly", width=52)  # fmt: skip
            combo.grid(row=row_index, column=1, sticky="w", padx=(self.px(8), 0), pady=self.px(2))
            Tooltip(self, name, tip)
            Tooltip(self, combo, tip)
        hf = tk.Label(models, text="Find more models on Hugging Face ...", font=self.f_link,
                      bg=self.c.bg, fg=self.c.accent, cursor="hand2")  # fmt: skip
        hf.grid(row=row + 3, column=0, sticky="w", pady=(self.px(6), 0))
        hf.bind("<Button-1>", lambda _event: self.open_hf_search())
        Tooltip(self, hf, "Search Hugging Face for other GGUF models and use one of them")

        # At the foot of the window, always in view (the list of models above is long): what
        # will be downloaded, and the buttons. show() puts it there for this page only.
        self.setup_bar = ttk.Frame(self.root)
        tk.Frame(self.setup_bar, bg=self.c.border, height=1).pack(fill="x")
        buttons = ttk.Frame(
            self.setup_bar, padding=(self.px(28), self.px(10), self.px(28), self.px(12))
        )
        buttons.pack(fill="x")
        buttons.columnconfigure(0, weight=1)
        self.summary = self._label(buttons, "", "Dim.TLabel", wrap=True)
        self.summary.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, self.px(8)))
        note = self._label(
            buttons, "Your choices are saved in config/launcher.json.", "Small.TLabel"
        )
        note.grid(row=1, column=0, sticky="w")
        self.setup_cancel = self._button(buttons, "Cancel", self.on_setup_cancel)
        self.setup_cancel.grid(row=1, column=1, padx=(0, self.px(8)))
        self.setup_quit = self._button(buttons, "Quit", self.quit_app)
        self.setup_quit.grid(row=1, column=2, padx=(0, self.px(8)))
        self.setup_go = self._button(
            buttons, "Install and start", self.on_setup_go, "Accent.TButton"
        )
        self.setup_go.grid(row=1, column=3)

    # -- more models: Hugging Face ------------------------------------------------------------

    def open_hf_search(self) -> None:
        """A small window: search Hugging Face for GGUF models, pick a file, use it."""
        c = self.c
        window = tk.Toplevel(self.root)
        window.transient(self.root)  # in front of the launcher, not behind it
        window.title("Find a model on Hugging Face")
        window.configure(bg=c.bg)
        window.geometry(f"{self.px(760)}x{self.px(520)}")
        top = ttk.Frame(window, padding=self.px(12))
        top.pack(fill="x")
        query = tk.StringVar(value="Qwen3 GGUF")
        entry = ttk.Entry(top, textvariable=query, width=40)
        entry.pack(side="left", fill="x", expand=True)
        state = self._label(top, "", "Small.TLabel")
        lists = ttk.Frame(window, padding=(self.px(12), 0))
        lists.pack(fill="both", expand=True)
        lists.columnconfigure((0, 1), weight=1)
        lists.rowconfigure(1, weight=1)
        self._label(lists, "Models (most downloaded first)", "Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self._label(lists, "Files (pick one size)", "Section.TLabel").grid(
            row=0, column=1, sticky="w", padx=(self.px(10), 0)
        )
        repos = tk.Listbox(lists, bg=c.panel, fg=c.text, font=self.f, activestyle="none",
                           exportselection=False, relief="flat")  # fmt: skip
        repos.grid(row=1, column=0, sticky="nsew")
        files = tk.Listbox(lists, bg=c.panel, fg=c.text, font=self.f, activestyle="none",
                           exportselection=False, relief="flat")  # fmt: skip
        files.grid(row=1, column=1, sticky="nsew", padx=(self.px(10), 0))
        found: dict[str, list[Any]] = {"repos": [], "files": []}
        vram = self.gpu.vram if getattr(self, "gpu", None) is not None and self.gpu.vram else 0

        def run(job: Any, done: Any) -> None:
            def work() -> None:
                try:
                    result = job()
                except self.L.LauncherError as exc:
                    message = str(exc)
                    self.root.after(0, lambda: state.configure(text=message))
                    return
                self.root.after(0, lambda: done(result))

            state.configure(text="Asking Hugging Face ...")
            threading.Thread(target=work, name="hf", daemon=True).start()

        def show_repos(result: list[dict[str, Any]]) -> None:
            found["repos"] = result
            repos.delete(0, "end")
            files.delete(0, "end")
            for item in result:
                repos.insert("end", f"{item['repo']}   ({item['downloads']:,} downloads)")
            state.configure(text=f"{len(result)} found" if result else "Nothing found")

        def show_files(result: list[dict[str, Any]]) -> None:
            found["files"] = result
            files.delete(0, "end")
            for item in result:
                fits = " - fits your card" if vram and item["size"] * 1.15 < vram else ""
                files.insert("end", f"{item['file'].rsplit('/', 1)[-1]}   "
                                    f"{self.L.format_size(item['size'])}{fits}")  # fmt: skip
            state.configure(text=f"{len(result)} files" if result else "No single-file GGUF here")

        def search(_event: object = None) -> None:
            run(lambda: self.L.search_hf(query.get()), show_repos)

        def pick_repo(_event: object = None) -> None:
            chosen = selected(repos)
            if chosen is not None:
                repo = found["repos"][chosen]["repo"]
                run(lambda: self.L.hf_files(repo), show_files)

        def use() -> None:
            chosen = selected(files)
            if chosen is None:
                state.configure(text="Pick a file first")
                return
            self.use_model_url(found["files"][chosen]["url"], found["files"][chosen]["size"])
            window.destroy()

        entry.bind("<Return>", search)
        repos.bind("<<ListboxSelect>>", pick_repo)
        files.bind("<Double-Button-1>", lambda _event: use())
        self._button(top, "Search", search).pack(side="left", padx=(self.px(8), 0))
        state.pack(side="left", padx=(self.px(10), 0))
        bottom = ttk.Frame(window, padding=self.px(12))
        bottom.pack(fill="x")
        self._label(
            bottom, "Models not from the list are not tested with Starlight: try the tool-use "
            "exam once it runs.", "Small.TLabel", wrap=True,
        ).pack(side="left", fill="x", expand=True)  # fmt: skip
        self._button(bottom, "Use this model", use, "Accent.TButton").pack(side="right")
        entry.focus_set()

    def use_model_url(self, url: str, size: int = 0) -> None:
        """Make a model from elsewhere the chosen one (the "your own model" card)."""
        self.settings.model_url = url
        if size:
            self.sizes[url] = size
        card = self.cards["custom"]
        card["name"].configure(text=f"Your own model: {shorten(self.L._file_name(url))}")
        card["desc"].configure(text="From Hugging Face. Downloaded when you start.")
        self.select_model("custom", touched=True)
        self._apply_visibility()
        self._refresh_cards()

    def _tesseract_installed(self) -> bool:
        return bool(self.L.tesseract_found())

    def _current_model_key(self) -> str:
        if self.settings.model_server == "lmstudio":
            return LMSTUDIO
        for key, url in self.L.MODEL_PRESETS.items():
            if url == self.settings.model_url:
                return str(key)
        return "custom"

    def _model_url_of(self, key: str) -> str:
        return str(self.settings.model_url if key == "custom" else self.L.MODEL_PRESETS[key])

    def _model_file_of(self, key: str) -> Path:
        return Path(self.settings.models / self.L._file_name(self._model_url_of(key)))

    def _make_card(self, parent: ttk.Frame, key: str, row: int) -> None:
        c = self.c
        if key == "custom":
            title = f"Your own model: {shorten(self.L._file_name(self.settings.model_url))}"
            text = "Set as model_url in config/launcher.json."
        elif key == LMSTUDIO:
            title = "LM Studio"
            text = "The model you load in LM Studio, with its local server on. Nothing to download."
        else:
            title, text, _ = self.L.MODEL_INFO[key]
        frame = tk.Frame(
            parent, bg=c.panel, highlightthickness=2, highlightbackground=c.border, cursor="hand2"
        )
        frame.grid(row=row, column=0, sticky="ew", pady=self.px(3))
        frame.columnconfigure(1, weight=1)
        mark = tk.Canvas(frame, width=self.px(22), height=self.px(22), bg=c.panel,
                         highlightthickness=0)  # fmt: skip
        self._draw_mark(mark, "empty", c.dim)
        mark.grid(row=0, column=0, rowspan=2, padx=(self.px(12), self.px(8)), pady=self.px(8))
        head = tk.Frame(frame, bg=c.panel)
        head.grid(row=0, column=1, sticky="w", pady=(self.px(8), 0))
        name = tk.Label(head, text=title, font=self.f_bold, bg=c.panel, fg=c.text, anchor="w",
                        justify="left")  # fmt: skip
        name.pack(side="left")
        tags = tk.Frame(head, bg=c.panel)
        tags.pack(side="left", padx=(self.px(8), 0))
        tag_labels = [
            tk.Label(tags, text=tag, font=self.f_small, bg=c.panel2, fg=c.dim, padx=self.px(5))
            for tag in self.L.MODEL_TAGS.get(key, ())
        ]
        for tag_label in tag_labels:
            tag_label.pack(side="left", padx=(0, self.px(4)))
            Tooltip(self, tag_label, self.L.TAG_INFO.get(tag_label.cget("text"), ""))
        desc = tk.Label(frame, text=text, font=self.f_small, bg=c.panel, fg=c.dim, anchor="w",
                        justify="left")  # fmt: skip
        desc.grid(row=1, column=1, sticky="w", pady=(0, self.px(8)))
        side = tk.Frame(frame, bg=c.panel)
        side.grid(row=0, column=2, rowspan=2, sticky="e", padx=self.px(12))
        meta = tk.Label(side, text="", font=self.f_small, bg=c.panel, fg=c.dim, anchor="e")
        meta.pack(anchor="e")
        badges = tk.Frame(side, bg=c.panel)
        badges.pack(anchor="e", pady=(self.px(2), 0))

        def choose(_event: Any, chosen: str = key) -> None:
            self.select_model(chosen, touched=True)

        for widget in (frame, mark, head, name, tags, *tag_labels, desc, side, meta, badges):
            widget.bind("<Button-1>", choose)
        card = {"frame": frame, "mark": mark, "meta": meta, "badges": badges, "name": name,
                "desc": desc, "side": side, "head": head, "tags": tags}  # fmt: skip
        self.cards[key] = card
        frame.bind("<Configure>", self._layout_cards_soon, add="+")
        for widget in (frame, mark, head, name, tags, *tag_labels, desc, side, meta, badges):
            widget.bind("<Enter>", partial(self._hover_card, key), add="+")
            widget.bind("<Leave>", partial(self._hover_card, key), add="+")

    def _hover_card(self, key: str, _event: object = None) -> None:
        """The card under the pointer gets a lighter border (moving between its parts is
        a Leave and an Enter: what counts is where the pointer is)."""
        frame = self.cards[key]["frame"]
        x, y = self.root.winfo_pointerxy()
        widget = self.root.winfo_containing(x, y)
        while widget is not None and widget is not frame:
            widget = widget.master if isinstance(widget.master, tk.Misc) else None
        self._paint_card(key, hover=widget is frame)

    def _paint_card(self, key: str, hover: bool = False) -> None:
        c = self.c
        chosen = key == self.model_var.get()
        border = c.accent if chosen else c.dim if hover else c.border
        self.cards[key]["frame"].configure(highlightbackground=border)

    def _filter_by(self, tag: str, _event: object = None) -> None:
        self.tag_filter = "" if tag == self.tag_filter else tag  # a second click: all again
        self._paint_chips()
        self._apply_visibility()

    def _paint_chips(self) -> None:
        c = self.c
        for tag, chip in self.chips.items():
            on = tag == self.tag_filter
            chip.configure(bg=c.accent if on else c.panel2, fg=c.on_accent if on else c.dim)

    def _hidden(self, key: str) -> bool:
        """Folded away: without the tag chosen above the list, or too big for the graphics
        card (unless asked for). Never the chosen model, nor LM Studio or your own."""
        if key in (self.model_var.get(), self._current_model_key()):
            return False
        if key == "custom":  # your own model's card: while it is the one chosen
            return True
        if key == LMSTUDIO:
            return False
        if self.tag_filter and self.tag_filter not in self.L.MODEL_TAGS.get(key, ()):
            return True
        return not self.show_big_var.get() and self._too_big(self.L.MODEL_INFO[key][2])

    def _apply_visibility(self) -> None:
        """Which cards and headings show: none with the test model, the ones that fit the
        graphics card (or all, see show_big_var), and a heading only above shown cards."""
        use_model = self.settings.use_model
        for heading, keys in self.sections:
            shown = [k for k in keys if use_model and not self._hidden(k)]
            for key in keys:
                if key in shown:
                    self.cards[key]["frame"].grid()
                else:
                    self.cards[key]["frame"].grid_remove()
            if heading is not None:
                if shown:
                    heading.grid()
                else:
                    heading.grid_remove()
        big = [k for k in self.L.MODEL_PRESETS if self._too_big(self.L.MODEL_INFO[k][2])]
        if use_model and big:
            self.show_big_check.set_text(
                f"Also show the {len(big)} models too big for this graphics card"
            )
            self.show_big_check.grid()
        else:
            self.show_big_check.grid_remove()
        self._layout_cards_soon()

    def _shown_keys(self) -> list[str]:
        return [k for k, card in self.cards.items() if card["frame"].winfo_manager() == "grid"]

    def _move_selection(self, step: int) -> str | None:
        """Up and Down choose the model above or below (on the setup page)."""
        if self.page != "setup" or not self.settings.use_model:
            return None
        keys = self._shown_keys()
        if not keys:
            return None
        current = self.model_var.get()
        index = keys.index(current) + step if current in keys else 0
        key = keys[max(0, min(index, len(keys) - 1))]
        self.select_model(key, touched=True)
        self._scroll_into_view(self.cards[key]["frame"])
        return "break"

    def _scroll_into_view(self, widget: tk.Misc) -> None:
        """Scroll the page so that this widget shows (when the page is taller than the window)."""
        if not self._scrollable:
            return
        self.root.update_idletasks()
        total = max(self.body.winfo_height(), 1)
        top = widget.winfo_rooty() - self.body.winfo_rooty()
        bottom = top + widget.winfo_height()
        first, last = self.scroller.yview()
        margin = self.px(12)
        if top < first * total:
            self.scroller.yview_moveto(max(top - margin, 0) / total)
        elif bottom > last * total:
            view = (last - first) * total
            self.scroller.yview_moveto(max(bottom + margin - view, 0) / total)

    def _layout_cards_soon(self, _event: object = None) -> None:
        """Lay the cards out again once the window is idle (after the badges were drawn);
        many requests at once are one layout."""
        if not self._cards_pending:
            self._cards_pending = True
            self.root.after_idle(self._layout_cards)

    def _layout_cards(self) -> None:
        """Every card looks the same, whatever it shows: the right-hand column (video memory,
        badges) is as wide on all of them, titles and descriptions wrap where it begins
        rather than being cut off on a narrow window, and all cards are as tall as the
        tallest, so badges coming and going never move the list."""
        self._cards_pending = False
        cards = list(self.cards.values())
        if not cards:
            return
        self.root.update_idletasks()  # the sizes of new badges and texts
        side = max(card["side"].winfo_reqwidth() for card in cards)
        shown = [card["frame"] for card in cards if card["frame"].winfo_ismapped()]
        width = shown[0].winfo_width() if shown else self._page_width()
        # the round mark (22 + 12 + 8), the right column with its margins, the border, and
        # the labels' own padding
        wrap = max(width - self.px(42) - side - self.px(24) - 4 - self.px(8), self.px(160))
        for card in cards:
            card["frame"].columnconfigure(2, minsize=side + self.px(24))
            beside_tags = max(wrap - card["tags"].winfo_reqwidth() - self.px(8), self.px(120))
            for label, limit in ((card["name"], beside_tags), (card["desc"], wrap)):
                if int(str(label.cget("wraplength"))) != limit:
                    label.configure(wraplength=limit)
        self.root.update_idletasks()
        title = max(card["head"].winfo_reqheight() for card in cards)
        text = max(card["desc"].winfo_reqheight() for card in cards)
        for card in cards:
            card["frame"].rowconfigure(0, minsize=title + self.px(8))
            card["frame"].rowconfigure(1, minsize=text + self.px(8))
        self.root.after_idle(self._fit_body)  # the page got shorter or taller: scroll range

    def select_model(self, key: str, touched: bool = False) -> None:
        if touched:
            self.model_touched = True
        self.model_var.set(key)
        self.gpu_check.state(["disabled"] if key == LMSTUDIO else ["!disabled"])
        if hasattr(self, "lms_row"):
            self._refresh_lms_choice()
        if "thinks first" in self.L.MODEL_TAGS.get(key, ()):
            self.think_check.grid()
        else:
            self.think_check.grid_remove()  # (LM Studio: set it in LM Studio)
        for card_key, card in self.cards.items():
            chosen = card_key == key
            self._paint_card(card_key)
            self._draw_mark(
                card["mark"],
                "radio" if chosen else "empty",
                self.c.accent if chosen else self.c.dim,
            )
        if self.cards:
            recommended, _ = self.L.recommend_model(self.gpu)
            self._refresh_cards(recommended if self.gpu is not None else "")
        self._refresh_summary()

    def _refresh_cards(self, recommended: str = "") -> None:
        for key, card in self.cards.items():
            if key == LMSTUDIO:
                self._refresh_lmstudio_card(card)
                continue
            have = self._model_file_of(key).is_file()
            size = self.sizes.get(self._model_url_of(key))
            parts = []
            if key != "custom":
                parts.append(f"about {self.L.MODEL_INFO[key][2]:.0f} GB video memory")
            if not have and size:
                parts.append(f"{self.L.format_size(size)} download")
            card["meta"].configure(text=" - ".join(parts))
            for child in card["badges"].winfo_children():
                child.destroy()
            if not have and self._model_url_of(key) in self.missing:
                self._pill(card["badges"], "Download link not found", self.c.bad, "#ffffff")
            elif key == recommended:
                pill_text = self.c.on_accent
                self._pill(card["badges"], "Recommended for your PC", self.c.accent, pill_text)
            elif key != "custom" and self._too_big(self.L.MODEL_INFO[key][2]):
                warn_text = "#241a00" if self.c is DARK else "#ffffff"
                self._pill(card["badges"], "Too big for your graphics card", self.c.warn, warn_text)
            if have:
                dark_text = "#06240f" if self.c is DARK else "#ffffff"
                self._pill(card["badges"], "Downloaded", self.c.ok, dark_text)
                if key not in ("custom", self._current_model_key(), self.model_var.get()):
                    remove = tk.Label(card["badges"], text="Remove", font=self.f_link,
                                      bg=self.c.panel, fg=self.c.dim, cursor="hand2")  # fmt: skip
                    remove.pack(side="left", padx=(self.px(8), 0))
                    remove.bind("<Button-1>", partial(self._remove_clicked, key))
        self._layout_cards_soon()

    def _remove_clicked(self, key: str, _event: object) -> None:
        self.remove_model(key)

    def remove_model(self, key: str) -> None:
        """Delete a downloaded model that is not in use, to free its space (it can be
        downloaded again by choosing it)."""
        if key in (self.model_var.get(), self._current_model_key()):
            return  # chosen or in use (the link is only offered for other models)
        path = self._model_file_of(key)
        try:
            size = path.stat().st_size
        except OSError:
            return
        title = self.L.MODEL_INFO[key][0]
        question = (
            f"Delete {title} ({self.L.format_size(size)}) from the models folder?\n\n"
            "It is downloaded again if you choose it later."
        )
        if not messagebox.askyesno("Starlight", question, default="no"):
            return
        try:
            path.unlink()
        except OSError as exc:
            messagebox.showinfo("Starlight", f"{path.name} could not be deleted: {exc}")
            return
        recommended, _ = self.L.recommend_model(self.gpu)
        self._refresh_cards(recommended if self.gpu is not None else "")
        self._refresh_summary()

    def _too_big(self, memory: float) -> bool:
        """Whether a model needing this much video memory (GB) does not fit the card found
        (it would then run partly on the processor, many times slower)."""
        vram = getattr(self.gpu, "vram", None)
        return bool(vram) and memory > vram / self.L.GB + 0.5

    def _lms_choice(self) -> str:
        """The LM Studio model chosen below the cards; "" for whichever is loaded."""
        chosen = self.lms_model_var.get()
        return "" if chosen == LMS_AUTO else chosen

    def _refresh_lms_choice(self) -> None:
        """The LM Studio model list: shown with LM Studio chosen, filled from what it has."""
        state = self.lmstudio
        names = list(state.available) if state is not None else []
        chosen = self._lms_choice()
        if chosen and chosen not in names:
            names.insert(0, chosen)  # set before, or LM Studio is not running now
        self.lms_combo.configure(values=[LMS_AUTO, *names])
        if self.model_var.get() == LMSTUDIO and self.settings.use_model and names:
            self.lms_row.grid()
        else:
            self.lms_row.grid_remove()
        if LMSTUDIO in self.cards:
            self._refresh_lmstudio_card(self.cards[LMSTUDIO])

    def _refresh_lmstudio_card(self, card: dict[str, Any]) -> None:
        """What LM Studio said when the page opened: running, and with which model."""
        state = self.lmstudio
        for child in card["badges"].winfo_children():
            child.destroy()
        if state is None:
            text = "runs in LM Studio"
        elif state.locked:
            text = f"asks for an API token: set {self.L.LMSTUDIO_TOKEN_ENV} (see the README)"
        elif state.loaded:
            text = f"found: {shorten(state.loaded)}"
            pill_fg = "#06240f" if self.c is DARK else "#ffffff"
            self._pill(card["badges"], "Running", self.c.ok, pill_fg)
        elif state.answers and (self._lms_choice() or len(state.available) == 1):
            name = self._lms_choice() or state.available[0]
            text = f"found: {shorten(name)} is loaded when Starlight starts"
        elif state.answers and state.available:
            text = "found, but no model is loaded: pick one below"
        elif state.answers:
            text = "found, but it has no model yet: download one in LM Studio"
        else:
            text = "not running now (start its local server)"
        card["meta"].configure(text=text)
        self._layout_cards_soon()

    def _draw_mark(self, canvas: tk.Canvas, kind: str, colour: str) -> None:
        """A small round mark drawn on a canvas (fonts differ in which symbols they have):
        "empty" a ring, "radio" a ring with a dot, "dot" a filled dot, "done" a check mark."""
        canvas.delete("all")
        size = int(canvas.cget("width"))
        pad = size * 0.14
        if kind == "done":
            canvas.create_oval(pad, pad, size - pad, size - pad, fill=colour, outline="")
            points = [0.30, 0.52, 0.45, 0.67, 0.72, 0.36]
            canvas.create_line(*[value * size for value in points], fill=self.c.bg, width=2)
            return
        if kind == "dot":
            canvas.create_oval(pad, pad, size - pad, size - pad, fill=colour, outline="")
            return
        canvas.create_oval(pad, pad, size - pad, size - pad, outline=colour, width=2)
        if kind == "radio":
            inner = size * 0.34
            canvas.create_oval(inner, inner, size - inner, size - inner, fill=colour, outline="")

    def _pill(self, parent: tk.Misc, text: str, bg: str, fg: str) -> None:
        pill = tk.Label(parent, text=text, font=self.f_small, bg=bg, fg=fg, padx=self.px(8), pady=1)
        pill.pack(side="left", padx=(self.px(4), 0))

    def _start_background_lookups(self) -> None:
        """The graphics card and the sizes of the model downloads: found while the page is
        already showing."""

        def look() -> None:
            self.events.put(("lmstudio_found", self.L.lmstudio_state(self.settings.lmstudio_url)))
            self.events.put(("gpu", self.L.detect_gpu()))
            for key in self.L.MODEL_PRESETS:
                url = self.L.MODEL_PRESETS[key]
                if not self._model_file_of(key).is_file():
                    try:
                        self.events.put(("size", url, self.L.probe(url).size))
                    except self.L.MissingDownload:
                        self.events.put(("missing", url))
                    except (urllib.error.URLError, OSError, ValueError, self.L.LauncherError):
                        self.events.put(("size", url, None))

        threading.Thread(target=look, name="lookups", daemon=True).start()

    def on_lmstudio_found(self, state: Any) -> None:
        self.lmstudio = state
        self._refresh_lms_choice()
        recommended, _ = self.L.recommend_model(self.gpu)
        self._refresh_cards(recommended if self.gpu is not None else "")

    def on_gpu(self, gpu: Any) -> None:
        self.gpu = gpu
        recommended, why = self.L.recommend_model(gpu)
        if gpu is None:
            self.gpu_label.configure(text="Graphics card: not found. " + why)
        else:
            memory = f" ({gpu.vram / self.L.GB:.0f} GB video memory)" if gpu.vram else ""
            self.gpu_label.configure(text=f"Graphics card: {gpu.name}{memory}. {why}")
        self._refresh_cards(recommended if gpu is not None else "")
        self._apply_visibility()
        first_run = not self._installed()
        chosen_lmstudio = self.settings.model_server == "lmstudio"  # kept: no download needed
        untouched = not self.model_touched and not chosen_lmstudio
        if gpu is not None and first_run and untouched and recommended in self.cards:
            self.select_model(recommended)

    def on_size(self, url: str, size: int | None) -> None:
        self.sizes[url] = size
        recommended, _ = self.L.recommend_model(self.gpu)
        self._refresh_cards(recommended if self.gpu is not None else "")
        self._refresh_summary()

    def on_missing(self, url: str) -> None:
        """A model's download link does not work (the file was moved or taken down)."""
        self.missing.add(url)
        recommended, _ = self.L.recommend_model(self.gpu)
        self._refresh_cards(recommended if self.gpu is not None else "")
        self._refresh_summary()

    def _installed(self) -> bool:
        return bool((self.settings.tools / "uv" / self.L.exe("uv")).is_file())

    def _refresh_summary(self) -> None:
        gb = self.L.GB
        # What would be downloaded with the boxes as they are now: the same checks as the
        # launcher's own estimate in setup(), so a part already there is not counted.
        chosen = replace(
            self.settings,
            variant="vulkan" if self.gpu_var.get() else "cpu",
            voice=self.voice_var.get(),
        )
        need = 0.0
        gone = False  # the chosen model's download link does not work
        if not self._installed():
            need += self.L.FIRST_RUN_SIZE
        key = self.model_var.get()
        if self.settings.use_model and key != LMSTUDIO:
            if not self.L.llama_binary(chosen).is_file():
                need += self.L.LLAMA_SIZE
            if key and not self._model_file_of(key).is_file():
                known = self.sizes.get(self._model_url_of(key))
                need += known if known else self.APPROX_GB.get(key, 9.0) * gb
                gone = self._model_url_of(key) in self.missing
        if self.L.voice_will_download(chosen):
            need += self.L.VOICE_SIZE
        try:
            free = shutil.disk_usage(self.settings.root).free
        except OSError:
            free = 10**15  # unknown: do not block the button
        enough = free >= need + self.L.SPARE_SPACE
        if need <= 0:
            text = "Nothing to download."
        else:
            text = (
                f"Needs about {need / gb:.1f} GB of free space; "
                f"this drive has {free / gb:.0f} GB free."
            )
        if not enough:
            text += " That is not enough: free some space first."
        if gone:
            text = "This model's download link does not work right now: pick another model."
        ok = enough and not gone
        self.summary.configure(text=text, foreground=self.c.dim if ok else self.c.bad)
        self.setup_go.state(["!disabled"] if ok else ["disabled"])

    def open_setup(self, settings_mode: bool) -> None:
        """Show the setup page: as the first-run welcome, or as Settings while running."""
        self.in_settings = settings_mode
        if settings_mode:
            self.setup_title.configure(text="Settings")
            self.setup_sub.configure(text="Changes apply after Starlight restarts.")
            self.setup_go.configure(text="Save and restart")
            self.setup_cancel.grid()
            self.setup_quit.grid_remove()
        else:
            first = not self._installed()
            self.setup_title.configure(text="Welcome to Starlight" if first else "Ready to start")
            self.setup_sub.configure(
                text="Your own assistant. It runs on this PC: nothing you say leaves it."
                if first
                else "Check your choices, then start."
            )
            self.setup_go.configure(text="Install and start" if first else "Start")
            self.setup_cancel.grid_remove()
            self.setup_quit.grid()
        use_model = self.settings.use_model
        self._apply_visibility()
        if use_model:
            self.test_model_note.grid_remove()
            self.gpu_label.grid()
        else:
            self.test_model_note.grid(row=1, column=0, sticky="w")
            self.gpu_label.grid_remove()
        self.select_model(self.model_var.get() or self._current_model_key())
        self._refresh_cards()
        self.show("setup")

    def on_setup_cancel(self) -> None:
        self.show("running")

    def on_setup_go(self) -> None:
        L, settings = self.L, self.settings
        key = self.model_var.get()
        choices: dict[str, Any] = {
            "variant": "vulkan" if self.gpu_var.get() else "cpu",
            "voice": self.voice_var.get(),
            "viewer": self.viewer_var.get(),
            "think": self.think_var.get(),
        }
        style = next(k for k, v in STYLE_NAMES.items() if v == self.style_var.get())
        context = next(k for k, v in self.context_names.items() if v == self.context_var.get())
        choices.update(style=style, context=context)
        settings.style, settings.context = style, context
        drop: tuple[str, ...] = ()
        keep_server = settings.model_server in ("ollama", "remote") and not self.model_touched
        if settings.use_model and not keep_server:  # (Ollama or another PC: set in the file)
            settings.model_server = "lmstudio" if key == LMSTUDIO else "llamacpp"
            choices["model_server"] = settings.model_server
        if settings.use_model and key == LMSTUDIO:
            settings.lmstudio_model = self._lms_choice()
            choices["lmstudio_model"] = settings.lmstudio_model
        if settings.use_model and key not in ("custom", LMSTUDIO):
            settings.model_url = L.MODEL_PRESETS[key]
            choices["model"] = key
            drop = ("model_url",)  # a saved model_url would win over "model" (load_settings)
        elif settings.use_model and key == "custom":
            choices["model_url"] = settings.model_url
            drop = ("model",)
        if sys.platform == "win32" and self.autostart_var.get() != L.autostart_enabled():
            try:
                L.set_autostart(self.autostart_var.get(), L.autostart_command(settings.root))
            except (OSError, L.LauncherError) as exc:
                messagebox.showwarning(
                    "Starlight", f"Could not change the start with Windows: {exc}"
                )
        settings.variant = choices["variant"]
        settings.voice = choices["voice"]
        settings.viewer = choices["viewer"]
        settings.think = choices["think"]
        if self.has_ocr_option:
            settings.tesseract = self.ocr_var.get()
            choices["tesseract"] = settings.tesseract
        if sys.platform == "win32":
            settings.app_window = choices["app_window"] = self.app_var.get()
        with contextlib.suppress(OSError):
            L.save_choices(settings.root, choices, drop)
        if self.in_settings:
            self.show_progress("Restarting Starlight ...")
            threading.Thread(target=self._stop_then_begin, name="restart", daemon=True).start()
        else:
            self.begin()

    def _stop_then_begin(self) -> None:
        self._stop_services()
        self.events.put(("begin",))

    # -- progress page --------------------------------------------------------------------------

    def _build_progress(self) -> None:
        c = self.c
        page = self._new_page("progress")
        page.rowconfigure(4, weight=1)
        self.p_title = self._label(page, "", "Big.TLabel", wrap=True)
        self.p_title.grid(row=0, column=0, sticky="w")
        self.p_sub = self._label(page, "", "Dim.TLabel")
        self.p_sub.grid(row=1, column=0, sticky="w", pady=(self.px(2), self.px(10)))
        self.bar = ttk.Progressbar(page, mode="indeterminate", maximum=1000)
        self.bar.grid(row=2, column=0, sticky="ew")
        self.steps_frame = ttk.Frame(page)
        self.steps_frame.grid(row=3, column=0, sticky="ew", pady=(self.px(18), 0))
        self.step_rows: list[tuple[tk.Canvas, tk.Label]] = []

        details = ttk.Frame(page)
        details.grid(row=4, column=0, sticky="nsew", pady=(self.px(14), 0))
        details.columnconfigure(0, weight=1)
        details.rowconfigure(1, weight=1)
        self.details_open = tk.BooleanVar(value=False)
        self.details_toggle = tk.Label(details, text="Show details", font=self.f_small,
                                       bg=c.bg, fg=c.accent, cursor="hand2")  # fmt: skip
        self.details_toggle.grid(row=0, column=0, sticky="w")
        self.details_toggle.bind("<Button-1>", lambda _event: self._flip_details())
        self.log_frame = ttk.Frame(details)
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            self.log_frame, height=9, wrap="word", bg=c.panel, fg=c.dim, font=self.f_mono,
            relief="flat", highlightthickness=1, highlightbackground=c.border, state="disabled",
            padx=self.px(8), pady=self.px(6),
        )  # fmt: skip
        scroll = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        buttons = ttk.Frame(page)
        buttons.grid(row=5, column=0, sticky="ew", pady=(self.px(10), 0))
        buttons.columnconfigure(0, weight=1)
        note = "Finished downloads are kept if you stop."
        self._label(buttons, note, "Small.TLabel").grid(row=0, column=0, sticky="w")
        self.pause_button = self._button(buttons, "Pause download", self.toggle_download_pause)
        self.pause_button.grid(row=0, column=1, padx=(0, self.px(8)))
        self.pause_button.grid_remove()  # shown while something downloads
        self._button(buttons, "Quit", self.quit_app).grid(row=0, column=2)

    def toggle_download_pause(self) -> None:
        """Pause the download (it stops at the next megabyte) or go on with it."""
        paused = self.L.DOWNLOADS_PAUSED
        if paused.is_set():
            paused.clear()
            self.pause_button.configure(text="Pause download")
        else:
            paused.set()
            self.pause_button.configure(text="Resume download")

    def _flip_details(self) -> None:
        self.details_open.set(not self.details_open.get())
        self._toggle_details()

    def _toggle_details(self) -> None:
        self.details_toggle.configure(
            text="Hide details" if self.details_open.get() else "Show details"
        )
        if self.details_open.get():
            self.log_frame.grid(row=1, column=0, sticky="nsew", pady=(self.px(6), 0))
        else:
            self.log_frame.grid_remove()

    def show_progress(self, title: str) -> None:
        for glyph, text in self.step_rows:
            glyph.destroy()
            text.destroy()
        self.step_rows.clear()
        self.p_title.configure(text=title)
        self.p_sub.configure(text="")
        self.bar.configure(mode="indeterminate")
        self.bar.start(14)
        self.show("progress")

    def _step_row(self, text: str) -> None:
        c = self.c
        row = len(self.step_rows)
        glyph = tk.Canvas(self.steps_frame, width=self.px(18), height=self.px(18), bg=c.bg,
                          highlightthickness=0)  # fmt: skip
        self._draw_mark(glyph, "dot", c.accent)
        label = tk.Label(self.steps_frame, text=text, fg=c.text, bg=c.bg, font=self.f, anchor="w")
        glyph.grid(row=row, column=0, sticky="w", padx=(0, self.px(8)))
        label.grid(row=row, column=1, sticky="w")
        self.step_rows.append((glyph, label))

    def on_step(self, text: str) -> None:
        if not self.L.DOWNLOADS_PAUSED.is_set():
            self.pause_button.grid_remove()  # a new step: no download in progress yet
        if self.step_rows:
            glyph, label = self.step_rows[-1]
            self._draw_mark(glyph, "done", self.c.ok)
            label.configure(fg=self.c.dim)
        self._step_row(text)
        self.p_sub.configure(text=text)  # (the title stays: what is being done overall)
        self.bar.configure(mode="indeterminate")
        self.bar.start(14)

    def on_progress(self, done: int, total: int | None, started: float) -> None:
        fraction, text = self.L.transfer_text(done, total, started)
        self.p_sub.configure(text=text)
        self.pause_button.grid()  # (only downloads report progress)
        if fraction < 0:
            return
        self.bar.stop()
        self.bar.configure(mode="determinate", value=fraction * 1000)

    def on_waiting(self, text: str) -> None:
        self.p_sub.configure(text=text)

    MAX_LOG_LINES = 1500  # the progress page's output box keeps the newest lines

    def on_say(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])  # Tk index "line.column"
        if lines > self.MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{lines - self.MAX_LOG_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # -- starting -------------------------------------------------------------------------------

    def needs_setup_page(self) -> bool:
        if not self._installed():
            return True
        return bool(self.settings.runs_llama and not self.settings.model_file.is_file())

    def begin(self) -> None:
        """Install what is missing and start Starlight, in a worker thread."""
        if self.group is None:
            self.group = self.L.ProcessGroup()  # also ends everything if this window dies
        self.show_progress("Getting ready ...")
        threading.Thread(target=self._work, name="setup", daemon=True).start()

    def _work(self) -> None:
        L = self.L
        try:
            installed = L.setup(self.settings, self.ui)
            services = L.Services(self.settings, self.ui, installed, group=self.group)
            services.start()
            self.chat_window = L.ChatWindow(self.settings, installed.uv, self.group)
            self.services = services  # before "running": on_running polls it
            self.events.put(("running",))
            L.tell_whats_new(self.settings.root, self.ui)  # once, after an update
        except L.LauncherError as exc:
            self.events.put(("error", str(exc), ""))
        except (urllib.error.URLError, TimeoutError) as exc:
            message = (
                f"A network problem stopped the download ({exc}). Finished downloads are kept."
            )
            self.events.put(("error", message, ""))
        except Exception as exc:  # shown to the user, with the details to report
            self.events.put(
                ("error", f"Something unexpected went wrong: {exc}", traceback.format_exc())
            )

    def try_again(self) -> None:
        """The error page's button. After a problem with the settings (no Starlight folder, a
        broken launcher.json) they are read again first, and the setup page is rebuilt."""
        if self.fatal:
            try:
                settings = self.L.load_settings(self.args)
            except self.L.LauncherError as exc:
                self.fatal = str(exc)
                self.on_error(self.fatal)
                return
            self.fatal = ""
            settings.assume_yes = True  # (maybe_tesseract: the setup page offered OCR)
            self.settings = settings
            self.ui.log = settings.root / "logs" / "launcher.log"
            self.pages["setup"].destroy()
            self._build_setup()
            if self.needs_setup_page():
                self.open_setup(settings_mode=False)
                self._start_background_lookups()
                return
        self.begin()

    def on_begin(self) -> None:
        self.begin()

    def on_running(self) -> None:
        self.show("running")
        self.polling = True
        if self._poll_job is not None:  # after a restart, the earlier timer is still running
            self.root.after_cancel(self._poll_job)
        self._poll_tick()

    def _poll_tick(self) -> None:
        self._poll_job = None
        if self.closing or not self.polling:
            return
        if self.services is not None and not self._poll_busy:
            self._poll_busy = True
            threading.Thread(target=self._poll_once, name="poll", daemon=True).start()
        self._poll_job = self.root.after(1500, self._poll_tick)

    def _poll_once(self) -> None:
        try:
            status = self.services.poll()
        except Exception:  # a failed look is not worth a message; the next one tries again
            status = None
        self._poll_busy = False
        if status is not None:
            self.events.put(("status", status))

    # -- running page ---------------------------------------------------------------------------

    def _build_running(self) -> None:
        c = self.c
        page = self._new_page("running")
        self.r_title = self._label(page, "", "Big.TLabel")
        self.r_title.grid(row=0, column=0, sticky="w")
        self.r_sub = self._label(page, "", "Dim.TLabel", wrap=True)
        self.r_sub.grid(row=1, column=0, sticky="w", pady=(self.px(2), self.px(14)))

        card = tk.Frame(page, bg=c.panel, highlightthickness=1, highlightbackground=c.border)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(2, weight=1)
        for index, name in enumerate(("Model", "Chat", "Statistics", "Kill switch")):
            pad = (
                self.px(12) if index == 0 else self.px(4),
                self.px(12) if index == 3 else self.px(4),
            )
            title = tk.Label(
                card, text=tr(name), font=self.f_bold, bg=c.panel, fg=c.text, anchor="w", width=11
            )
            title.grid(row=index, column=0, sticky="w", padx=(self.px(16), 0), pady=pad)
            dot = tk.Canvas(
                card, width=self.px(12), height=self.px(12), bg=c.panel, highlightthickness=0
            )
            oval = dot.create_oval(1, 1, self.px(11), self.px(11), fill=c.dim, outline="")
            dot.grid(row=index, column=1, padx=self.px(8), pady=pad)
            text = tk.Label(card, text="", font=self.f, bg=c.panel, fg=c.text, anchor="w",
                            justify="left")  # fmt: skip
            text.grid(row=index, column=2, sticky="w", pady=pad)
            self.status_rows[name] = (dot, oval, text)
        self.status_title_width = title.winfo_reqwidth()

        # The main actions, large: the chat, the statistics, and the kill switch (always one
        # click). The tools below are quieter, so the eye finds the main ones first.
        gap = self.px(4)
        main = ttk.Frame(page)
        main.grid(row=3, column=0, sticky="ew", pady=(self.px(18), 0))
        tools = ttk.Frame(page)
        tools.grid(row=5, column=0, sticky="ew")
        for frame in (main, tools):
            for column in range(3):
                frame.columnconfigure(column, weight=1, uniform="b")
        self.btn_chat = self._button(main, "Open chat", self.open_chat, "Big.Accent.TButton")
        self.btn_stats = self._button(main, "Statistics", self.open_stats, "Big.TButton")
        self.btn_kill = self._button(
            main, "Stop everything", self.toggle_kill, "Big.Danger.TButton"
        )
        self._label(page, "TOOLS", "Section.TLabel").grid(
            row=4, column=0, sticky="w", padx=gap, pady=(self.px(16), self.px(2))
        )
        tool = "Tool.TButton"
        self.btn_restart = self._button(tools, "Restart model server", self.restart_model, tool)
        self.btn_check = self._button(tools, "Check everything", self.run_check, tool)
        self.btn_logs = self._button(tools, "Open logs folder", self.open_logs, tool)
        self.btn_speed = self._button(tools, "Speed test", self.run_speed_test, tool)
        Tooltip(self, self.btn_speed, "How fast the model answers (tokens per second)")
        self.btn_exam = self._button(tools, "Tool-use exam", self.run_exam, tool)
        Tooltip(
            self, self.btn_exam, "Eight small tasks: does this model use Starlight's tools well?"
        )
        self.btn_disk = self._button(tools, "Disk space", self.open_disk, tool)
        Tooltip(self, self.btn_disk, "What takes space: models and folders; remove unused models")
        rows = [
            [self.btn_chat, self.btn_stats, self.btn_kill],  # in main
            [self.btn_restart, self.btn_check, self.btn_logs],  # in tools
            [self.btn_speed, self.btn_exam, self.btn_disk],
        ]
        for row, widgets in enumerate(rows):
            for column, widget in enumerate(widgets):
                widget.grid(row=max(row - 1, 0), column=column, sticky="ew", padx=gap, pady=gap)
        page.rowconfigure(6, weight=1)
        footer = ttk.Frame(page)
        footer.grid(row=7, column=0, sticky="ew", pady=(self.px(12), 0))
        footer.columnconfigure(0, weight=1)
        # Starlight's folder, a link that opens it
        self.r_footer = tk.Label(footer, text="", font=self.f_small, bg=self.c.bg,
                                 fg=self.c.dim, cursor="hand2", anchor="w")  # fmt: skip
        self.r_footer.grid(row=0, column=0, sticky="w")
        self.r_footer.bind("<Button-1>", lambda _event: open_folder(self.settings.root))
        self.r_footer.bind("<Enter>", lambda _event: self.r_footer.configure(fg=self.c.accent))
        self.r_footer.bind("<Leave>", lambda _event: self.r_footer.configure(fg=self.c.dim))
        Tooltip(self, self.r_footer, "Open Starlight's folder")
        self._button(footer, "Settings ...", self.open_settings).grid(
            row=0, column=1, padx=(0, self.px(8))
        )
        self._button(footer, "Quit Starlight", self.quit_app).grid(row=0, column=2)

    def _wrap_status(self, page_width: int) -> None:
        """The status texts (a long model name, LM Studio's advice) wrap inside their box."""
        # the name column with its margin, the dot with its margins, a right margin, border
        used = self.status_title_width + self.px(16) + self.px(12) + 2 * self.px(8)
        wrap = max(page_width - used - self.px(16) - 2, self.px(160))
        for _dot, _oval, label in self.status_rows.values():
            label.configure(wraplength=wrap)

    def _set_row(self, name: str, color: str, text: str) -> None:
        dot, oval, label = self.status_rows[name]
        dot.itemconfigure(oval, fill=color)
        label.configure(text=text)

    def on_status(self, status: Any) -> None:
        L, c = self.L, self.c
        self.last_status = status
        state = (
            "kill switch on" if status.kill
            else "ready" if status.model in ("ready", "off") and status.chat
            else "starting"
        )  # fmt: skip
        self.root.title(f"Starlight · {tr(state)}")  # (the taskbar shows it too)
        model_name = self.settings.model_file.stem if self.settings.use_model else ""
        elapsed = L.format_duration(status.seconds)
        if status.model == "ready":
            self._set_row(
                "Model", c.ok, trf("Ready: {model}", model=status.model_detail or model_name)
            )
        elif status.model == "waiting":  # LM Studio: not started, or no model loaded in it
            self._set_row("Model", c.warn, status.model_detail)
        elif status.model == "off":
            self._set_row(
                "Model", c.dim, tr("The built-in test model (it only repeats what you say)")
            )
        elif status.model == "restarting":
            self._set_row("Model", c.warn, tr("Stopped by itself; starting it again ..."))
        elif status.model == "down":
            reason = status.model_detail or tr("see the logs")
            self._set_row("Model", c.bad, trf("Stopped: {reason}", reason=reason))
        elif status.model == "paused":
            self._set_row(
                "Model",
                c.warn,
                trf("Paused, the graphics card is free ({detail})", detail=status.model_detail),
            )
        else:
            self._set_row(
                "Model", c.warn, trf("Loading into video memory ... {elapsed}", elapsed=elapsed)
            )
        chat_url, viewer_url = L.chat_url(), L.viewer_url()
        self._set_row(
            "Chat", c.ok if status.chat else c.warn, chat_url if status.chat else tr("Starting ...")
        )
        if status.viewer is None:
            self._set_row("Statistics", c.dim, tr("Off"))
        else:
            self._set_row(
                "Statistics",
                c.ok if status.viewer else c.warn,
                viewer_url if status.viewer else tr("Starting ..."),
            )
        if status.kill is None:
            self._set_row("Kill switch", c.dim, tr("Ready once the chat is up"))
        elif status.kill:
            self._set_row(
                "Kill switch", c.bad, tr("ENGAGED: Starlight will not do anything until you resume")
            )
        else:
            self._set_row("Kill switch", c.ok, tr("Off: Starlight can act"))
        self.btn_chat.state(["!disabled"] if status.chat else ["disabled"])
        self.btn_stats.state(["!disabled"] if status.viewer else ["disabled"])
        self.btn_kill.state(["!disabled"] if status.kill is not None else ["disabled"])
        self.btn_kill.configure(
            text=tr("Resume") if status.kill else tr("Stop everything"),
            style="Big.TButton" if status.kill else "Big.Danger.TButton",
        )
        # Our own model server can be restarted; a model in LM Studio can be reloaded there.
        lmstudio = self.settings.uses_lmstudio
        can_restart = self.services is not None and (
            lmstudio or getattr(self.services, "watchdog", None) is not None
        )
        paused = status.model == "paused"
        self.btn_restart.state(["!disabled"] if can_restart or paused else ["disabled"])
        self.btn_restart.configure(
            text=tr("Resume now") if paused
            else tr("Reload the model") if lmstudio
            else tr("Restart model server")
        )  # fmt: skip

        if status.model == "down":
            title, sub = (
                tr("Something needs attention"),
                tr("The model server stopped and could not be restarted."),
            )
        elif status.kill:
            title, sub = (
                tr("Starlight is stopped"),
                tr("The kill switch is engaged. Press Resume to let it act again."),
            )
        elif paused:
            title = tr("Starlight is paused for a game")
            sub = tr("The model comes back when the game closes, or press Resume now.")
        elif status.model in ("loading", "restarting") or not status.chat:
            title = tr("Starlight is starting ...")
            sub = tr("The model takes a minute to load; the chat opens by itself when it is ready.")
        else:
            title = tr("Starlight is running")
            window = self.chat_window
            if window is not None and window.wanted and not window.reason:
                sub = tr(
                    "The chat is in its own window (Open chat brings it back). Close this "
                    "window to stop Starlight."
                )
            else:
                sub = trf(
                    "Your chat is at {url}. Close this window to stop Starlight.", url=chat_url
                )
            if window is not None and window.reason:
                sub += "\n" + trf(
                    "Starlight's own window could not open ({reason}); the chat is in the browser.",
                    reason=window.reason,
                )
        self.r_title.configure(text=title)
        self.r_sub.configure(text=sub)
        self.r_footer.configure(text=trf("Folder: {path}", path=self.settings.root))
        ready = status.chat and status.model in ("ready", "off")
        if ready and not getattr(self, "was_ready", False):
            flash_window(self.root)  # it took a while: the taskbar button says it is ready
        self.was_ready = ready
        if ready and not self.opened_browser and self.settings.web:
            self.opened_browser = True
            self.open_chat(self.settings.first_page)  # (--open: that page)
        self._follow_chat_window()

    def _follow_chat_window(self) -> None:
        """The chat's own window was closed: Starlight still runs, and this window comes
        back to say so (with Open chat and Stop everything)."""
        open_now = self.chat_window is not None and self.chat_window.running()
        if self.window_was_open and not open_now and not self.closing:
            self.root.deiconify()
        self.window_was_open = open_now

    def open_chat(self, page: str = "") -> None:
        url = self.L.chat_url() + page
        if self.chat_window is None:
            webbrowser.open(url)
            return
        self.chat_window.open(url)
        if self.chat_window.wanted:
            self.root.iconify()  # the chat is the window now; this one waits in the taskbar

    def open_stats(self) -> None:
        webbrowser.open(self.L.viewer_url())

    def open_logs(self) -> None:
        open_folder(self.settings.root / "logs")

    def toggle_kill(self) -> None:
        status = getattr(self, "last_status", None)
        if status is None or status.kill is None:
            return
        services = self.services  # ("Save and restart" may clear it before the thread runs)
        if services is None:
            return
        engage = not status.kill
        threading.Thread(
            target=lambda: services.set_kill_switch(engage), name="kill", daemon=True
        ).start()

    def restart_model(self) -> None:
        services = self.services  # ("Save and restart" may clear it before the thread runs)
        if services is None:
            return
        status = getattr(self, "last_status", None)
        if status is not None and status.model == "paused":  # the button says Resume now
            services.resume_model()
            return

        def restart() -> None:
            if self.settings.uses_lmstudio:
                problem = self.L.reload_in_lmstudio(self.settings)
                if problem:
                    self.events.put(("note", f"The model was not reloaded: {problem}"))
            elif not services.restart_model():
                self.events.put(
                    (
                        "note",
                        "This model server was already running when Starlight started, "
                        "so it can't be restarted from here.",
                    )
                )

        threading.Thread(target=restart, name="restart-model", daemon=True).start()

    def on_note(self, text: str) -> None:
        messagebox.showinfo("Starlight", text)

    def open_settings(self) -> None:
        self.open_setup(settings_mode=True)

    def run_check(self) -> None:
        """Run `starlight doctor` and show what it says."""
        self._run_starlight(("doctor",), "Starlight check", 120)

    def run_speed_test(self) -> None:
        self._run_starlight(("model", "bench", "--runs", "2"), "Speed test", 600, "Measuring ...")

    def run_exam(self) -> None:
        self._run_starlight(
            ("model", "exam"), "Tool-use exam", 1800, "Eight tasks, a minute or two ..."
        )

    def _run_starlight(
        self, args: tuple[str, ...], title: str, timeout: float, note: str = ""
    ) -> None:
        """Run a `starlight ...` command in the background and show its output."""
        if self.services is None:
            return
        uv, root = self.services.installed.uv, self.settings.root
        if note:
            self.events.put(("note_quiet", f"{title}: {note}"))

        def work() -> None:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined,unused-ignore]
            try:
                done = subprocess.run(
                    self.L.starlight(uv, *args), cwd=root, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags,
                )  # fmt: skip
                text = (done.stdout + done.stderr).strip()
            except (OSError, subprocess.SubprocessError) as exc:
                text = f"It could not run: {exc}"
            self.events.put(("doctor", text or "No output.", title))

        threading.Thread(target=work, name=args[0], daemon=True).start()

    def on_note_quiet(self, text: str) -> None:
        self.r_sub.configure(text=text)

    def on_doctor(self, text: str, title: str = "Starlight check") -> None:
        c = self.c
        window = tk.Toplevel(self.root)
        window.title(title)
        window.configure(bg=c.bg)
        width = min(self.px(760), self.root.winfo_screenwidth() - 40)
        height = min(self.px(420), self.root.winfo_screenheight() - 100)
        window.geometry(f"{width}x{height}")
        box = tk.Text(
            window,
            wrap="none",
            bg=c.panel,
            fg=c.text,
            font=self.f_mono,
            relief="flat",
            padx=10,
            pady=8,
        )
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(fill="both", expand=True, padx=self.px(12), pady=(self.px(12), 0))
        row = ttk.Frame(window)
        row.pack(fill="x", padx=self.px(12), pady=self.px(10))
        self._button(row, "Copy", lambda: self._copy(text)).pack(side="left")
        self._button(row, "Close", window.destroy).pack(side="right")

    def open_disk(self) -> None:
        """What takes space: the models (unused ones can be removed) and the big folders."""
        c, L = self.c, self.L
        window = tk.Toplevel(self.root)
        window.transient(self.root)
        window.title("Disk space")
        window.configure(bg=c.bg)
        body = ttk.Frame(window, padding=self.px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        def fill() -> None:
            for child in body.winfo_children():
                child.destroy()
            report = L.disk_report(self.settings)
            total = sum(item["bytes"] for item in report)
            free = shutil.disk_usage(self.settings.root).free
            used = (
                f"Starlight uses {L.format_size(total)}; {L.format_size(free)} free on this drive."
            )
            self._label(body, used, "Head.TLabel").grid(
                row=0, column=0, columnspan=3, sticky="w", pady=(0, self.px(8))
            )
            for row, item in enumerate(report, start=1):
                name = item["name"] + (
                    "   (in use)" if item["kind"] == "model" and item["in_use"] else ""
                )
                self._label(body, name).grid(row=row, column=0, sticky="w", pady=self.px(2))
                self._label(body, L.format_size(item["bytes"]), "Dim.TLabel").grid(
                    row=row, column=1, sticky="e", padx=self.px(12)
                )
                if item["kind"] == "model" and not item["in_use"]:
                    self._button(body, "Remove", partial(remove, item["path"])).grid(
                        row=row, column=2
                    )
                elif item["kind"] == "folder":
                    self._button(body, "Open", partial(open_folder, item["path"])).grid(
                        row=row, column=2
                    )

        def remove(path: Path) -> None:
            if messagebox.askyesno("Starlight", f"Delete {path.name}? It can be downloaded again."):
                with contextlib.suppress(OSError):
                    path.unlink()
                fill()

        fill()

    def _copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    # -- errors ---------------------------------------------------------------------------------

    def _build_error(self) -> None:
        c = self.c
        page = self._new_page("error")
        page.rowconfigure(2, weight=1)
        title = self._label(page, "Starlight could not start", "Big.TLabel")
        title.grid(row=0, column=0, sticky="w")
        self.err_message = self._label(page, "", "TLabel", wrap=True)
        self.err_message.grid(row=1, column=0, sticky="w", pady=(self.px(8), self.px(10)))
        self.err_text = tk.Text(
            page, height=12, wrap="word", bg=c.panel, fg=c.dim, font=self.f_mono, relief="flat",
            highlightthickness=1, highlightbackground=c.border, padx=self.px(8), pady=self.px(6),
        )  # fmt: skip
        self.err_text.grid(row=2, column=0, sticky="nsew")
        buttons = ttk.Frame(page)
        buttons.grid(row=3, column=0, sticky="ew", pady=(self.px(12), 0))
        buttons.columnconfigure(0, weight=1)
        self._button(buttons, "Open logs folder", self.open_logs).grid(
            row=0, column=1, padx=self.px(6)
        )
        self.err_report = ""
        self._button(buttons, "Copy details", lambda: self._copy(self.err_report)).grid(
            row=0, column=2, padx=self.px(6)
        )
        self._button(buttons, "Quit", self.quit_app).grid(row=0, column=3, padx=self.px(6))
        self._button(buttons, "Try again", self.try_again, "Accent.TButton").grid(row=0, column=4)

    def on_error(self, message: str, details: str = "") -> None:
        self.polling = False
        self.err_message.configure(text=message)
        # The message shows above; the box holds the details and the last lines of the log
        # (what GuiUi wrote), and "Copy details" copies all three.
        parts = [details] if details else []
        with contextlib.suppress(OSError):
            if self.ui.log is not None:
                lines = self.ui.log.read_text(encoding="utf-8").splitlines()[-25:]
                if lines:
                    parts.append("Recent log:\n" + "\n".join(lines))
        extra = "\n\n".join(parts)
        self.err_report = "\n\n".join(part for part in (message, extra) if part)
        self.err_text.configure(state="normal")
        self.err_text.delete("1.0", "end")
        self.err_text.insert("1.0", extra or "No more details.")
        self.bar.stop()
        self.show("error")

    def _callback_failed(self, exc: type[BaseException], value: BaseException, tb: Any) -> None:
        details = "".join(traceback.format_exception(exc, value, tb))
        self.ui._write_log(details)
        self.on_error(f"The window hit a problem: {value}", details)

    # -- leaving --------------------------------------------------------------------------------

    def _stop_services(self) -> None:
        with contextlib.suppress(Exception):
            if self.services is not None:
                self.services.stop()
                self.services = None
            elif self.group is not None:
                self.group.stop()

    def quit_app(self) -> None:
        if self.closing:
            return
        question = ""
        if self.page in ("running", "progress") and self.services is not None:
            question = "Stop Starlight and close the window?"
        elif self.page == "progress":
            question = "Stop setting up? Finished downloads are kept."
        if question and not messagebox.askyesno("Starlight", question, default="no"):
            return
        self.closing = True
        self.polling = False
        self.p_title.configure(text="Stopping ...")
        self.p_sub.configure(text="")
        self.show("progress")
        threading.Thread(target=self._shutdown, name="shutdown", daemon=True).start()

    def _shutdown(self) -> None:
        self._stop_services()
        self.events.put(("exit",))

    def on_exit(self) -> None:
        self.exited = True
        self.root.destroy()

    # -- the loop -------------------------------------------------------------------------------

    def _pump(self) -> None:
        """Handle what the threads reported, then look again in 60 ms (the finally re-arms
        the timer even if something raised). An event is (name, *args) and goes to
        on_<name>: say, step, progress, waiting, note (GuiUi); lmstudio_found, gpu, size,
        missing, begin, running, status, note_quiet, doctor, error, exit (App's threads). A
        name without a method is dropped."""
        try:
            while not self.exited:
                event = self.events.get_nowait()
                handler = getattr(self, f"on_{event[0]}", None)
                if handler is not None:
                    try:
                        handler(*event[1:])
                    except Exception as exc:  # shown on the error page; the loop must go on
                        self._callback_failed(type(exc), exc, exc.__traceback__)
        except queue.Empty:
            pass
        finally:
            if not self.exited:
                self.root.after(60, self._pump)

    def run(self) -> int:
        self._check_layout()
        if self.fatal:
            self.on_error(self.fatal)
        elif self.needs_setup_page():
            self.open_setup(settings_mode=False)
            self._start_background_lookups()
        else:
            self.begin()
        self.root.deiconify()
        self.root.after(60, self._pump)
        self.root.mainloop()
        return 0

    def selftest(self) -> int:
        """Build and fill every page (no screen interaction): the check CI runs on the exe."""
        L = self.L
        self.opened_browser = True  # (a test must not open the real browser)
        self.open_setup(settings_mode=False)
        self.on_gpu(L.Gpu("Test graphics card", 16 * L.GB))
        self.on_size(L.MODEL_PRESETS["qwen7b"], 5 * L.GB)
        self.root.update()
        self.show_progress("Getting ready ...")
        self.on_step("Downloading the model")
        self.on_progress(3 * L.GB, 9 * L.GB, time.monotonic() - 60)
        self.on_say("a line of output")
        self.on_step("Setting up Python")
        self.toggle_download_pause()
        self.toggle_download_pause()  # (paused and going on again)
        self.use_model_url("https://huggingface.co/a/b/resolve/main/model-Q4_K_M.gguf", 5 * L.GB)
        self.open_hf_search()
        self.open_disk()
        self.on_doctor("40 tokens per second", "Speed test")
        self.show("running")
        for model in ("loading", "ready", "restarting", "down", "off", "paused"):
            self.on_status(L.Status(model=model, chat=True, viewer=True, kill=False, seconds=75))
        self.on_status(L.Status(model="ready", chat=True, viewer=None, kill=True))
        self.on_error("A test error", "details")
        self.root.update()
        self.root.destroy()
        print("gui ok", flush=True)
        return 0


def run(launcher: Any, args: Any) -> int | None:
    """Show the window. None: there is no screen to show it on.

    launcher is the starlight_launcher module itself: in Starlight.exe it runs as __main__,
    so importing it here would load a second copy. GuiUi must offer every method the
    launcher calls on its Ui."""
    try:
        app = App(launcher, args)
    except tk.TclError:
        return None
    if getattr(args, "gui_selftest", False):
        return app.selftest()
    return app.run()
