"""The Starlight window.

Starlight.exe shows this when it is started plainly (double-click): a setup page where you
choose the model and options, a progress page while things download, and a status page
with buttons while Starlight runs. It drives the same code as the text version through the
launcher's ``Ui`` interface, so what happens is identical; only how it is shown differs.

Everything that touches tkinter runs on the main thread. Slow work (the setup, the lookups,
the status polling, restarts, the shutdown, the kill switch, the check) runs in short daemon
threads that never call tkinter: they report by putting (name, *args) on ``App.events``,
and ``App._pump`` hands each event to ``on_<name>`` on the main thread. A few plain
attributes are shared without a lock: ``App.services`` (set by the setup thread before it
reports "running", cleared by ``_stop_services``) and ``App._poll_busy`` (cleared by the
polling thread).
"""

from __future__ import annotations

import contextlib
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.error
import webbrowser
from dataclasses import dataclass
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
    bad: str


# The colors of the web pages (see starlight/web/static/style.css).
DARK = Palette(
    bg="#0f1115", panel="#171a21", panel2="#1e222b", border="#2a2f3a", text="#e6e8ec",
    dim="#9aa3b2", accent="#6ea8fe", on_accent="#0b1220", ok="#3fb950", warn="#d29922",
    bad="#f85149",
)  # fmt: skip
LIGHT = Palette(
    bg="#f6f7f9", panel="#ffffff", panel2="#eef0f4", border="#d8dce3", text="#1f2328",
    dim="#5a6372", accent="#2563eb", on_accent="#ffffff", ok="#1a7f37", warn="#9a6700",
    bad="#cf222e",
)  # fmt: skip


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


def open_folder(path: Path) -> None:
    """Show a folder in the file manager."""
    with contextlib.suppress(OSError):
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined,unused-ignore]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


class GuiUi:
    """The launcher's Ui for the window: everything goes into a queue the window reads.
    It is not a subclass of the launcher's ``Ui`` (the launcher module is handed in at run
    time, see run()), so it must offer every method and attribute ``Ui`` has; mypy does
    not check that."""

    captures_output = True
    assume_yes = True  # the setup page was the question; nothing else is asked

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
        self.events.put(("step", text))

    def progress(self, done: int, total: int | None, started: float) -> None:
        self.events.put(("progress", done, total, started))

    def waiting(self, text: str) -> None:
        self.events.put(("waiting", text.lstrip("\r").strip()))

    def run_output(self, line: str) -> None:
        self._write_log(line)
        self.events.put(("say", line))

    def ask(self, question: str, default: bool) -> bool:
        """The setup page asked everything: the launcher's questions get their default."""
        return default


class App:
    """The window and everything on it."""

    def __init__(self, launcher: Any, args: Any) -> None:
        self.launcher = launcher
        self.args = args
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._dpi_aware()
        self.root = tk.Tk()  # raises TclError when there is no screen
        self.root.withdraw()
        self.colors = LIGHT if system_uses_light_theme() else DARK
        # 96 dpi is 100 % on Windows. px() scales sizes given in pixels; fonts are given in
        # points and scale by themselves.
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.services: Any = None
        self.group: Any = None
        self.page = ""
        self.pages: dict[str, ttk.Frame] = {}
        self._poll_busy = False
        self.opened_browser = False
        self.polling = False
        self._poll_job: str | None = None  # the timer of the status polling
        self.closing = False
        self.exited = False
        self.in_settings = False
        self.sizes: dict[str, int | None] = {}  # download size per model link; None: unknown
        self.gpu: Any = None
        self.fatal = ""
        try:
            self.settings = launcher.load_settings(args)
        except launcher.LauncherError as exc:
            self.fatal = str(exc)
            self.settings = launcher.Settings(root=Path.cwd())
        # The setup page is the question (Settings.assume_yes: maybe_tesseract asks nothing).
        self.settings.assume_yes = True
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
        self._build_progress()
        self._build_running()
        self._build_error()

    # -- window, fonts, colors ----------------------------------------------------------------

    @staticmethod
    def _dpi_aware() -> None:
        """Sharp text on scaled screens. Must run before tk.Tk() creates the first window."""
        if sys.platform == "win32":
            import ctypes

            process_system_dpi_aware = 1
            with contextlib.suppress(Exception):  # no shcore before Windows 8.1
                ctypes.windll.shcore.SetProcessDpiAwareness(process_system_dpi_aware)

    def px(self, value: int) -> int:
        return int(value * self.scale)

    def _init_window(self) -> None:
        root = self.root
        root.title("Starlight")
        root.configure(bg=self.colors.bg)
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
        holder = ttk.Frame(self.root)
        holder.pack(fill="both", expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.scroller = tk.Canvas(holder, bg=self.colors.bg, highlightthickness=0, borderwidth=0)
        self.scroller.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.scroller.yview)
        self.scroller.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.scrollbar.grid_remove()
        self._scrollable = False
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
        self.font = tkfont.Font(family=family, size=10)
        self.font_bold = tkfont.Font(family=family, size=10, weight="bold")
        self.font_small = tkfont.Font(family=family, size=9)
        self.font_big = tkfont.Font(family=family, size=18, weight="bold")
        self.font_head = tkfont.Font(family=family, size=12, weight="bold")
        self.font_mono = tkfont.Font(
            family="Consolas" if sys.platform == "win32" else "monospace", size=9
        )

    def _init_style(self) -> None:
        c = self.colors
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=c.bg, foreground=c.text, fieldbackground=c.panel2,
                        bordercolor=c.border, lightcolor=c.border, darkcolor=c.border,
                        troughcolor=c.panel2, focuscolor=c.bg, font=self.font)  # fmt: skip
        style.configure("TFrame", background=c.bg)
        style.configure("Card.TFrame", background=c.panel)
        style.configure("TLabel", background=c.bg, foreground=c.text)
        style.configure("Dim.TLabel", foreground=c.dim)
        style.configure("Small.TLabel", foreground=c.dim, font=self.font_small)
        style.configure("Big.TLabel", font=self.font_big)
        style.configure("Head.TLabel", font=self.font_head)
        style.configure("Section.TLabel", foreground=c.dim, font=self.font_small)
        style.configure("TButton", background=c.panel2, foreground=c.text, borderwidth=1,
                        padding=(self.px(14), self.px(7)), relief="flat")  # fmt: skip
        style.map("TButton", background=[("active", c.panel), ("disabled", c.panel)],
                  foreground=[("disabled", c.dim)], bordercolor=[("active", c.accent)])  # fmt: skip
        style.configure("Accent.TButton", background=c.accent, foreground=c.on_accent,
                        bordercolor=c.accent, font=self.font_bold)  # fmt: skip
        style.map("Accent.TButton", background=[("active", c.accent), ("disabled", c.panel2)],
                  foreground=[("disabled", c.dim)])  # fmt: skip
        style.configure("Danger.TButton", background=c.bad, foreground="#ffffff", bordercolor=c.bad)
        style.map("Danger.TButton", background=[("active", c.bad), ("disabled", c.panel2)],
                  foreground=[("disabled", c.dim)])  # fmt: skip
        style.configure("TCheckbutton", background=c.bg, foreground=c.text, indicatorcolor=c.panel2,
                        indicatorbackground=c.panel2, padding=(0, self.px(3)))  # fmt: skip
        style.map("TCheckbutton", indicatorbackground=[("selected", c.accent)],
                  indicatorforeground=[("selected", c.on_accent)],
                  background=[("active", c.bg)])  # fmt: skip
        style.configure("Horizontal.TProgressbar", background=c.accent, troughcolor=c.panel2,
                        bordercolor=c.panel2, lightcolor=c.accent, darkcolor=c.accent,
                        thickness=self.px(10))  # fmt: skip

    def _build_header(self) -> None:
        c = self.colors
        bar = tk.Frame(self.root, bg=c.panel, highlightthickness=0)
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=c.panel)
        inner.pack(fill="x", padx=self.px(28), pady=self.px(12))
        self._draw_logo(inner)
        tk.Label(inner, text="Starlight", font=self.font_head, bg=c.panel, fg=c.text).pack(
            side="left", padx=(self.px(10), 0)
        )
        tk.Label(
            inner,
            text=f"launcher {self.launcher.VERSION}",
            font=self.font_small,
            bg=c.panel,
            fg=c.dim,
        ).pack(side="right")
        tk.Frame(self.root, bg=c.border, height=1).pack(fill="x")

    def _draw_logo(self, parent: tk.Misc) -> None:
        """The logo, drawn as vectors: purple square (lavender in the light look), the gold
        sparkle and its rays."""
        light = self.colors is LIGHT
        size = self.px(34)
        canvas = tk.Canvas(
            parent, width=size, height=size, bg=self.colors.panel, highlightthickness=0
        )
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
        self.page = name

    def _new_page(self, name: str) -> ttk.Frame:
        frame = ttk.Frame(self.body)
        frame.columnconfigure(0, weight=1)
        self.pages[name] = frame
        return frame

    def _label(
        self, parent: tk.Misc, text: str = "", style: str = "TLabel", **kwargs: Any
    ) -> ttk.Label:
        return ttk.Label(parent, text=text, style=style, **kwargs)

    def _button(
        self, parent: tk.Misc, text: str, command: Any, style: str = "TButton"
    ) -> ttk.Button:
        return ttk.Button(parent, text=text, command=command, style=style, cursor="hand2")

    # -- setup page -----------------------------------------------------------------------------

    # The download size of each preset in GB until probe() has told the real one (the
    # video memory each needs is in the launcher's MODEL_INFO). Keys: MODEL_PRESETS.
    APPROX_DOWNLOAD_GB: ClassVar[dict[str, float]] = {
        "qwen14b-uncensored": 9.0,
        "qwen14b": 9.0,
        "qwen7b": 4.7,
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
        section = self._label(models, "MODEL", "Section.TLabel")
        section.grid(row=0, column=0, sticky="w", pady=(0, self.px(6)))
        self.cards: dict[str, dict[str, Any]] = {}
        self.model_var = tk.StringVar(value="")
        keys = list(self.launcher.MODEL_PRESETS)
        if self.settings.model_url not in self.launcher.MODEL_PRESETS.values():
            keys.append("custom")
        for index, key in enumerate(keys, start=1):
            self._make_card(models, key, index)
        self.model_var.set(self._current_model_key())
        self.test_model_note = self._label(
            models,
            "Starlight's built-in test model is used (started with --no-model). It only "
            "repeats what you say, so nothing is downloaded.",
            "Dim.TLabel",
            wraplength=self.px(740),
            justify="left",
        )
        self.gpu_label = self._label(
            models, "Graphics card: looking ...", "Small.TLabel", wraplength=self.px(740)
        )
        self.gpu_label.grid(row=len(keys) + 1, column=0, sticky="w", pady=(self.px(6), 0))

        options = ttk.Frame(page)
        options.grid(row=3, column=0, sticky="ew", pady=(self.px(16), 0))
        heading = self._label(options, "OPTIONS", "Section.TLabel")
        heading.grid(row=0, column=0, sticky="w", pady=(0, self.px(4)))
        self.gpu_var = tk.BooleanVar(value=self.settings.variant != "cpu")
        self.voice_var = tk.BooleanVar(value=bool(self.settings.voice))
        self.ocr_var = tk.BooleanVar(value=bool(self.settings.tesseract))
        self.viewer_var = tk.BooleanVar(value=bool(self.settings.viewer))
        rows = [
            (
                self.gpu_var,
                "Run the model on the graphics card (turn off only if there is none: much slower)",
            ),
            (self.voice_var, "Speech: talk to Starlight and hear its answers (adds about 0.6 GB)"),
        ]
        self.has_ocr_option = sys.platform == "win32" and not self.launcher.tesseract_found()
        if self.has_ocr_option:
            rows.append(
                (
                    self.ocr_var,
                    "Read the screen: install Tesseract OCR "
                    "(Windows asks for administrator approval)",
                )
            )
        rows.append((self.viewer_var, "Statistics page with charts about what Starlight is doing"))
        for index, (variable, text) in enumerate(rows, start=1):
            check = ttk.Checkbutton(
                options, text=text, variable=variable, command=self._refresh_summary, cursor="hand2"
            )
            check.grid(row=index, column=0, sticky="w")

        self.summary = self._label(page, "", "Dim.TLabel", wraplength=self.px(740), justify="left")
        self.summary.grid(row=4, column=0, sticky="w", pady=(self.px(16), 0))
        page.rowconfigure(5, weight=1)
        buttons = ttk.Frame(page)
        buttons.grid(row=6, column=0, sticky="ew", pady=(self.px(10), 0))
        buttons.columnconfigure(0, weight=1)
        note = self._label(
            buttons, "Your choices are saved in config/launcher.json.", "Small.TLabel"
        )
        note.grid(row=0, column=0, sticky="w")
        self.setup_cancel = self._button(buttons, "Cancel", self.setup_cancel_clicked)
        self.setup_cancel.grid(row=0, column=1, padx=(0, self.px(8)))
        self.setup_quit = self._button(buttons, "Quit", self.quit_app)
        self.setup_quit.grid(row=0, column=2, padx=(0, self.px(8)))
        self.setup_go = self._button(
            buttons, "Install and start", self.setup_go_clicked, "Accent.TButton"
        )
        self.setup_go.grid(row=0, column=3)

    def _current_model_key(self) -> str:
        for key, url in self.launcher.MODEL_PRESETS.items():
            if url == self.settings.model_url:
                return str(key)
        return "custom"

    def _model_url_of(self, key: str) -> str:
        return str(self.settings.model_url if key == "custom" else self.launcher.MODEL_PRESETS[key])

    def _model_file_of(self, key: str) -> Path:
        return Path(self.settings.models / self.launcher._file_name(self._model_url_of(key)))

    def _make_card(self, parent: ttk.Frame, key: str, row: int) -> None:
        c = self.colors
        if key == "custom":
            title = f"Your own model: {self.launcher._file_name(self.settings.model_url)}"
            text = "Set as model_url in config/launcher.json."
        else:
            title, text, _ = self.launcher.MODEL_INFO[key]
        frame = tk.Frame(
            parent, bg=c.panel, highlightthickness=2, highlightbackground=c.border, cursor="hand2"
        )
        frame.grid(row=row, column=0, sticky="ew", pady=self.px(3))
        frame.columnconfigure(1, weight=1)
        mark = tk.Canvas(frame, width=self.px(22), height=self.px(22), bg=c.panel,
                         highlightthickness=0)  # fmt: skip
        self._draw_mark(mark, "empty", c.dim)
        mark.grid(row=0, column=0, rowspan=2, padx=(self.px(12), self.px(8)), pady=self.px(8))
        name = tk.Label(frame, text=title, font=self.font_bold, bg=c.panel, fg=c.text, anchor="w")
        name.grid(row=0, column=1, sticky="w", pady=(self.px(8), 0))
        desc = tk.Label(frame, text=text, font=self.font_small, bg=c.panel, fg=c.dim, anchor="w")
        desc.grid(row=1, column=1, sticky="w", pady=(0, self.px(8)))
        side = tk.Frame(frame, bg=c.panel)
        side.grid(row=0, column=2, rowspan=2, sticky="e", padx=self.px(12))
        meta = tk.Label(side, text="", font=self.font_small, bg=c.panel, fg=c.dim, anchor="e")
        meta.pack(anchor="e")
        badges = tk.Frame(side, bg=c.panel)
        badges.pack(anchor="e", pady=(self.px(2), 0))

        def choose(_event: Any, chosen: str = key) -> None:
            self.select_model(chosen, touched=True)

        for widget in (frame, mark, name, desc, side, meta, badges):
            widget.bind("<Button-1>", choose)
        self.cards[key] = {"frame": frame, "mark": mark, "meta": meta, "badges": badges}

    def select_model(self, key: str, touched: bool = False) -> None:
        if touched:
            self.model_touched = True
        self.model_var.set(key)
        for card_key, card in self.cards.items():
            chosen = card_key == key
            card["frame"].configure(
                highlightbackground=self.colors.accent if chosen else self.colors.border
            )
            self._draw_mark(
                card["mark"],
                "radio" if chosen else "empty",
                self.colors.accent if chosen else self.colors.dim,
            )
        self._refresh_summary()

    def _refresh_cards(self, recommended: str = "") -> None:
        for key, card in self.cards.items():
            have = self._model_file_of(key).is_file()
            size = self.sizes.get(self._model_url_of(key))
            parts = []
            if key != "custom":
                parts.append(f"about {self.launcher.MODEL_INFO[key][2]:.0f} GB video memory")
            if not have and size:
                parts.append(f"{self.launcher.format_size(size)} download")
            card["meta"].configure(text=" - ".join(parts))
            for child in card["badges"].winfo_children():
                child.destroy()
            if key == recommended:
                pill_fg = self.colors.on_accent
                self._pill(card["badges"], "Recommended for your PC", self.colors.accent, pill_fg)
            if have:
                # The text on the green pill: dark on the bright green of the dark look.
                pill_fg = "#06240f" if self.colors is DARK else "#ffffff"
                self._pill(card["badges"], "Downloaded", self.colors.ok, pill_fg)

    def _draw_mark(self, canvas: tk.Canvas, kind: str, colour: str) -> None:
        """A small round mark drawn on a canvas (fonts differ in which symbols they have):
        "empty" a ring, "radio" a ring with a dot, "dot" a filled dot, "done" a check mark."""
        canvas.delete("all")
        size = int(canvas.cget("width"))
        pad = size * 0.14
        if kind == "done":
            canvas.create_oval(pad, pad, size - pad, size - pad, fill=colour, outline="")
            points = [0.30, 0.52, 0.45, 0.67, 0.72, 0.36]
            canvas.create_line(*[value * size for value in points], fill=self.colors.bg, width=2)
            return
        if kind == "dot":
            canvas.create_oval(pad, pad, size - pad, size - pad, fill=colour, outline="")
            return
        canvas.create_oval(pad, pad, size - pad, size - pad, outline=colour, width=2)
        if kind == "radio":
            inner = size * 0.34
            canvas.create_oval(inner, inner, size - inner, size - inner, fill=colour, outline="")

    def _pill(self, parent: tk.Misc, text: str, bg: str, fg: str) -> None:
        pill = tk.Label(
            parent, text=text, font=self.font_small, bg=bg, fg=fg, padx=self.px(8), pady=1
        )
        pill.pack(side="left", padx=(self.px(4), 0))

    def _start_background_lookups(self) -> None:
        """The graphics card and the sizes of the model downloads: found while the page is
        already showing."""

        def look() -> None:
            self.events.put(("gpu", self.launcher.detect_gpu()))
            for key in self.launcher.MODEL_PRESETS:
                url = self.launcher.MODEL_PRESETS[key]
                if not self._model_file_of(key).is_file():
                    try:
                        self.events.put(("size", url, self.launcher.probe(url).size))
                    except (
                        urllib.error.URLError,
                        OSError,
                        ValueError,
                        self.launcher.LauncherError,
                    ):
                        self.events.put(("size", url, None))

        threading.Thread(target=look, name="lookups", daemon=True).start()

    def on_gpu(self, gpu: Any) -> None:
        self.gpu = gpu
        recommended, why = self.launcher.recommend_model(gpu)
        if gpu is None:
            self.gpu_label.configure(text="Graphics card: not found. " + why)
        else:
            memory = f" ({gpu.vram / self.launcher.GB:.0f} GB video memory)" if gpu.vram else ""
            self.gpu_label.configure(text=f"Graphics card: {gpu.name}{memory}. {why}")
        self._refresh_cards(recommended if gpu is not None else "")
        first_run = not self._installed()
        if gpu is not None and first_run and not self.model_touched and recommended in self.cards:
            self.select_model(recommended)

    def on_size(self, url: str, size: int | None) -> None:
        self.sizes[url] = size
        recommended, _ = self.launcher.recommend_model(self.gpu)
        self._refresh_cards(recommended if self.gpu is not None else "")
        self._refresh_summary()

    def _installed(self) -> bool:
        return bool((self.settings.tools / "uv" / self.launcher.exe("uv")).is_file())

    def _refresh_summary(self) -> None:
        import shutil

        launcher = self.launcher
        gb = launcher.GB
        need = 0.0  # the sizes are the launcher's (see FIRST_RUN_SIZE there)
        if not self._installed():
            need += launcher.FIRST_RUN_SIZE
        if self.settings.use_model:
            key = self.model_var.get()
            if not self._installed():
                need += launcher.LLAMA_SIZE
            if key and not self._model_file_of(key).is_file():
                known = self.sizes.get(self._model_url_of(key))
                if known:
                    need += known
                else:
                    need += self.APPROX_DOWNLOAD_GB.get(key, 9.0) * gb
        if self.voice_var.get():
            need += launcher.VOICE_SIZE
        try:
            free = shutil.disk_usage(self.settings.root).free
        except OSError:
            free = 10**15  # unknown: do not block the button
        enough = free >= need + launcher.SPARE_SPACE
        if need <= 0:
            text = "Nothing to download."
        else:
            text = (
                f"Needs about {need / gb:.1f} GB of free space; "
                f"this drive has {free / gb:.0f} GB free."
            )
        if not enough:
            text += " That is not enough: free some space first."
        self.summary.configure(text=text, foreground=self.colors.dim if enough else self.colors.bad)
        self.setup_go.state(["!disabled"] if enough else ["disabled"])

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
        for card in self.cards.values():
            if use_model:
                card["frame"].grid()
            else:
                card["frame"].grid_remove()
        if use_model:
            self.test_model_note.grid_remove()
            self.gpu_label.grid()
        else:
            self.test_model_note.grid(row=1, column=0, sticky="w")
            self.gpu_label.grid_remove()
        self.select_model(self.model_var.get() or self._current_model_key())
        self._refresh_cards()
        self.show("setup")

    def setup_cancel_clicked(self) -> None:
        self.show("running")

    def setup_go_clicked(self) -> None:
        launcher, settings = self.launcher, self.settings
        key = self.model_var.get()
        choices: dict[str, Any] = {
            "variant": "vulkan" if self.gpu_var.get() else "cpu",
            "voice": self.voice_var.get(),
            "viewer": self.viewer_var.get(),
        }
        drop: tuple[str, ...] = ()
        if settings.use_model and key != "custom":
            settings.model_url = launcher.MODEL_PRESETS[key]
            choices["model"] = key
            drop = ("model_url",)  # a saved model_url would win over "model" (load_settings)
        settings.variant = choices["variant"]
        settings.voice = choices["voice"]
        settings.viewer = choices["viewer"]
        if self.has_ocr_option:
            settings.tesseract = self.ocr_var.get()
            choices["tesseract"] = settings.tesseract
        with contextlib.suppress(OSError):
            launcher.save_choices(settings.root, choices, drop)
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
        c = self.colors
        page = self._new_page("progress")
        page.rowconfigure(4, weight=1)
        self.progress_title = self._label(
            page, "", "Big.TLabel", wraplength=self.px(740), justify="left"
        )
        self.progress_title.grid(row=0, column=0, sticky="w")
        self.progress_sub = self._label(page, "", "Dim.TLabel")
        self.progress_sub.grid(row=1, column=0, sticky="w", pady=(self.px(2), self.px(10)))
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
        toggle = ttk.Checkbutton(
            details, text="Show details", variable=self.details_open, command=self._toggle_details
        )
        toggle.grid(row=0, column=0, sticky="w")
        self.log_frame = ttk.Frame(details)
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            self.log_frame, height=9, wrap="word", bg=c.panel, fg=c.dim, font=self.font_mono,
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
        self._button(buttons, "Quit", self.quit_app).grid(row=0, column=1)

    def _toggle_details(self) -> None:
        if self.details_open.get():
            self.log_frame.grid(row=1, column=0, sticky="nsew", pady=(self.px(6), 0))
        else:
            self.log_frame.grid_remove()

    def show_progress(self, title: str) -> None:
        for glyph, text in self.step_rows:
            glyph.destroy()
            text.destroy()
        self.step_rows.clear()
        self.progress_title.configure(text=title)
        self.progress_sub.configure(text="")
        self.bar.configure(mode="indeterminate")
        self.bar.start(14)
        self.show("progress")

    def _step_row(self, text: str) -> None:
        c = self.colors
        row = len(self.step_rows)
        glyph = tk.Canvas(self.steps_frame, width=self.px(18), height=self.px(18), bg=c.bg,
                          highlightthickness=0)  # fmt: skip
        self._draw_mark(glyph, "dot", c.accent)
        label = tk.Label(
            self.steps_frame, text=text, fg=c.text, bg=c.bg, font=self.font, anchor="w"
        )
        glyph.grid(row=row, column=0, sticky="w", padx=(0, self.px(8)))
        label.grid(row=row, column=1, sticky="w")
        self.step_rows.append((glyph, label))

    def on_step(self, text: str) -> None:
        if self.step_rows:
            glyph, label = self.step_rows[-1]
            self._draw_mark(glyph, "done", self.colors.ok)
            label.configure(fg=self.colors.dim)
        self._step_row(text)
        self.progress_title.configure(text=text)
        self.progress_sub.configure(text="")
        self.bar.configure(mode="indeterminate")
        self.bar.start(14)

    def on_progress(self, done: int, total: int | None, started: float) -> None:
        fraction, text = self.launcher.transfer_text(done, total, started)
        self.progress_sub.configure(text=text)
        if fraction < 0:
            return
        self.bar.stop()
        self.bar.configure(mode="determinate", value=fraction * 1000)

    def on_waiting(self, text: str) -> None:
        self.progress_sub.configure(text=text)

    MAX_LOG_LINES = 1500  # the details box keeps the newest lines (a uv sync prints many)

    def on_say(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        last_line = int(self.log_text.index("end-1c").split(".")[0])  # Tk: "line.column"
        if last_line > self.MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{last_line - self.MAX_LOG_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # -- starting -------------------------------------------------------------------------------

    def needs_setup_page(self) -> bool:
        if not self._installed():
            return True
        return bool(self.settings.use_model and not self.settings.model_file.is_file())

    def begin(self) -> None:
        """Install what is missing and start Starlight, in a worker thread."""
        if self.group is None:
            self.group = self.launcher.ProcessGroup()  # also ends everything if this window dies
        self.show_progress("Getting ready ...")
        threading.Thread(target=self._work, name="setup", daemon=True).start()

    def _work(self) -> None:
        launcher = self.launcher
        try:
            installed = launcher.setup(self.settings, self.ui)
            services = launcher.Services(self.settings, self.ui, installed, group=self.group)
            services.start()
            self.services = services  # before "running": on_running starts polling it
            self.events.put(("running",))
        except launcher.LauncherError as exc:
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
                settings = self.launcher.load_settings(self.args)
            except self.launcher.LauncherError as exc:
                self.fatal = str(exc)
                self.on_error(self.fatal)
                return
            self.fatal = ""
            settings.assume_yes = True  # the setup page is the question
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
        c = self.colors
        page = self._new_page("running")
        self.running_title = self._label(page, "", "Big.TLabel")
        self.running_title.grid(row=0, column=0, sticky="w")
        self.running_sub = self._label(
            page, "", "Dim.TLabel", wraplength=self.px(740), justify="left"
        )
        self.running_sub.grid(row=1, column=0, sticky="w", pady=(self.px(2), self.px(14)))

        card = tk.Frame(page, bg=c.panel, highlightthickness=1, highlightbackground=c.border)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(2, weight=1)
        self.status_rows: dict[str, tuple[tk.Canvas, int, tk.Label]] = {}
        for index, name in enumerate(("Model", "Chat", "Statistics", "Kill switch")):
            pad = (
                self.px(12) if index == 0 else self.px(4),
                self.px(12) if index == 3 else self.px(4),
            )
            tk.Label(
                card, text=name, font=self.font_bold, bg=c.panel, fg=c.text, anchor="w", width=11
            ).grid(row=index, column=0, sticky="w", padx=(self.px(16), 0), pady=pad)
            dot = tk.Canvas(
                card, width=self.px(12), height=self.px(12), bg=c.panel, highlightthickness=0
            )
            oval = dot.create_oval(1, 1, self.px(11), self.px(11), fill=c.dim, outline="")
            dot.grid(row=index, column=1, padx=self.px(8), pady=pad)
            text = tk.Label(card, text="", font=self.font, bg=c.panel, fg=c.text, anchor="w")
            text.grid(row=index, column=2, sticky="w", pady=pad)
            self.status_rows[name] = (dot, oval, text)

        buttons = ttk.Frame(page)
        buttons.grid(row=3, column=0, sticky="ew", pady=(self.px(18), 0))
        for column in range(3):
            buttons.columnconfigure(column, weight=1, uniform="b")
        gap = self.px(4)
        self.btn_chat = self._button(buttons, "Open chat", self.open_chat, "Accent.TButton")
        self.btn_stats = self._button(buttons, "Statistics", self.open_stats)
        self.btn_kill = self._button(buttons, "Stop everything", self.toggle_kill, "Danger.TButton")
        self.btn_restart = self._button(buttons, "Restart model server", self.restart_model)
        self.btn_check = self._button(buttons, "Check everything", self.run_check)
        self.btn_logs = self._button(buttons, "Open logs folder", self.open_logs)
        grid = [
            [self.btn_chat, self.btn_stats, self.btn_kill],
            [self.btn_restart, self.btn_check, self.btn_logs],
        ]
        for row, widgets in enumerate(grid):
            for column, widget in enumerate(widgets):
                widget.grid(row=row, column=column, sticky="ew", padx=gap, pady=gap)
        page.rowconfigure(4, weight=1)
        footer = ttk.Frame(page)
        footer.grid(row=5, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.running_footer = self._label(footer, "", "Small.TLabel")
        self.running_footer.grid(row=0, column=0, sticky="w")
        self._button(footer, "Settings ...", self.open_settings).grid(
            row=0, column=1, padx=(0, self.px(8))
        )
        self._button(footer, "Quit Starlight", self.quit_app).grid(row=0, column=2)

    def _set_row(self, name: str, color: str, text: str) -> None:
        dot, oval, label = self.status_rows[name]
        dot.itemconfigure(oval, fill=color)
        label.configure(text=text)

    def on_status(self, status: Any) -> None:
        launcher, c = self.launcher, self.colors
        self.last_status = status
        model_name = self.settings.model_file.stem if self.settings.use_model else ""
        elapsed = launcher.format_duration(status.seconds)
        if status.model == "ready":
            self._set_row("Model", c.ok, f"Ready: {model_name}")
        elif status.model == "off":
            self._set_row("Model", c.dim, "The built-in test model (it only repeats what you say)")
        elif status.model == "restarting":
            self._set_row("Model", c.warn, "Stopped by itself; starting it again ...")
        elif status.model == "down":
            self._set_row("Model", c.bad, "Stopped: " + (status.model_detail or "see the logs"))
        else:
            self._set_row("Model", c.warn, f"Loading into video memory ... {elapsed}")
        chat_url, viewer_url = launcher.chat_url(), launcher.viewer_url()
        self._set_row(
            "Chat", c.ok if status.chat else c.warn, chat_url if status.chat else "Starting ..."
        )
        if status.viewer is None:
            self._set_row("Statistics", c.dim, "Off")
        else:
            self._set_row(
                "Statistics",
                c.ok if status.viewer else c.warn,
                viewer_url if status.viewer else "Starting ...",
            )
        if status.kill is None:
            self._set_row("Kill switch", c.dim, "-")
        elif status.kill:
            self._set_row(
                "Kill switch", c.bad, "ENGAGED: Starlight will not do anything until you resume"
            )
        else:
            self._set_row("Kill switch", c.ok, "Off: Starlight can act")
        self.btn_chat.state(["!disabled"] if status.chat else ["disabled"])
        self.btn_stats.state(["!disabled"] if status.viewer else ["disabled"])
        self.btn_kill.state(["!disabled"] if status.kill is not None else ["disabled"])
        self.btn_kill.configure(
            text="Resume" if status.kill else "Stop everything",
            style="TButton" if status.kill else "Danger.TButton",
        )
        can_restart = (
            self.services is not None and getattr(self.services, "watchdog", None) is not None
        )
        self.btn_restart.state(["!disabled"] if can_restart else ["disabled"])

        if status.model == "down":
            title, sub = (
                "Something needs attention",
                "The model server stopped and could not be restarted.",
            )
        elif status.kill:
            title, sub = (
                "Starlight is stopped",
                "The kill switch is engaged. Press Resume to let it act again.",
            )
        elif status.model in ("loading", "restarting") or not status.chat:
            title = "Starlight is starting ..."
            sub = "The model takes a minute to load; the chat opens by itself when it is ready."
        else:
            title = "Starlight is running"
            sub = f"Your chat is at {chat_url}. Close this window to stop Starlight."
        self.running_title.configure(text=title)
        self.running_sub.configure(text=sub)
        self.running_footer.configure(text=str(self.settings.root))
        ready = status.chat and status.model in ("ready", "off")
        if ready and not self.opened_browser and self.settings.web:
            self.opened_browser = True
            self.open_chat()

    def open_chat(self) -> None:
        webbrowser.open(self.launcher.chat_url())

    def open_stats(self) -> None:
        webbrowser.open(self.launcher.viewer_url())

    def open_logs(self) -> None:
        open_folder(self.settings.root / "logs")

    def toggle_kill(self) -> None:
        status = getattr(self, "last_status", None)
        # The thread gets this copy: a "Save and restart" may set self.services to None
        # while the thread runs.
        services = self.services
        if services is None or status is None or status.kill is None:
            return
        engage = not status.kill
        threading.Thread(
            target=lambda: services.set_kill_switch(engage), name="kill", daemon=True
        ).start()

    def restart_model(self) -> None:
        services = self.services  # the thread gets this copy (see toggle_kill)
        if services is None:
            return

        def restart() -> None:
            if not services.restart_model():
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
        if self.services is None:
            return
        uv, root = self.services.installed.uv, self.settings.root

        def check() -> None:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined,unused-ignore]
            try:
                done = subprocess.run(
                    self.launcher.starlight(uv, "doctor"), cwd=root, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=120, creationflags=flags,
                )  # fmt: skip
                text = (done.stdout + done.stderr).strip()
            except (OSError, subprocess.SubprocessError) as exc:
                text = f"The check could not run: {exc}"
            self.events.put(("doctor", text or "No output."))

        threading.Thread(target=check, name="doctor", daemon=True).start()

    def on_doctor(self, text: str) -> None:
        c = self.colors
        window = tk.Toplevel(self.root)
        window.title("Starlight check")
        window.configure(bg=c.bg)
        window.geometry(f"{self.px(760)}x{self.px(420)}")
        box = tk.Text(
            window,
            wrap="none",
            bg=c.panel,
            fg=c.text,
            font=self.font_mono,
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

    def _copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    # -- errors ---------------------------------------------------------------------------------

    def _build_error(self) -> None:
        c = self.colors
        page = self._new_page("error")
        page.rowconfigure(2, weight=1)
        title = self._label(page, "Starlight could not start", "Big.TLabel")
        title.grid(row=0, column=0, sticky="w")
        self.err_message = self._label(page, "", "TLabel", wraplength=self.px(740), justify="left")
        self.err_message.grid(row=1, column=0, sticky="w", pady=(self.px(8), self.px(10)))
        self.err_text = tk.Text(
            page, height=12, wrap="word", bg=c.panel, fg=c.dim, font=self.font_mono, relief="flat",
            highlightthickness=1, highlightbackground=c.border, padx=self.px(8), pady=self.px(6),
        )  # fmt: skip
        self.err_text.grid(row=2, column=0, sticky="nsew")
        buttons = ttk.Frame(page)
        buttons.grid(row=3, column=0, sticky="ew", pady=(self.px(12), 0))
        buttons.columnconfigure(0, weight=1)
        self._button(buttons, "Open logs folder", self.open_logs).grid(
            row=0, column=1, padx=self.px(6)
        )
        self._button(
            buttons, "Copy details", lambda: self._copy(self.err_text.get("1.0", "end"))
        ).grid(row=0, column=2, padx=self.px(6))
        self._button(buttons, "Quit", self.quit_app).grid(row=0, column=3, padx=self.px(6))
        self._button(buttons, "Try again", self.try_again, "Accent.TButton").grid(row=0, column=4)

    def on_error(self, message: str, details: str = "") -> None:
        self.polling = False
        self.err_message.configure(text=message)
        log_tail = ""
        if self.ui.log is not None:  # logs/launcher.log in the Starlight folder
            with contextlib.suppress(OSError):
                lines = self.ui.log.read_text(encoding="utf-8").splitlines()
                log_tail = "\n".join(lines[-25:])
        parts = [message, details]
        if log_tail:
            parts.append("Recent log:\n" + log_tail)
        report = "\n\n".join(part for part in parts if part)
        self.err_text.configure(state="normal")
        self.err_text.delete("1.0", "end")
        self.err_text.insert("1.0", report)
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
        self.progress_title.configure(text="Stopping ...")
        self.progress_sub.configure(text="")
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
        """Handle what the threads reported, then look again in 60 ms (the finally sets the
        next look even when something here raised). The loop ends when the queue is empty
        (queue.Empty) or the window is gone.

        An event is a tuple (name, *args) and goes to the method on_<name>: say, step,
        progress, waiting (from GuiUi); gpu, size, begin, running, error, status, note,
        doctor, exit (from the window's own threads). A name without a method is dropped."""
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
        launcher = self.launcher
        self.opened_browser = True  # (a test must not open the real browser)
        self.open_setup(settings_mode=False)
        self.on_gpu(launcher.Gpu("Test graphics card", 16 * launcher.GB))
        self.on_size(launcher.MODEL_PRESETS["qwen7b"], 5 * launcher.GB)
        self.root.update()
        self.show_progress("Getting ready ...")
        self.on_step("Downloading the model")
        self.on_progress(3 * launcher.GB, 9 * launcher.GB, time.monotonic() - 60)
        self.on_say("a line of output")
        self.on_step("Setting up Python")
        self.show("running")
        for model in ("loading", "ready", "restarting", "down", "off"):
            self.on_status(
                launcher.Status(model=model, chat=True, viewer=True, kill=False, seconds=75)
            )
        self.on_status(launcher.Status(model="ready", chat=True, viewer=None, kill=True))
        self.on_error("A test error", "details")
        self.root.update()
        self.root.destroy()
        print("gui ok", flush=True)
        return 0


def run(launcher: Any, args: Any) -> int | None:
    """Show the window. None: there is no screen to show it on.

    ``launcher`` is the starlight_launcher module itself, handed over instead of imported
    because in Starlight.exe it runs as __main__. It is typed Any, so mypy does not check
    the window's calls into it."""
    try:
        app = App(launcher, args)
    except tk.TclError:
        return None
    if getattr(args, "gui_selftest", False):
        return app.selftest()
    return app.run()
