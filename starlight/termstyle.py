"""How the terminal chat looks: the web pages' colours, and light Markdown for answers while
they stream in.

The colours are the web's (``starlight/web/static/style.css``): the accent, the gold of the
star, a softer grey, and green, amber and red. They are written as ANSI codes at the depth the
terminal has (24-bit, 256 or the basic 16), for a dark terminal or, where it says so, a light
one. ``NO_COLOR``, a dumb terminal or output that is not a terminal get no colour.

``MarkdownStream`` turns an answer's Markdown into terminal text a piece at a time, so it shows
while the model writes: headings, **bold**, *italic*, ``code``, lists, quotes, rules, code
blocks and tables, wrapped between words to the width of the terminal.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

ESC = "\x1b["

# The web's colours (style.css), for a dark and a light terminal. "code" is a code span's
# text and its background.
COLOURS: dict[str, dict[str, str]] = {
    "dark": {
        "accent": "#a99bff", "star": "#f5c542", "dim": "#9aa3b2", "code": "#e6e8ec",
        "code_bg": "#262b36", "ok": "#3fb950", "warn": "#d29922", "bad": "#f85149",
    },
    "light": {
        "accent": "#5543d1", "star": "#b7791f", "dim": "#5a6372", "code": "#1f2328",
        "code_bg": "#eceff3", "ok": "#1a7f37", "warn": "#9a6700", "bad": "#cf222e",
    },
}  # fmt: skip
# The nearest of the basic 16 colours, for terminals that have no more (SGR codes).
BASIC: dict[str, dict[str, str]] = {
    "dark": {"accent": "94", "star": "93", "dim": "90", "code": "36", "ok": "32", "warn": "33",
             "bad": "91"},
    "light": {"accent": "34", "star": "33", "dim": "90", "code": "36", "ok": "32", "warn": "33",
              "bad": "31"},
}  # fmt: skip


@dataclass(frozen=True)
class Palette:
    """ANSI codes for each part of the chat (empty strings: no colour)."""

    accent: str = ""
    star: str = ""
    dim: str = ""
    code: str = ""
    ok: str = ""
    warn: str = ""
    bad: str = ""
    bold: str = ""
    italic: str = ""
    reset: str = ""
    light: bool = False

    def paint(self, text: str, *styles: str) -> str:
        codes = "".join(styles)
        return f"{codes}{text}{self.reset}" if codes else text


PLAIN = Palette()  # not a terminal: no codes at all


def wants_light(environ: Mapping[str, str] | None = None) -> bool:
    """A light terminal: STARLIGHT_TERMINAL_THEME=light, or a terminal that says its
    background is white (COLORFGBG "0;15"). Otherwise dark, as most terminals are."""
    env = os.environ if environ is None else environ
    choice = env.get("STARLIGHT_TERMINAL_THEME", "").strip().lower()
    if choice in ("light", "dark"):
        return choice == "light"
    background = env.get("COLORFGBG", "").rsplit(";", 1)[-1].strip()
    return background in ("7", "15")


def palette(color_system: str | None, no_color: bool = False, light: bool = False) -> Palette:
    """The codes for a terminal with this many colours (rich's ``color_system``: truecolor,
    256, standard or windows; None: no colour). Bold and italic stay without colour too."""
    styles = {"bold": f"{ESC}1m", "italic": f"{ESC}3m", "reset": f"{ESC}0m"}
    if color_system is None or no_color:
        return Palette(**styles, light=light)
    theme = "light" if light else "dark"
    colours = COLOURS[theme]
    if color_system in ("truecolor", "256"):
        fg = _truecolor if color_system == "truecolor" else _xterm
        bg = _truecolor_bg if color_system == "truecolor" else _xterm_bg
        found = {name: f"{ESC}{fg(colours[name])}m" for name in BASIC[theme]}
        found["code"] = f"{ESC}{fg(colours['code'])};{bg(colours['code_bg'])}m"
    else:
        found = {name: f"{ESC}{code}m" for name, code in BASIC[theme].items()}
    return Palette(**found, **styles, light=light)


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _truecolor(colour: str) -> str:
    return "38;2;{};{};{}".format(*_rgb(colour))


def _truecolor_bg(colour: str) -> str:
    return "48;2;{};{};{}".format(*_rgb(colour))


def xterm256(colour: str) -> int:
    """The nearest of the 256 xterm colours (the 6x6x6 cube or the 24 greys)."""
    r, g, b = _rgb(colour)
    steps = (0, 95, 135, 175, 215, 255)

    def nearest(value: int) -> int:
        return min(range(6), key=lambda i: abs(steps[i] - value))

    ri, gi, bi = nearest(r), nearest(g), nearest(b)
    grey = min(23, max(0, (round((r + g + b) / 3) - 8) // 10))

    def distance(other: tuple[int, int, int]) -> int:
        return sum((x - y) ** 2 for x, y in zip(other, (r, g, b), strict=True))

    cube = (steps[ri], steps[gi], steps[bi])
    grey_value = 8 + 10 * grey
    if distance(cube) <= distance((grey_value, grey_value, grey_value)):
        return 16 + 36 * ri + 6 * gi + bi
    return 232 + grey


def _xterm(colour: str) -> str:
    return f"38;5;{xterm256(colour)}"


def _xterm_bg(colour: str) -> str:
    return f"48;5;{xterm256(colour)}"


def width_of(text: str) -> int:
    """Columns the text takes: wide characters (CJK, most emoji) take two, marks none."""
    total = 0
    for char in text:
        if unicodedata.combining(char) or char in "​‍️":
            continue
        total += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return total


# -- light Markdown, a piece at a time -------------------------------------------------------

_MARKS = re.compile(r"[ \t]*[#>*+\-_`~0-9.)|]*")  # a line start that may still be a list, ...
_FENCE = re.compile(r"[ \t]*(`{3,}|~{3,})[ \t]*([\w+#.-]*)")
_HEADING = re.compile(r"[ \t]{0,3}(#{1,6})[ \t]+")
_RULE = re.compile(r"[ \t]*([-*_])([ \t]*\1){2,}[ \t]*")
_RULE_SO_FAR = re.compile(r"[ \t]*([-*_])([ \t]*\1)*[ \t]*")  # "- -", "* *": maybe a rule
# [text](address): shown as "text (address)", the address dimmed (between these two marks)
_LINK = re.compile(r"\[([^\]\n]{1,300})\]\(([^)\s]{1,800})\)")
_LINK_SO_FAR = re.compile(r"\[[^\]\n]{0,300}(\](\([^)\s]{0,800})?)?")  # (a link not ended yet)
DIM_ON, DIM_OFF = "\ue000", "\ue001"
_BULLET = re.compile(r"([ \t]*)[-*+][ \t]+")
_NUMBER = re.compile(r"([ \t]*)(\d{1,3})[.)][ \t]+")
_QUOTE = re.compile(r"[ \t]{0,3}>[ \t]?")
_DELIMITER = re.compile(r":?-{1,}:?")
_ESCAPABLE = set("\\`*_#[]()>-+.!|~{}")


class MarkdownStream:
    """Terminal text for Markdown that arrives in pieces: ``feed`` returns what can be shown
    now, ``close`` the rest. Words wrap at ``width``; every line starts ``indent`` columns in
    (the first goes on after ``column``, where a label left the cursor)."""

    def __init__(self, palette: Palette, width: int, indent: int = 0, column: int = 0) -> None:
        self.p = palette
        self.width = max(24, width)
        self.indent = indent
        self.col = column
        self._buf = ""
        self._out: list[str] = []
        self._line_start = True
        self._fence: str | None = None  # inside a code block: the fence that ends it
        self._code_line = False  # writing a code block's line
        self._table: list[str] = []  # a table's lines, drawn when it ends
        self._line_style = ""  # a heading's or a quote's look, for its whole line
        self._lead = ""  # what a wrapped line starts with (spaces, a quote's bar)
        self._lead_width = indent
        self._bold = self._italic = self._code = self._dim = False
        self._word: list[str] = []  # the word being collected (text and style codes)
        self._word_width = 0
        self._word_style = ""  # the look the word begins in (to start a wrapped line in)
        self._space = False  # a space is due before the next word
        self._prev = " "  # the last character of the line so far

    # -- the outside ---------------------------------------------------------------------

    def feed(self, text: str) -> str:
        self._buf += text.replace("\r", "")
        self._run(final=False)
        return self._take()

    def close(self) -> str:
        self._run(final=True)
        if self._table:
            self._draw_table()
        self._end_word()
        if self._styled():
            self._out.append(self.p.reset)
        return self._take()

    # -- the work ------------------------------------------------------------------------

    def _take(self) -> str:
        text = "".join(self._out)
        self._out.clear()
        return text

    def _run(self, final: bool) -> None:
        while self._buf:
            if self._line_start:
                if not self._start_line(final):
                    return  # wait: the line's kind is not clear yet
                continue
            char = self._buf[0]
            if char == "\n":
                self._buf = self._buf[1:]
                self._end_line()
                continue
            if self._code_line:
                self._buf = self._buf[1:]
                text = "    " if char == "\t" else char
                self._out.append(text)
                self.col += width_of(text)
                continue
            if not self._inline(final):
                return

    def _start_line(self, final: bool) -> bool:
        """Decide what the line in front is (a heading, a list item, ...) and begin it."""
        end = self._buf.find("\n")
        line = self._buf if end < 0 else self._buf[:end]
        whole = end >= 0 or final
        if self._fence is not None:  # in a code block: its end, or a line of code
            stripped = line.strip()
            if not whole and self._fence.startswith(stripped):
                return False
            if whole and stripped.startswith(self._fence) and not stripped.strip("`~"):
                self._fence = None
                self._consume_line(end)
                return True
            self._begin(self.indent + 2)
            self._out.append(self.p.code)
            self._code_line = True
            return True
        is_row = line.lstrip().startswith("|")
        if self._table and not is_row and (whole or line.strip()):
            self._draw_table()
        if is_row:
            if not whole:
                return False
            self._table.append(line)
            self._consume_line(end)
            return True
        if not whole and _MARKS.fullmatch(line) and len(line) < 16:
            return False  # "-", "1.", "##", "```": wait for the character that decides
        if not whole and _RULE_SO_FAR.fullmatch(line):
            return False  # "* *" may still be a rule (or "* **Bold**" a list item)
        fence = _FENCE.match(line)
        if fence and not line[fence.end() :].strip():
            if not whole:
                return False
            self._fence = fence.group(1)[0] * 3
            if fence.group(2):  # its language, small, above the code
                self._begin(self.indent + 2)
                self._out.append(self.p.paint(fence.group(2), self.p.dim))
                self._newline()
            self._consume_line(end)
            return True
        if whole and _RULE.fullmatch(line):
            self._begin(self.indent)
            rule = "─" * max(8, self.width - self.indent)
            self._out.append(self.p.paint(rule, self.p.dim))
            self._newline()
            self._consume_line(end)
            return True
        if not line and end == 0:  # a blank line: a gap between paragraphs
            self._buf = self._buf[1:]
            self._newline()
            return True
        self._open_line(line)
        return True

    def _open_line(self, line: str) -> None:
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        number = _NUMBER.match(line)
        quote = _QUOTE.match(line)
        if heading:
            self._buf = self._buf[heading.end() :]
            self._begin(self.indent)
            self._line_style = self.p.bold + self.p.accent
        elif bullet:
            nested = len(bullet.group(1).expandtabs(4))
            self._buf = self._buf[bullet.end() :]
            self._begin(self.indent + nested)
            self._out.append(self.p.paint("•", self.p.accent) + " ")
            self._hang(self.indent + nested + 2)
        elif number:
            nested = len(number.group(1).expandtabs(4))
            mark = number.group(2) + "."
            self._buf = self._buf[number.end() :]
            self._begin(self.indent + nested)
            self._out.append(self.p.paint(mark, self.p.accent) + " ")
            self._hang(self.indent + nested + len(mark) + 1)
        elif quote:
            self._buf = self._buf[quote.end() :]
            bar = self.p.paint("│", self.p.dim) + " "
            self._begin(self.indent)
            self._out.append(bar)
            self._lead, self._lead_width = " " * self.indent + bar, self.indent + 2
            self.col += 2
            self._line_style = self.p.dim
        else:
            self._buf = self._buf[len(line) - len(line.lstrip(" \t")) :]
            self._begin(self.indent)
        if self._line_style:
            self._out.append(self._line_style)
        self._line_start = False

    def _begin(self, column: int) -> None:
        """Start a line's text at this column (after a label, the cursor is there already)."""
        if self.col < column:
            self._out.append(" " * (column - self.col))
            self.col = column
        self._lead, self._lead_width = " " * self.indent, self.indent
        self._line_start = False

    def _hang(self, column: int) -> None:
        """Wrapped lines of a list item go on under its text, not under the bullet."""
        self.col = max(self.col, column)
        self._lead, self._lead_width = " " * column, column

    def _consume_line(self, end: int) -> None:
        self._buf = "" if end < 0 else self._buf[end + 1 :]
        self._line_start = True

    def _newline(self) -> None:
        self._out.append("\n")
        self.col = 0
        self._line_start = True

    def _end_line(self) -> None:
        self._end_word()
        if self._styled():
            self._out.append(self.p.reset)
        self._bold = self._italic = self._code = self._dim = self._code_line = False
        self._line_style = ""
        self._space = False
        self._prev = " "
        self._newline()

    def _styled(self) -> bool:
        looks = (self._bold, self._italic, self._code, self._dim, self._code_line)
        return bool(self._line_style or any(looks))

    def _style(self) -> str:
        """The codes for the look in force now (after a reset)."""
        codes = self._line_style
        if self._bold:
            codes += self.p.bold
        if self._italic:
            codes += self.p.italic
        if self._code:
            codes += self.p.code
        if self._dim:
            codes += self.p.dim
        return self.p.reset + codes

    def _inline(self, final: bool) -> bool:
        """One piece of a line: a mark that changes the look, a space, or a character."""
        buf = self._buf
        char = buf[0]
        if char == "\\" and not self._code:
            if len(buf) < 2 and not final:
                return False
            if len(buf) > 1 and buf[1] in _ESCAPABLE:
                self._add(buf[1])
                self._buf = buf[2:]
                return True
        elif char in (DIM_ON, DIM_OFF):
            self._toggle("_dim")
            self._buf = buf[1:]
            return True
        elif char == "[" and not self._code:
            link = _LINK.match(buf)
            if link:  # "text (address)": the address as it is, its marks no Markdown
                address = re.sub(r"([\\`*\[\]_])", r"\\\1", link.group(2))
                shown = f"{link.group(1)} {DIM_ON}({address}){DIM_OFF}"
                self._buf = shown + buf[link.end() :]
                return True
            if not final and _LINK_SO_FAR.fullmatch(buf):
                return False  # wait: it may still become a link
        elif char == "`":
            self._toggle("_code")
            self._buf = buf[1:]
            self._prev = char
            return True
        elif char == "*" and not self._code:
            if len(buf) < 2 and not final:
                return False
            following = buf[1] if len(buf) > 1 else " "
            if following == "*":
                self._toggle("_bold")
                self._buf = buf[2:]
                self._prev = char
                return True
            opens = not self._italic and not following.isspace() and not self._prev.isalnum()
            closes = self._italic and not self._prev.isspace()
            if opens or closes:
                self._toggle("_italic")
                self._buf = buf[1:]
                self._prev = char
                return True
        elif char in " \t" and not self._code:
            self._end_word()
            self._space = True
            self._buf = buf[1:]
            self._prev = " "
            return True
        self._add(char)
        self._buf = buf[1:]
        return True

    def _toggle(self, look: str) -> None:
        """Bold, italic or code on or off, from here in the word."""
        if not self._word:
            self._word_style = self._style()
        setattr(self, look, not getattr(self, look))
        self._word.append(self._style())

    def _add(self, char: str) -> None:
        if not self._word:
            self._word_style = self._style()
        self._word.append(char)
        self._word_width += width_of(char)
        self._prev = char

    def _end_word(self) -> None:
        if not self._word:
            return
        space = 1 if self._space and self.col > self._lead_width else 0
        too_long = self._word_width and self.col + space + self._word_width > self.width
        if too_long and self.col > self._lead_width:  # wrap: on the next line, under the text
            styled = self._word_style != self.p.reset  # (the look the line is in now)
            self._out.append((self.p.reset if styled else "") + "\n" + self._lead)
            self._out.append(self._word_style if styled else "")
            self.col = self._lead_width
            space = 0
        if space:
            self._out.append(" ")
            self.col += 1
        self._out.extend(self._word)
        self.col += self._word_width
        self._word.clear()
        self._word_width = 0
        self._space = False

    def _draw_table(self) -> None:
        """A table, its columns lined up (or as it was written, when it is too wide)."""
        rows = self._table
        self._table = []
        cells = [[_bare(c) for c in row.strip().strip("|").split("|")] for row in rows]
        align: list[str] = []
        if len(cells) > 1 and all(_DELIMITER.fullmatch(c.strip()) for c in cells[1] if c.strip()):
            align = [_alignment(c.strip()) for c in cells[1]]
            del cells[1]
        count = max(len(row) for row in cells)
        cells = [row + [""] * (count - len(row)) for row in cells]
        widths = [max(width_of(row[i]) for row in cells) for i in range(count)]
        bar = self.p.paint(" │ ", self.p.dim)
        if self.indent + sum(widths) + 3 * (count - 1) > self.width:
            for written in rows:  # too wide: as written (the terminal wraps it)
                self._begin(self.indent)
                self._out.append(written.strip())
                self._newline()
            return
        for number, row in enumerate(cells):
            self._begin(self.indent)
            shown = []
            for i, cell in enumerate(row):
                pad = widths[i] - width_of(cell)
                how = align[i] if i < len(align) else "l"
                if how == "r":
                    text = " " * pad + cell
                elif how == "c":
                    text = " " * (pad // 2) + cell + " " * (pad - pad // 2)
                else:
                    text = cell + " " * pad
                shown.append(self.p.paint(text, self.p.bold) if number == 0 else text)
            self._out.append(bar.join(shown).rstrip())
            self._newline()
            if number == 0 and len(cells) > 1:
                self._begin(self.indent)
                self._out.append(self.p.paint("─┼─".join("─" * w for w in widths), self.p.dim))
                self._newline()


def _alignment(delimiter: str) -> str:
    """A table column's alignment from its delimiter: ---: right, :---: centre, else left."""
    if delimiter.endswith(":"):
        return "c" if delimiter.startswith(":") else "r"
    return "l"


def _bare(cell: str) -> str:
    """A table cell's text without its Markdown marks."""
    text = cell.strip().replace("\\|", "|")
    return re.sub(r"(\*\*|__|`|\*(?=\S)|(?<=\S)\*)", "", text)
