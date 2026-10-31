"""A friendlier terminal chat, built on ``rich``.

A coloured banner and status line, a clear input prompt, nicely rendered answers and
tables, and clear approve/deny questions. If ``rich`` is missing or the output is not a
real terminal (piped, redirected), everything falls back to plain text, so scripts and
pipes behave exactly as before.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from starlight import console

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


# ANSI colours for the input prompt (rich renders output, but input stays plain so that
# pasting several lines and dropping type-ahead before a question keep working).
USER_PROMPT = "\x1b[1;36myou ›\x1b[0m "  # noqa: RUF001
ANSWER = "\x1b[32m"
RESET = "\x1b[0m"


BOLD_YELLOW = "1;33"  # the terminal (ANSI SGR) code questions are shown in


class ChatView:
    """How the chat looks. Also the UserIO the core asks through, so confirmations,
    questions and notices match the rest of the chat."""

    def __init__(self, assume_yes: bool = False) -> None:
        self.assume_yes = assume_yes
        self.conversation: str | None = None  # a private one (/private); None: the current
        self.rich = _rich_available()
        self._console: Any = None
        if self.rich:
            from rich.console import Console

            self._console = Console(highlight=False, soft_wrap=False)

    # -- the UserIO protocol (confirmations, 2FA codes, notices) --------------------------

    async def confirm(self, question: str) -> bool:
        if self.assume_yes:
            return True
        if not console.interactive():
            return False
        self._print_question(question)
        hint = self._style("  [y]es / [n]o: ", BOLD_YELLOW)
        answer = (await console.lines.read(console.answer, hint)).strip().lower()
        return answer in console.YES

    async def _permission(self, question: str) -> str:
        self._print_question(question)
        hint = self._style("  [y]es / [n]o / [a]lways this session: ", BOLD_YELLOW)
        answer = (await console.lines.read(console.answer, hint)).strip().lower()
        if answer in ("a", "always"):
            return "always"
        return "yes" if answer in console.YES else "no"

    async def ask(self, question: str, secret: bool = False) -> str | None:
        if not console.interactive():
            return None
        import getpass

        if secret:
            console.drop_typeahead()
            return await console.lines.read(
                getpass.getpass, self._style(f"\n{question}: ", BOLD_YELLOW)
            )
        answer = await console.lines.read(
            console.answer, self._style(f"\n{question}: ", BOLD_YELLOW)
        )
        return answer.strip() or None

    async def notify(self, text: str) -> None:
        if self.rich:
            self._console.print(f"[dim]ℹ {_escape(text)}[/dim]")  # noqa: RUF001
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
        if not self.rich:
            print(f"Starlight {version}. Type /help for commands, /quit to exit.")
            print("Ctrl+C stops an answer while it is being written.")
            for key, value in info.items():
                print(f"  {key}: {value}")
            return
        from rich.panel import Panel
        from rich.table import Table

        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", justify="right")
        grid.add_column()
        for key, value in info.items():
            grid.add_row(_escape(key), _escape(value))
        self._console.print(
            Panel(
                grid,
                title=f"[bold]⭐ Starlight {version}[/bold]",
                subtitle="[dim]/help for commands · /quit to leave[/dim]",
                border_style="blue",
            )
        )
        stop = "Ctrl+C stops an answer · "
        stop += f"{hotkey or 'the hotkey'} or a screen corner stops everything"
        self._console.print(f"[dim]{stop}[/dim]\n")

    async def aread(self) -> str:
        """The next request, read without stopping Starlight's background work."""
        prompt = USER_PROMPT if self.rich else "you> "
        return (await console.lines.read(console.read_request, prompt)).strip()

    def on_token(self) -> Any:
        """A callback that streams the answer live under a 'starlight' label."""
        started = [False]

        def emit(token: str) -> None:
            if not started[0]:
                started[0] = True
                if self.rich:
                    sys.stdout.write(f"\x1b[1;32mstarlight ›\x1b[0m {ANSWER}")  # noqa: RUF001
                else:
                    sys.stdout.write("starlight> ")
            sys.stdout.write(token)
            sys.stdout.flush()

        return emit

    def end_answer(self, streamed: bool) -> None:
        if streamed:
            sys.stdout.write(RESET + "\n")
            sys.stdout.flush()

    def answer(self, text: str) -> None:
        """Print a full answer that was not streamed (e.g. the test model)."""
        if self.rich:
            from rich.markdown import Markdown

            self._console.print("[bold green]starlight ›[/bold green]", end=" ")  # noqa: RUF001
            self._console.print(Markdown(text) if _looks_markdown(text) else _plain_text(text))
        else:
            print(f"starlight> {text}")

    def info(self, text: str) -> None:
        if self.rich:
            self._console.print(f"[dim]{_escape(text)}[/dim]")
        else:
            print(text)

    def error(self, text: str) -> None:
        if self.rich:
            self._console.print(f"[bold red]{_escape(text)}[/bold red]")
        else:
            print(text)

    def tools(self, tools: list[Tool]) -> None:
        if not self.rich:
            for t in tools:
                summary = t.description.splitlines()[0] if t.description else ""
                print(f"  {t.qualified_name:<24} [{t.permission.value}] {summary}")
            return
        from rich.table import Table

        table = Table(box=None, pad_edge=False)
        table.add_column("tool", style="cyan")
        table.add_column("when", style="yellow")
        table.add_column("what")
        colour = {"safe": "green", "confirm": "yellow", "blocked": "red"}
        for t in tools:
            summary = t.description.splitlines()[0] if t.description else ""
            perm = t.permission.value
            table.add_row(
                t.qualified_name, f"[{colour.get(perm, 'white')}]{perm}[/]", _escape(summary)
            )
        self._console.print(table)

    def help(self, text: str) -> None:
        if self.rich:
            from rich.markdown import Markdown

            self._console.print(Markdown(text))
        else:
            print(text)

    # -- helpers -------------------------------------------------------------------------

    def _print_question(self, question: str) -> None:
        if self.rich:
            from rich.panel import Panel

            # Text, not markup: a command such as `del C:\\Backup\\[old]\\*.zip` must be shown
            # exactly as it will run (and "[/" in code must not break the chat).
            question_text = _plain_text(question)
            self._console.print(
                Panel(question_text, border_style="yellow", title="[yellow]confirm[/]")
            )
        else:
            print(f"\n{question}")

    @staticmethod
    def _style(text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if console.interactive() else text


def _escape(text: str) -> str:
    """Text from tools, the model or the user, safe inside rich markup."""
    from rich.markup import escape

    return escape(text)


def _plain_text(text: str) -> Any:
    from rich.text import Text

    return Text(text)


def _looks_markdown(text: str) -> bool:
    marks = ("```", "\n- ", "\n* ", "\n#", "**", "| ")
    return any(m in text for m in marks)
