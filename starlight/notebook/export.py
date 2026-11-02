"""A note to keep or to send: as one HTML file or as a PDF (docs/plan-notes-app.md, G1).

* ``pictures``: the pictures a note shows (``![[photo.png]]``), read from the Starlight
  folder and turned into ``data:`` addresses, so an exported page shows them on its own.
* ``to_pdf``: the note as a PDF made here (with reportlab, the ``pdf`` extra): its title,
  headings, paragraphs with bold, italic and code, lists and tasks, quotes and callouts,
  tables, code blocks and pictures. Links to other notes become their text.

Only what is inside the Starlight folder is read; pictures are capped in size.
"""

from __future__ import annotations

import base64
import io
import re
import sys
from pathlib import Path
from typing import Any

from starlight.core.tools import ToolError
from starlight.notebook import markdown

PICTURE = re.compile(r"\.(png|jpe?g|gif|webp)$", re.IGNORECASE)
MAX_PICTURE = 10 * 1024**2
MAX_PICTURES = 25 * 1024**2  # all pictures of one note together
ATTACHMENTS = "Notes/Attachments"
TYPES = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF8": "image/gif"}


class ExportError(ToolError):
    pass


def picture_path(target: str) -> str | None:
    """Where a picture a note names is, in the Starlight folder (as render.js finds it): a
    bare name is in Notes/Attachments. None for anything that could lead outside."""
    parts = target.replace("\\", "/").split("/")
    if not target or re.match(r"^[a-z][a-z0-9+.\-]*:", target, re.I) or target.startswith("/"):
        return None
    if any(not p or p in (".", "..") or p.startswith(".") for p in parts):
        return None
    return "/".join(parts) if len(parts) > 1 else f"{ATTACHMENTS}/{parts[0]}"


def _kind(data: bytes) -> str | None:
    for start, kind in TYPES.items():
        if data.startswith(start):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None  # (SVG is left out: it can carry code)


def read_picture(root: Path, path: str) -> bytes | None:
    """A picture's bytes, when it is inside ``root``, not too big and really a picture."""
    file = (root / path).resolve()
    try:
        if not file.is_relative_to(root.resolve()) or file.stat().st_size > MAX_PICTURE:
            return None
        data = file.read_bytes()
    except OSError:
        return None
    return data if _kind(data) else None


def pictures(root: Path, text: str) -> dict[str, str]:
    """The note's pictures: their path in the Starlight folder -> a data: address."""
    found: dict[str, str] = {}
    total = 0
    for link in markdown.parse(text).links:
        if not (link.embed and PICTURE.search(link.target)):
            continue
        path = picture_path(link.target)
        if path is None or path in found:
            continue
        data = read_picture(root, path)
        if data is None or total + len(data) > MAX_PICTURES:
            continue
        total += len(data)
        found[path] = f"data:{_kind(data)};base64,{base64.b64encode(data).decode('ascii')}"
    return found


# -- the PDF -------------------------------------------------------------------------------------

FONT_FILES = [  # (regular, bold, italic, bold italic), the first that is there
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
     r"C:\Windows\Fonts\segoeuii.ttf", r"C:\Windows\Fonts\segoeuiz.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf",
     r"C:\Windows\Fonts\ariali.ttf", r"C:\Windows\Fonts\arialbi.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
]  # fmt: skip


def _font() -> str:
    """A font family with umlauts and more (registered once), or Helvetica."""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "StarlightSans" in pdfmetrics.getRegisteredFontNames():
        return "StarlightSans"
    import reportlab

    bundled = Path(reportlab.__file__).parent / "fonts"
    vera = tuple(str(bundled / n) for n in ("Vera.ttf", "VeraBd.ttf", "VeraIt.ttf", "VeraBI.ttf"))
    choices = [*FONT_FILES, vera]
    if sys.platform != "win32":
        choices = [c for c in choices if not c[0].startswith("C:")]
    for regular, bold, italic, both in choices:
        if not all(Path(f).is_file() for f in (regular, bold, italic, both)):
            continue
        try:
            for suffix, file in (("", regular), ("-Bold", bold), ("-Italic", italic),
                                 ("-BoldItalic", both)):  # fmt: skip
                pdfmetrics.registerFont(TTFont("StarlightSans" + suffix, file))
        except Exception:  # a font file it cannot read: the next one
            continue
        addMapping("StarlightSans", 0, 0, "StarlightSans")
        addMapping("StarlightSans", 1, 0, "StarlightSans-Bold")
        addMapping("StarlightSans", 0, 1, "StarlightSans-Italic")
        addMapping("StarlightSans", 1, 1, "StarlightSans-BoldItalic")
        return "StarlightSans"
    return "Helvetica"


def without_title(text: str, title: str) -> str:
    """The note's text without its first line when that is a heading saying its title (the
    export shows the title already)."""
    lines = text.split("\n")
    first = next((n for n, line in enumerate(lines) if line.strip()), None)
    if first is not None:
        heading = re.match(r"^#\s+(.*?)\s*#*\s*$", lines[first])
        if heading and heading.group(1).strip() == title.strip():
            del lines[first]
    return "\n".join(lines)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


INLINE_CODE = re.compile(r"(`+)(.+?)\1")
WIKILINK = re.compile(r"!?\[\[([^\]|\n]*\|)?([^\]\n]*)\]\]")
MDLINK = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]*)[^)]*\)")


def inline(text: str) -> str:
    """One line of Markdown as reportlab's little markup (bold, italic, code, struck), with
    links as their text. Everything else is escaped."""
    codes: list[str] = []

    def keep_code(match: re.Match[str]) -> str:
        codes.append(match.group(2))
        return f"\x00{len(codes) - 1}\x00"

    text = INLINE_CODE.sub(keep_code, text)
    text = WIKILINK.sub(lambda m: m.group(2).split("#")[0] if not m.group(1) else m.group(2), text)
    text = MDLINK.sub(lambda m: m.group(1) or m.group(2), text)
    text = _escape(text)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<strike>\1</strike>", text)
    text = re.sub(r"==(.+?)==", r"<u>\1</u>", text)
    return re.sub(
        "\x00(\\d+)\x00",
        lambda m: f'<font face="Courier">{_escape(codes[int(m.group(1))])}</font>',
        text,
    )


LIST = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(?:\[(.)\]\s+)?(.*)$")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
CALLOUT = re.compile(r"^\[!([\w-]+)\][+-]?\s*(.*)$")
PICTURE_LINE = re.compile(r"^\s*!\[\[([^\]|]+?)(?:\|(\d+))?\]\]\s*$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def to_pdf(title: str, text: str, root: Path, when: str = "") -> bytes:
    """The note (``text``: its Markdown without the properties block) as a PDF."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            HRFlowable,
            Image,
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        raise ExportError(
            "making a PDF needs the 'pdf' extra (reportlab); the HTML file works without it"
        ) from None
    font = _font()
    sheet = getSampleStyleSheet()
    body = ParagraphStyle("body", sheet["BodyText"], fontName=font, fontSize=10.5, leading=15)
    styles = {
        "title": ParagraphStyle("t", sheet["Title"], fontName=font, alignment=0, fontSize=20,
                                leading=24, spaceAfter=2),
        "when": ParagraphStyle("w", body, fontSize=9, textColor=colors.grey, spaceAfter=10),
        1: ParagraphStyle("h1", sheet["Heading1"], fontName=font, fontSize=16, leading=20),
        2: ParagraphStyle("h2", sheet["Heading2"], fontName=font, fontSize=13.5, leading=17),
        3: ParagraphStyle("h3", sheet["Heading3"], fontName=font, fontSize=12, leading=15),
        "quote": ParagraphStyle("q", body, leftIndent=14, textColor=colors.HexColor("#5a6372"),
                                borderPadding=(2, 0, 2, 6)),
        "code": ParagraphStyle("c", body, fontName="Courier", fontSize=8.5, leading=11,
                               backColor=colors.HexColor("#f3f4f6"), borderPadding=5,
                               leftIndent=4, rightIndent=4),
        "cell": ParagraphStyle("cell", body, fontSize=9.5, leading=12),
    }  # fmt: skip
    out = io.BytesIO()
    margins = {"leftMargin": 2.2 * cm, "rightMargin": 2.2 * cm, "topMargin": 2 * cm,
               "bottomMargin": 2 * cm}  # fmt: skip
    doc = SimpleDocTemplate(out, pagesize=A4, title=title, **margins)
    story: list[Any] = [Paragraph(_escape(title), styles["title"])]
    if when:
        story.append(Paragraph(_escape(when), styles["when"]))
    lines = without_title(text.replace("\r\n", "\n"), title).split("\n")
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            story.append(Paragraph(inline(" ".join(p.strip() for p in paragraph)), body))
            paragraph.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fence = markdown.FENCE.match(line)
        if fence:
            flush()
            marker, code = fence.group(1), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                code.append(lines[i])
                i += 1
            story.append(Preformatted("\n".join(code) or " ", styles["code"]))
            story.append(Spacer(1, 6))
            i += 1
            continue
        heading = markdown.HEADING.match(line)
        if heading and heading.group(2):
            flush()
            level = min(len(heading.group(1)), 3)
            story.append(Paragraph(inline(heading.group(2)), styles[level]))
            i += 1
            continue
        picture = PICTURE_LINE.match(line)
        if picture and PICTURE.search(picture.group(1)):
            flush()
            path = picture_path(picture.group(1).strip())
            data = read_picture(root, path) if path else None
            if data is not None:
                try:
                    from reportlab.lib.utils import ImageReader

                    width, height = ImageReader(io.BytesIO(data)).getSize()
                    wanted = min(float(picture.group(2) or width) * 0.75, doc.width)
                    scale = min(wanted / width, (12 * cm) / height)
                    story.append(
                        Image(io.BytesIO(data), width * scale, height * scale, hAlign="LEFT")
                    )
                    story.append(Spacer(1, 6))
                except Exception:  # (a picture reportlab cannot read is left out)
                    pass
            i += 1
            continue
        if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
            flush()
            story.append(
                HRFlowable(width="100%", color=colors.lightgrey, spaceBefore=4, spaceAfter=6)
            )
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush()
            quoted = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            callout = CALLOUT.match(quoted[0]) if quoted else None
            if callout:
                head = callout.group(2) or callout.group(1).capitalize()
                story.append(Paragraph(f"<b>{_escape(head)}</b>", styles["quote"]))
                quoted = quoted[1:]
            words = " ".join(q.strip() for q in quoted if q.strip())
            if words:
                story.append(Paragraph(inline(words), styles["quote"]))
            story.append(Spacer(1, 4))
            continue
        if "|" in line and i + 1 < len(lines) and TABLE_RULE.match(lines[i + 1]):
            flush()
            rows = [_cells(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            width = max(len(r) for r in rows)
            cells = [[Paragraph(inline(c), styles["cell"]) for c in r + [""] * (width - len(r))]
                     for r in rows]  # fmt: skip
            table = Table(cells, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))  # fmt: skip
            story.extend([table, Spacer(1, 6)])
            continue
        item = LIST.match(line)
        if item:
            flush()
            depth = len(item.group(1).expandtabs(4)) // 2
            mark = item.group(3)
            bullet = ("[x]" if mark.lower() == "x" else "[ ]") if mark is not None else (
                item.group(2) if item.group(2)[0].isdigit() else "\u2022")  # fmt: skip
            style = ParagraphStyle(
                f"li{depth}", body, leftIndent=16 + depth * 14, bulletIndent=4 + depth * 14
            )
            words = inline(item.group(4))
            if mark is not None and mark.lower() == "x":
                words = f'<font color="#6b7280"><strike>{words}</strike></font>'
            story.append(Paragraph(words, style, bulletText=bullet))
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        paragraph.append(line)
        i += 1
    flush()
    doc.build(story)
    return out.getvalue()
