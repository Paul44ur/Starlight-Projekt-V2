"""A friendlier terminal chat, built on ``rich``.

The web pages' colours (``termstyle``), a banner and a status line (the model, the kill
switch, practice mode, a private conversation; the modes also show in the prompt), a spinner
until the first word of an answer, answers in light Markdown as they stream in, wrapped to the
terminal, and clear approve/deny questions. If ``rich`` is missing or the output is not a
real terminal (piped, redirected), everything falls back to plain text, so scripts and pipes
behave exactly as before. NO_COLOR keeps the layout and drops the colours.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from starlight import console, termstyle

if TYPE_CHECKING:
    from starlight.core.dispatcher import Confirmer
    from starlight.core.permissions import PermissionPolicy
    from starlight.core.tools import Tool


def _rich_available() -> bool:
    try:
        import rich  # noqa: F401
    except ImportError:
        return False
    return console.interactive()


STAR = "✦"  # the logo's four-pointed star, in its gold
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_AFTER = 0.2  # seconds: a quick answer needs no spinner
MAX_WIDTH = 100  # answers wrap at the terminal's width, but no wider than this (to read well)
CLEAR_LINE = "\r\x1b[K"


class ChatView:
    """How the chat looks. Also the UserIO the core asks through, so confirmations,
    questions and notices match the rest of the chat."""

    def __init__(self, assume_yes: bool = False) -> None:
        self.assume_yes = assume_yes
        self.conversation: str | None = None  # a private one (/private); None: the current
        self.model = ""  # for the status line
        self.modes: dict[str, bool] = {}  # stopped, practice, private: the status line, the prompt
        self.rich = _rich_available()
        self._console: Any = None
        self.palette = termstyle.PLAIN
        self._colours = _styles(termstyle.COLOURS["dark"])  # rich styles for the web's colours
        self._stream: termstyle.MarkdownStream | None = None
        self._waiting = False  # a request went out and its first word has not come yet
        self._since = 0.0  # when it went out
        self._tool = ""  # the tool running now (the spinner names it)
        self._spinner: asyncio.Task[None] | None = None
        self._spun = False  # the spinner is on the screen
        if self.rich:
            from rich.console import Console

            light = termstyle.wants_light()
            colours = termstyle.COLOURS["light" if light else "dark"]
            self._colours = _styles(colours)
            self._console = Console(highlight=False, soft_wrap=False, theme=_theme(colours))
            self.palette = termstyle.palette(
                self._console.color_system, self._console.no_color, light
            )

    # -- the UserIO protocol (confirmations, 2FA codes, notices) --------------------------

    async def confirm(self, question: str) -> bool:
        if self.assume_yes:
            return True
        if not console.interactive():
            return False
        with self._aside():
            self._print_question(question)
            hint = self._style("  [y]es / [n]o: ", "warn")
            answer = (await console.lines.read(console.answer, hint)).strip().lower()
        return answer in console.YES

    async def _permission(self, question: str) -> str:
        with self._aside():
            self._print_question(question)
            hint = self._style("  [y]es / [n]o / [a]lways this session: ", "warn")
            answer = (await console.lines.read(console.answer, hint)).strip().lower()
        if answer in ("a", "always"):
            return "always"
        return "yes" if answer in console.YES else "no"

    async def ask(self, question: str, secret: bool = False) -> str | None:
        if not console.interactive():
            return None
        import getpass

        with self._aside():
            if secret:
                console.drop_typeahead()
                return await console.lines.read(
                    getpass.getpass, self._style(f"\n{question}: ", "warn")
                )
            answer = await console.lines.read(
                console.answer, self._style(f"\n{question}: ", "warn")
            )
        return answer.strip() or None

    async def notify(self, text: str) -> None:
        with self._aside():
            if self.rich:
                self._console.print(f"[{self._colours['dim']}]ℹ {_escape(text)}[/]")  # noqa: RUF001
            else:
                print(f"\n[starlight] {text}")

    # -- the tool-permission question (adds "always this session") -----------------------

    def permission_confirmer(self, policy: PermissionPolicy) -> Confirmer:
        async def confirm(tool: Tool, args: dict[str, Any]) -> bool:
            if self.assume_yes:
                return True
            if not console.interactive():
                return False
            import json

            shown = json.dumps(args, default=str)
            question = f"Starlight wants to run {tool.qualified_name}({shown})"
            result = await self._permission(question)
            if result == "always":
                policy.allow_for_session(tool.qualified_name)
                return True
            return result == "yes"

        return confirm

    # -- chat rendering ------------------------------------------------------------------

    def banner(self, version: str, info: dict[str, str], hotkey: str | None) -> None:
        self._quiet()
        if not self.rich:
            print(f"Starlight {version}. Type /help for commands, /quit to exit.")
            print("Ctrl+C stops an answer while it is being written.")
            for key, value in info.items():
                print(f"  {key}: {value}")
            if self.model:
                print(self._status_text())
            return
        from rich.panel import Panel
        from rich.table import Table

        c = self._colours
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=c["dim"], justify="right")
        grid.add_column()
        for key, value in info.items():
            grid.add_row(_escape(key), _escape(value))
        self._console.print(
            Panel(
                grid,
                title=f"[{c['star']}]{STAR}[/] [bold]Starlight {version}[/bold]",
                subtitle=f"[{c['dim']}]/help for commands · /status · /quit to leave[/]",
                border_style=c["accent"],
            )
        )
        if self.model:
            self.status()
        stop = "Ctrl+C stops an answer · "
        stop += f"{hotkey or 'the hotkey'} or a screen corner stops everything"
        self._console.print(f"[{c['dim']}]{stop}[/]\n")

    def status(self) -> None:
        """One line: the model, and whether the kill switch, practice mode and a private
        conversation are on (/status shows it again)."""
        self._quiet()
        if not self.rich:
            print(self._status_text())
            return
        p = self.palette
        parts = [p.paint("●", p.ok) + " " + p.paint(self.model or "?", p.bold)]
        stopped = self.modes.get("stopped", False)
        parts.append(
            p.paint("kill switch ", p.dim)
            + (p.paint("ON (/resume)", p.bad, p.bold) if stopped else p.paint("off", p.dim))
        )
        practice = self.modes.get("practice", False)
        parts.append(
            p.paint("practice ", p.dim)
            + (p.paint("on", p.warn, p.bold) if practice else p.paint("off", p.dim))
        )
        if self.modes.get("private"):
            parts.append(p.paint("private conversation", p.accent))
        sys.stdout.write(p.paint("  ·  ", p.dim).join(parts) + "\n")
        sys.stdout.flush()

    def _status_text(self) -> str:
        on = {True: "on", False: "off"}
        text = f"model: {self.model or '?'} | kill switch: {on[self.modes.get('stopped', False)]}"
        text += f" | practice: {on[self.modes.get('practice', False)]}"
        if self.modes.get("private"):
            text += " | private conversation"
        return text

    def prompt(self) -> str:
        """``you ›``, with the modes that are on (stopped, practice, private) in between."""  # noqa: RUF002
        if not self.rich:
            return "you> "
        p = self.palette
        marks = []
        if self.modes.get("stopped"):
            marks.append(p.paint("stopped", p.bad))
        if self.modes.get("practice"):
            marks.append(p.paint("practice", p.warn))
        if self.modes.get("private"):
            marks.append(p.paint("private", p.dim))
        if not marks:  # (one piece: the prompt stays findable in the output, as before)
            return p.paint("you ›", p.bold, p.accent) + " "  # noqa: RUF001
        between = "".join(p.paint(" · ", p.dim) + mark for mark in marks)
        return p.paint("you", p.bold, p.accent) + between + " " + p.paint("›", p.accent) + " "  # noqa: RUF001

    async def aread(self) -> str:
        """The next request, read without stopping Starlight's background work."""
        return (await console.lines.read(console.read_request, self.prompt())).strip()

    def on_token(self) -> Callable[[str], None]:
        """A callback that streams the answer live, after the star; until its first word a
        spinner shows that Starlight is at work (and which tool runs)."""
        self._stream = None
        started = [False]
        self._wait()

        def emit(token: str) -> None:
            if not started[0]:
                started[0] = True
                self._waiting = False
                self._quiet()
                if self.rich:
                    sys.stdout.write(self.palette.paint(STAR, self.palette.star) + " ")
                    self._stream = termstyle.MarkdownStream(
                        self.palette, _width(), indent=2, column=2
                    )
                else:
                    sys.stdout.write("starlight> ")
            sys.stdout.write(self._stream.feed(token) if self._stream else token)
            sys.stdout.flush()

        return emit

    def on_tool(self, report: dict[str, Any]) -> None:
        """A tool started or ended (the dispatcher's report): the spinner says which."""
        self._tool = str(report.get("tool") or "") if report.get("state") == "running" else ""

    def end_answer(self, streamed: bool) -> None:
        self._waiting = False
        self._quiet()
        if streamed:
            rest = self._stream.close() if self._stream else ""
            sys.stdout.write(rest + (self.palette.reset if self.rich else "") + "\n")
            sys.stdout.flush()
        self._stream = None

    def answer(self, text: str) -> None:
        """Print a full answer that was not streamed (e.g. the test model)."""
        self._waiting = False
        self._quiet()
        if self.rich:
            stream = termstyle.MarkdownStream(self.palette, _width(), indent=2, column=2)
            star = self.palette.paint(STAR, self.palette.star) + " "
            sys.stdout.write(star + stream.feed(text) + stream.close() + "\n")
            sys.stdout.flush()
        else:
            print(f"starlight> {text}")

    def info(self, text: str) -> None:
        with self._aside():
            if self.rich:
                self._console.print(f"[{self._colours['dim']}]{_escape(text)}[/]")
            else:
                print(text)

    def error(self, text: str) -> None:
        with self._aside():
            if self.rich:
                self._console.print(f"[bold {self._colours['bad']}]{_escape(text)}[/]")
            else:
                print(text)

    def tools(self, tools: list[Tool]) -> None:
        self._quiet()
        if not self.rich:
            for t in tools:
                summary = t.description.splitlines()[0] if t.description else ""
                print(f"  {t.qualified_name:<24} [{t.permission.value}] {summary}")
            return
        from rich.table import Table

        c = self._colours
        table = Table(box=None, pad_edge=False, header_style=f"bold {c['dim']}")
        table.add_column("tool", style=c["accent"])
        table.add_column("when")
        table.add_column("what")
        colour = {"safe": c["ok"], "confirm": c["warn"], "blocked": c["bad"]}
        for t in tools:
            summary = t.description.splitlines()[0] if t.description else ""
            perm = t.permission.value
            table.add_row(
                t.qualified_name, f"[{colour.get(perm, 'white')}]{perm}[/]", _escape(summary)
            )
        self._console.print(table)

    def help(self, text: str) -> None:
        self._quiet()
        if self.rich:
            from rich.markdown import Markdown

            self._console.print(Markdown(text))
        else:
            print(text)

    # -- the spinner ---------------------------------------------------------------------

    def _wait(self) -> None:
        """A request went out: show the spinner until its first word."""
        self._waiting, self._since, self._tool = True, time.monotonic(), ""
        self._spin()

    def _spin(self) -> None:
        if not self.rich or self._spinner is not None:
            return
        try:
            self._spinner = asyncio.get_running_loop().create_task(self._spinning())
        except RuntimeError:  # (no event loop: nothing to animate with)
            self._spinner = None

    async def _spinning(self) -> None:
        await asyncio.sleep(SPIN_AFTER)
        p = self.palette
        frame = 0
        while self._waiting:
            seconds = int(time.monotonic() - self._since)
            text = "Starlight is working…" + (f" {seconds} s" if seconds else "")
            if self._tool:
                text += f" · {self._tool}"
            text = text[: max(10, _width() - 3)]  # one line: \r must reach its start
            mark = p.paint(SPINNER[frame % len(SPINNER)], p.accent)
            sys.stdout.write(CLEAR_LINE + mark + " " + p.paint(text, p.dim))
            sys.stdout.flush()
            self._spun = True
            frame += 1
            await asyncio.sleep(0.1)

    def _quiet(self) -> None:
        """Take the spinner away (before anything else is written)."""
        if self._spinner is not None:
            self._spinner.cancel()
            self._spinner = None
        if self._spun:
            sys.stdout.write(CLEAR_LINE)
            sys.stdout.flush()
            self._spun = False

    @contextlib.contextmanager
    def _aside(self) -> Iterator[None]:
        """Something else is shown (a notice, a question): the spinner steps aside, and comes
        back afterwards if the answer still has not begun."""
        self._quiet()
        try:
            yield
        finally:
            if self._waiting:
                self._spin()

    # -- helpers -------------------------------------------------------------------------

    def _print_question(self, question: str) -> None:
        if self.rich:
            from rich.panel import Panel

            # Text, not markup: a command such as `del C:\\Backup\\[old]\\*.zip` must be shown
            # exactly as it will run (and "[/" in code must not break the chat).
            question_text = _plain_text(question)
            warn = self._colours["warn"]
            self._console.print(
                Panel(question_text, border_style=warn, title=f"[{warn}]confirm[/]")
            )
        else:
            print(f"\n{question}")

    def _style(self, text: str, colour: str) -> str:
        """A question's hint, bold and in a colour of the palette (when it is a terminal)."""
        if not console.interactive():
            return text
        return self.palette.paint(text, self.palette.bold, getattr(self.palette, colour))


def _styles(colours: dict[str, str]) -> dict[str, str]:
    return {name: colours[name] for name in ("accent", "star", "dim", "ok", "warn", "bad")}


def _width() -> int:
    return min(shutil.get_terminal_size((MAX_WIDTH, 24)).columns - 1, MAX_WIDTH)


def _theme(colours: dict[str, str]) -> Any:
    """rich's Markdown (/help) in the web's colours."""
    from rich.theme import Theme

    accent, dim = colours["accent"], colours["dim"]
    return Theme(
        {
            "markdown.h1": f"bold {accent}",
            "markdown.h1.border": accent,
            "markdown.h2": f"bold {accent}",
            "markdown.h3": f"bold {accent}",
            "markdown.code": f"{colours['code']} on {colours['code_bg']}",
            "markdown.item.bullet": accent,
            "markdown.item.number": accent,
            "markdown.link": f"underline {accent}",
            "markdown.link_url": dim,
            "markdown.hr": dim,
            "markdown.block_quote": dim,
        }
    )


def _escape(text: str) -> str:
    """Text from tools, the model or the user, safe inside rich markup."""
    from rich.markup import escape

    return escape(text)


def _plain_text(text: str) -> Any:
    from rich.text import Text

    return Text(text)
