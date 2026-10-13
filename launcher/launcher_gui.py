"""The Starlight window.

Starlight.exe shows this when it is started plainly (double-click): a setup page where you
choose the model and options, a progress page while things download, and a status page
with buttons while Starlight runs. It drives the same code as the text version through the
launcher's ``Ui`` interface, so what happens is identical; only how it is shown differs.

Everything that touches tkinter runs on the main thread. The work (downloads, starting
programs) runs in a worker thread that only talks to the window through a queue.
"""

from __future__ import annotations

import contextlib
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

# The star on a dark rounded square (the same icon as the web pages), 64 x 64 PNG.
ICON_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAALH0lEQVR42uWbf4xcV3XHP+fcNzs7s/Njf9m7azc4"
    "G2q7EMWpHYfQ2CROHGxqsCMqo1hV1FRN/2miFgKYioqKVAg3ElVFE1WkaksJShOhICGgqVJIESIoreJELWkpRiJx"
    "LP/YXa/t/b2zM/PuPf3jzaw3jn+s17MPJ1zpajU7T2/OOfec7znne+8Vzj+kMQNA78DgByLhw2bchnA9UAKs8czV"
    "MJqyTGL8VIQfxcazp4YOv9D4XhvP2PkUPXdoU/G+Ve++B7EHMdsqogKGmXE1D5Fk7cyCIfJjTP525MRr3zhXtwsZ"
    "wAG+55prVkU++kcR/RCAWQDwjeeVq3uExko7kURUs/Bc7OL7Tx89eqKp4/kM4ADf3z94MyrfRmXAQvALvns7Dg8g"
    "qo5gQwS7e3j48MGFRpCFrtHXN/g+nDwnQpdZiEEi3hHDYhGNzBjD24dGRg6/1NR53qVXrFmz0tX1J4isNDP/Nl71"
    "C3qDiDjMTvpMuHH0yJGTzZUXIGhVn0B0pZnFaSqfYhpxZhYjulKr+kQDK0QBv2L14F51uiNxe1J1+5BuUonMQqxO"
    "d6xYPbgX8AqIBNtvkm5eNwNVaMsIKWdWMcEk2H5AtHdgcKuIbLIQUkN7pzA5E/jgzR18/GOdjE97IifphUIIiMim"
    "3oHBraqwR0SjcwuEZa5WMIyP3tbBvTtLdBYcdR/SxIMgopHCHgW7tVEhSkq6M1cNrLumjS035OlfGXHX5jzTs4am"
    "V2JJozK+VUXY0ChvNR33F6Yqxu6tBVb0OEJs7LurmOBAei6gZoYIGxSkkGbKq3ujt6Ts217E143KnLHtpjwb12eZ"
    "qRiaanslBT1fh7RsZleYng3ccVOeG9Zlma0aPhjFkrJve4m5qqHpWsA0/ZZW2Le9NJ/6nAr1irFnSwf9PY5aPSDp"
    "SSSpwY4KzFaN6wczbNvUzlwlAb0mKK75tQw7bulgqmK4FL0gPQOoUJkzPnp7kWLZUY8XpD0RLMA9dxbJREJIsTLS"
    "dJwe6nGgp+z4ndsKxHNvjnWnUKkEtmxo5zfXtjFTCamlRE1n9WG6YmzblGP9YIZK9a1oH3ujvUPZu61EtQ6aEhCk"
    "Y2dJav9920sXxNwmGN69tYO+Lkc1JTBcdgOowEzF2Lg+y7ZN7VRmz+/eIjBXM9aszrB7awczFcPp29gAIsl0Tpir"
    "Gfu2lxLw83bRvGsB7t1Rpr1NCHb2Pb80AzQFmFdIz87Iyfx0mqy2Nv7GsRF7Y7oSWNHp2LO1g/olUpxTmK0ENr8n"
    "y+bfyDI2FQgB6nFitOa7z/fbzannyHtJguBSD9TqNp/GvLfkc+PF3jcJDUsEcYJZouSKTkcmEsamPB+7o8C7VkVM"
    "T1/arUOAqE34vd8u8z+v1+ksKMGM0xOeaiXJHj4Y9dga8CJETS0MokjIRIkcISR8w0UXuG/VoF2MrenrcgSDylyg"
    "s+gYHMgQzPABrh/M0lVU5mrGtf0Z3jUQUa8bGSesvSZD5AQfoJhXIsdlER8iMDEdGvw2vD5UZ3Im0JYRxiYDPz1c"
    "JYqEODb++xdV6rERRcLI6ZgTpzyZCPJZZeRMjLcLl7sXNIAqzM4ZH9nSwSMP9NKeUUIwVnZHiSYGZBb4WbA3MQq1"
    "WnMjJlmxpdQ2TZLEgGymgajNrnGeQDGog5khKkxOe2bmjHxOeeyZMb78jbGGRyzBA1RhfCqw9cYc//xwPys6I8Yn"
    "PVGUCGJm852UnBNwC0N9qSC2UOimzc9+tgW/JdDAnXy7ksnAJx8d5SvfmqCrqBc1/kUNAOBcQl/1d0c89fAAN2/I"
    "MTEWE0Vy1WwMQgKUpYLj9ITndz8/xI9+UqGnpPhwhVnAeyh3KKcmPHf/6XGefHaCck9ECKkzuhdVvtwd8crP57jr"
    "48d48X8r9JbdJZUHcIVi18OXJNAMshmhHhvfeWEaYmP75g4MiGPSpLLeIpcZlLoivvX8JH9wYIShUzHlgiP2i1ud"
    "RRmgGYORE9rahH/9jxl+cazOHTflKRcdc9WQNpGBb6S4XLvyyNfO8MnHRgkB8jnF+2WqBIMlebqvO+Kp5yfZ+2cn"
    "eO1EjdJlWLwVI/aQywrV2Hjgr0b4878/RTGvtEVyWcovCgQvNDKRMD7t6S07nnp4gN+6McfkZMC55aWYYp/UFcNn"
    "Yu79iyFeeLVCX1c0Xxil1gvUY6PU4ZiaDez69HH+6bsTlLrdsoJjPTZKnY6XD81x+4NHOXhojpWdbsnKXxYGXBAX"
    "IkFF+M6Pp5ma9Oy6tZD0Ar51TYw16opCd8ST/zLB/QeGmZgJFHNK7K/s3VdkgKYRVJMs8YNXKrx2rM7m97ZTzCsh"
    "XLkRmoVf7OGvnzzDZx8/hQDtbbKoNJdKO2zW6Bu6HU88O8m/H6zQnteWCGgBsm3KkeE6X/z6GfJZJZNpjfIt5wOq"
    "dWPVyoj3vSeLr7WmPlBN+opfX51h49os1Zq1dCejZQZQgWotsKY/4rpVGWq11u3yxD6QKyg3rs0yV2/t5knrDKBC"
    "tQ4b12fJFZTYt26zuUE5cMt7c0kTdjVSYk0h378cQqrg67BxXZZyi43bMgPEPlAuKBvXZfF1FuWmTfBcTHjVasZ1"
    "qzJctypDtYXhpa2L/7MCLib+vTcykZDLJlTbYgycLygb12eTfYMWWUBbHf/5S7ioWdLIFMuO0XHPoTdqFLui+QYn"
    "7RDTNOM/9kZbRih2KM+9OMPuzxzngw8d4/FnxlAnFPJC7M9Pny0XDmga8e8b/UGp7BgZ89x/YJi9nz3BG8N1vIdP"
    "fHmUXZ86xsGfVSmVHc7xlu5yuXBAlzP+rbHqhZyQzymPf3Oc2x84ytPfn6JUUNozgir0lh0v/2yOXZ86zv6/GaVS"
    "M0qlhNFZ6A3LgQO6XPHvQ1LDlzojXj5UZc/+4zz06CgTM57u0tmu0SwxUpM6f+yZce76k2M88/wUxUJS9ja9waT1"
    "OHDFR2TeVKQ0SJPYG8UORQQOfPU0uz59nB/+1yw9pWSz5HzkSTOkezsdr5+oc98Xhrn/i8OcHPOUyq5hLCG0Fgeu"
    "/IhMM/43rc1SrRjZNqHUGfG9l2a484+P8YWvnsYpDYEvvT8QeyOXFbqKytPfn+L2B47y+DfHyeeUUl6YqQQGBzJc"
    "t7olOCAKNn1l8Q/XDkRcO5AhmxdGxz0PPjLMPZ8b4v/eqNFTVqTBLi+aeguJR3SXHBMznoceHWXPZ47z8qEqxZLS"
    "UVI2vLsVOGDTasarjU2NsJT4r1QDW27IUehxfO3bE+z4xDH+4bsT5LJCR06I/dJjLG4USz0lxw9fmeXD+4/z+b87"
    "xUzN2HlLnrD0CAgighmvKsiLjSi4bDmbdHlv2fH7nxviD/9yhNFxz4rOaJ5AbQXXEHujXFBU4MDXz7Dzj45yetzT"
    "152cKlsadAkgL0rvwOAHnPADkoPScrkA6DRRdLqSYEEILOvp78gJU7OByEEuq8nWuSwJu7037hRAVg5c+5/q3M0W"
    "QmAJJ8YNiBR8SsetVROmqHmA4jKHF1UN3h88OfTG+xUwU/mSJGcQlrR2QnrKN0HSWDLfaGKIqXypWVo4wPf1D/6b"
    "/JJujaQ4kstTPnxvZPjwzkYEJ6xzyIb7sHBSRCIW3Kt7Bw0vIhEWToZsuK9ZBM7fpBw9cmTYPLvNGBMRBxa/c3S3"
    "WEScGWPm2T165MhwM5qavUAA3MjI4Zck2E6MIVHX9IS3szf4BPRchDEkwXY27gy65sK7c7Pa9PT4sWxn4WkNcr2I"
    "rhMRbWCjX4B5V/MIjakiqiKijauzHxk9ceTnXOTq7MIG6Vf28vTC//9KXJ//f+pGUkaAXHRuAAAAAElFTkSuQmCC"
)

# The five-pointed star of that icon, in its 512 x 512 box.
STAR = [(256, 70), (308, 198), (446, 208), (340, 296), (374, 430),
        (256, 356), (138, 430), (172, 296), (66, 208), (204, 198)]  # fmt: skip


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

        key = r"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize"
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
    """The launcher's Ui for the window: everything goes into a queue the window reads."""

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
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.events.put(("ask", question, default, reply))
        return reply.get()


class App:
    """The window and everything on it."""

    PAGES = ("setup", "progress", "running", "error")

    def __init__(self, launcher: Any, args: Any) -> None:
        self.L = launcher
        self.args = args
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._dpi_aware()
        self.root = tk.Tk()  # raises TclError when there is no screen
        self.root.withdraw()
        self.c = LIGHT if system_uses_light_theme() else DARK
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.exit_code = 0
        self.services: Any = None
        self.group: Any = None
        self.page = ""
        self.pages: dict[str, ttk.Frame] = {}
        self._poll_busy = False
        self.opened_browser = False
        self.polling = False
        self.closing = False
        self.exited = False
        self.in_settings = False
        self.working = False
        self.sizes: dict[str, int | None] = {}  # download size per model link; None: unknown
        self.gpu: Any = None
        self.fatal = ""
        try:
            self.settings = launcher.load_settings(args)
        except launcher.LauncherError as exc:
            self.fatal = str(exc)
            self.settings = launcher.Settings(root=Path.cwd())
        self.settings.assume_yes = True  # the setup page is the question
        self.ui = GuiUi(self.events, self.settings.root / "logs" / "launcher.log")
        self.root.report_callback_exception = self._callback_failed
        self._init_window()
        self._init_fonts()
        self._init_style()
        self._build_header()
        self.body = ttk.Frame(
            self.root, padding=(self.px(28), self.px(18), self.px(28), self.px(18))
        )
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self._build_setup()
        self._build_progress()
        self._build_running()
        self._build_error()

    # -- window, fonts, colors ----------------------------------------------------------------

    @staticmethod
    def _dpi_aware() -> None:
        if sys.platform == "win32":
            import ctypes

            with contextlib.suppress(Exception):
                ctypes.windll.shcore.SetProcessDpiAwareness(1)

    def px(self, value: int) -> int:
        return int(value * self.scale)

    def _init_window(self) -> None:
        root = self.root
        root.title("Starlight")
        root.configure(bg=self.c.bg)
        with contextlib.suppress(tk.TclError):
            root.iconphoto(True, tk.PhotoImage(data="".join(ICON_PNG)))
        width, height = self.px(820), self.px(700)
        root.minsize(self.px(720), self.px(600))
        x = max((root.winfo_screenwidth() - width) // 2, 0)
        y = max((root.winfo_screenheight() - height) // 3, 0)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.protocol("WM_DELETE_WINDOW", self.quit_app)

    def _init_fonts(self) -> None:
        family = (
            "Segoe UI"
            if sys.platform == "win32"
            else tkfont.nametofont("TkDefaultFont").actual("family")
        )
        self.f = tkfont.Font(family=family, size=10)
        self.f_bold = tkfont.Font(family=family, size=10, weight="bold")
        self.f_small = tkfont.Font(family=family, size=9)
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
                        troughcolor=c.panel2, focuscolor=c.bg, font=self.f)  # fmt: skip
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
        style.map("TButton", background=[("active", c.panel), ("disabled", c.panel)],
                  foreground=[("disabled", c.dim)], bordercolor=[("active", c.accent)])  # fmt: skip
        style.configure("Accent.TButton", background=c.accent, foreground=c.on_accent,
                        bordercolor=c.accent, font=self.f_bold)  # fmt: skip
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
        c = self.c
        bar = tk.Frame(self.root, bg=c.panel, highlightthickness=0)
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=c.panel)
        inner.pack(fill="x", padx=self.px(28), pady=self.px(12))
        size = self.px(30)
        star = tk.Canvas(inner, width=size, height=size, bg=c.panel, highlightthickness=0)
        star.create_polygon(
            [coord / 512 * size for point in STAR for coord in point], fill="#f5c542", outline=""
        )
        star.pack(side="left")
        tk.Label(inner, text="Starlight", font=self.f_head, bg=c.panel, fg=c.text).pack(
            side="left", padx=(self.px(10), 0)
        )
        tk.Label(
            inner, text=f"launcher {self.L.VERSION}", font=self.f_small, bg=c.panel, fg=c.dim
        ).pack(side="right")
        tk.Frame(self.root, bg=c.border, height=1).pack(fill="x")

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

    APPROX_GB: ClassVar[dict[str, float]] = {
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
        keys = list(self.L.MODEL_PRESETS)
        if self.settings.model_url not in self.L.MODEL_PRESETS.values():
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
        if sys.platform == "win32" and not self._tesseract_installed():
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
        self.has_ocr_option = any(variable is self.ocr_var for variable, _ in rows)

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
        self.setup_cancel = self._button(buttons, "Cancel", self.on_setup_cancel)
        self.setup_cancel.grid(row=0, column=1, padx=(0, self.px(8)))
        self.setup_quit = self._button(buttons, "Quit", self.quit_app)
        self.setup_quit.grid(row=0, column=2, padx=(0, self.px(8)))
        self.setup_go = self._button(
            buttons, "Install and start", self.on_setup_go, "Accent.TButton"
        )
        self.setup_go.grid(row=0, column=3)

    def _tesseract_installed(self) -> bool:
        import shutil

        installed = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists()
        return bool(shutil.which("tesseract")) or installed

    def _current_model_key(self) -> str:
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
            title = f"Your own model: {self.L._file_name(self.settings.model_url)}"
            text = "Set as model_url in config/launcher.json."
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
        name = tk.Label(frame, text=title, font=self.f_bold, bg=c.panel, fg=c.text, anchor="w")
        name.grid(row=0, column=1, sticky="w", pady=(self.px(8), 0))
        desc = tk.Label(frame, text=text, font=self.f_small, bg=c.panel, fg=c.dim, anchor="w")
        desc.grid(row=1, column=1, sticky="w", pady=(0, self.px(8)))
        side = tk.Frame(frame, bg=c.panel)
        side.grid(row=0, column=2, rowspan=2, sticky="e", padx=self.px(12))
        meta = tk.Label(side, text="", font=self.f_small, bg=c.panel, fg=c.dim, anchor="e")
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
            card["frame"].configure(highlightbackground=self.c.accent if chosen else self.c.border)
            self._draw_mark(
                card["mark"],
                "radio" if chosen else "empty",
                self.c.accent if chosen else self.c.dim,
            )
        self._refresh_summary()

    def _refresh_cards(self, recommended: str = "") -> None:
        for key, card in self.cards.items():
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
            if key == recommended:
                pill_text = self.c.on_accent
                self._pill(card["badges"], "Recommended for your PC", self.c.accent, pill_text)
            if have:
                dark_text = "#06240f" if self.c is DARK else "#ffffff"
                self._pill(card["badges"], "Downloaded", self.c.ok, dark_text)

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
            self.events.put(("gpu", self.L.detect_gpu()))
            for key in self.L.MODEL_PRESETS:
                url = self.L.MODEL_PRESETS[key]
                if not self._model_file_of(key).is_file():
                    try:
                        self.events.put(("size", url, self.L.probe(url).size))
                    except (urllib.error.URLError, OSError, ValueError, self.L.LauncherError):
                        self.events.put(("size", url, None))

        threading.Thread(target=look, name="lookups", daemon=True).start()

    def on_gpu(self, gpu: Any) -> None:
        self.gpu = gpu
        recommended, why = self.L.recommend_model(gpu)
        if gpu is None:
            self.gpu_label.configure(text="Graphics card: not found. " + why)
        else:
            memory = f" ({gpu.vram / self.L.GB:.0f} GB video memory)" if gpu.vram else ""
            self.gpu_label.configure(text=f"Graphics card: {gpu.name}{memory}. {why}")
        self._refresh_cards(recommended if gpu is not None else "")
        first_run = not self._installed()
        if gpu is not None and first_run and not self.model_touched and recommended in self.cards:
            self.select_model(recommended)

    def on_size(self, url: str, size: int | None) -> None:
        self.sizes[url] = size
        recommended, _ = self.L.recommend_model(self.gpu)
        self._refresh_cards(recommended if self.gpu is not None else "")
        self._refresh_summary()

    def _installed(self) -> bool:
        return bool((self.settings.tools / "uv" / self.L.exe("uv")).is_file())

    def _refresh_summary(self) -> None:
        import shutil

        gb = self.L.GB
        need = 0.0
        if not self._installed():
            need += 1.6 * gb
        if self.settings.use_model:
            key = self.model_var.get()
            if not self._installed():
                need += 0.2 * gb
            if key and not self._model_file_of(key).is_file():
                known = self.sizes.get(self._model_url_of(key))
                need += known if known else self.APPROX_GB.get(key, 9.0) * gb
        if self.voice_var.get():
            need += 0.6 * gb
        try:
            free = shutil.disk_usage(self.settings.root).free
        except OSError:
            free = 10**15
        enough = free >= need + gb
        if need <= 0:
            text = "Nothing to download."
        else:
            text = (
                f"Needs about {need / gb:.1f} GB of free space; "
                f"this drive has {free / gb:.0f} GB free."
            )
        if not enough:
            text += " That is not enough: free some space first."
        self.summary.configure(text=text, foreground=self.c.dim if enough else self.c.bad)
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

    def on_setup_cancel(self) -> None:
        self.show("running")

    def on_setup_go(self) -> None:
        L, settings = self.L, self.settings
        key = self.model_var.get()
        choices: dict[str, Any] = {
            "variant": "vulkan" if self.gpu_var.get() else "cpu",
            "voice": self.voice_var.get(),
            "viewer": self.viewer_var.get(),
        }
        drop: tuple[str, ...] = ()
        if settings.use_model and key != "custom":
            settings.model_url = L.MODEL_PRESETS[key]
            choices["model"] = key
            drop = ("model_url",)
        settings.variant = choices["variant"]
        settings.voice = choices["voice"]
        settings.viewer = choices["viewer"]
        if self.has_ocr_option:
            settings.tesseract = self.ocr_var.get()
            choices["tesseract"] = settings.tesseract
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
        self.p_title = self._label(page, "", "Big.TLabel", wraplength=self.px(740), justify="left")
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
        toggle = ttk.Checkbutton(
            details, text="Show details", variable=self.details_open, command=self._toggle_details
        )
        toggle.grid(row=0, column=0, sticky="w")
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
        if self.step_rows:
            glyph, label = self.step_rows[-1]
            self._draw_mark(glyph, "done", self.c.ok)
            label.configure(fg=self.c.dim)
        self._step_row(text)
        self.p_title.configure(text=text)
        self.p_sub.configure(text="")
        self.bar.configure(mode="indeterminate")
        self.bar.start(14)

    def on_progress(self, done: int, total: int | None, started: float) -> None:
        fraction, text = self.L.transfer_text(done, total, started)
        self.p_sub.configure(text=text)
        if fraction < 0:
            return
        self.bar.stop()
        self.bar.configure(mode="determinate", value=fraction * 1000)

    def on_waiting(self, text: str) -> None:
        self.p_sub.configure(text=text)

    def on_say(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 1500:
            self.log_text.delete("1.0", f"{lines - 1500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_ask(self, question: str, default: bool, reply: queue.Queue[bool]) -> None:
        answer = messagebox.askyesno("Starlight", question, default="yes" if default else "no")
        reply.put(bool(answer))

    # -- starting -------------------------------------------------------------------------------

    def needs_setup_page(self) -> bool:
        if not self._installed():
            return True
        return bool(self.settings.use_model and not self.settings.model_file.is_file())

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
            self.services = services
            self.events.put(("running",))
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

    def on_begin(self) -> None:
        self.begin()

    def on_running(self) -> None:
        self.show("running")
        self.polling = True
        self._poll_tick()

    def _poll_tick(self) -> None:
        if self.closing or not self.polling:
            return
        if self.services is not None and not self._poll_busy:
            self._poll_busy = True
            threading.Thread(target=self._poll_once, name="poll", daemon=True).start()
        self.root.after(1500, self._poll_tick)

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
        self.r_sub = self._label(page, "", "Dim.TLabel", wraplength=self.px(740), justify="left")
        self.r_sub.grid(row=1, column=0, sticky="w", pady=(self.px(2), self.px(14)))

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
                card, text=name, font=self.f_bold, bg=c.panel, fg=c.text, anchor="w", width=11
            ).grid(row=index, column=0, sticky="w", padx=(self.px(16), 0), pady=pad)
            dot = tk.Canvas(
                card, width=self.px(12), height=self.px(12), bg=c.panel, highlightthickness=0
            )
            oval = dot.create_oval(1, 1, self.px(11), self.px(11), fill=c.dim, outline="")
            dot.grid(row=index, column=1, padx=self.px(8), pady=pad)
            text = tk.Label(card, text="", font=self.f, bg=c.panel, fg=c.text, anchor="w")
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
        self.r_footer = self._label(footer, "", "Small.TLabel")
        self.r_footer.grid(row=0, column=0, sticky="w")
        self._button(footer, "Settings ...", self.open_settings).grid(
            row=0, column=1, padx=(0, self.px(8))
        )
        self._button(footer, "Quit Starlight", self.quit_app).grid(row=0, column=2)

    def _set_row(self, name: str, color: str, text: str) -> None:
        dot, oval, label = self.status_rows[name]
        dot.itemconfigure(oval, fill=color)
        label.configure(text=text)

    def on_status(self, status: Any) -> None:
        L, c = self.L, self.c
        self.last_status = status
        model_name = self.settings.model_file.stem if self.settings.use_model else ""
        elapsed = L.format_duration(status.seconds)
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
        chat_url = f"http://127.0.0.1:{L.WEB_PORT}"
        viewer_url = f"http://127.0.0.1:{L.VIEWER_PORT}"
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
        self.r_title.configure(text=title)
        self.r_sub.configure(text=sub)
        self.r_footer.configure(text=str(self.settings.root))
        ready = status.chat and status.model in ("ready", "off")
        if ready and not self.opened_browser and self.settings.web:
            self.opened_browser = True
            self.open_chat()

    def open_chat(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.L.WEB_PORT}")

    def open_stats(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.L.VIEWER_PORT}")

    def open_logs(self) -> None:
        open_folder(self.settings.root / "logs")

    def toggle_kill(self) -> None:
        status = getattr(self, "last_status", None)
        if status is None or status.kill is None:
            return
        engage = not status.kill
        threading.Thread(
            target=lambda: self.services.set_kill_switch(engage), name="kill", daemon=True
        ).start()

    def restart_model(self) -> None:
        if self.services is None:
            return

        def restart() -> None:
            if not self.services.restart_model():
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
                    self.L.starlight(uv, "doctor"), cwd=root, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=120, creationflags=flags,
                )  # fmt: skip
                text = (done.stdout + done.stderr).strip()
            except (OSError, subprocess.SubprocessError) as exc:
                text = f"The check could not run: {exc}"
            self.events.put(("doctor", text or "No output."))

        threading.Thread(target=check, name="doctor", daemon=True).start()

    def on_doctor(self, text: str) -> None:
        c = self.c
        window = tk.Toplevel(self.root)
        window.title("Starlight check")
        window.configure(bg=c.bg)
        window.geometry(f"{self.px(760)}x{self.px(420)}")
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
        self.err_message = self._label(page, "", "TLabel", wraplength=self.px(740), justify="left")
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
        self._button(
            buttons, "Copy details", lambda: self._copy(self.err_text.get("1.0", "end"))
        ).grid(row=0, column=2, padx=self.px(6))
        self._button(buttons, "Quit", self.quit_app).grid(row=0, column=3, padx=self.px(6))
        self._button(buttons, "Try again", self.begin, "Accent.TButton").grid(row=0, column=4)

    def on_error(self, message: str, details: str = "") -> None:
        self.polling = False
        self.err_message.configure(text=message)
        log_tail = ""
        with contextlib.suppress(OSError):
            lines = (
                (self.settings.root / "logs" / "launcher.log")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            log_tail = "\n".join(lines[-25:])
        report = "\n\n".join(
            part for part in (message, details, log_tail and "Recent log:\n" + log_tail) if part
        )
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
        """Handle what the worker threads reported."""
        try:
            while True:
                event = self.events.get_nowait()
                handler = getattr(self, f"on_{event[0]}", None)
                if handler is not None:
                    handler(*event[1:])
        except queue.Empty:
            pass
        if not self.exited:
            self.root.after(60, self._pump)

    def run(self) -> int:
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
        return self.exit_code

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
        self.show("running")
        for model in ("loading", "ready", "restarting", "down", "off"):
            self.on_status(L.Status(model=model, chat=True, viewer=True, kill=False, seconds=75))
        self.on_status(L.Status(model="ready", chat=True, viewer=None, kill=True))
        self.on_error("A test error", "details")
        self.root.update()
        self.root.destroy()
        print("gui ok", flush=True)
        return 0


def run(launcher: Any, args: Any) -> int | None:
    """Show the window. None: there is no screen to show it on."""
    try:
        app = App(launcher, args)
    except tk.TclError:
        return None
    if getattr(args, "gui_selftest", False):
        return app.selftest()
    return app.run()
