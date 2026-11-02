"""A small, safe language for finding notes by what they hold (docs/plan-notes-app.md, E3).

    status is open and tag is work
    due < 2026-12-01 or priority is high
    not (folder is Journal) and words > 200
    rating >= 4 and modified > today-7
    tags has garden          status is not done          due is empty

Words: ``and``, ``or``, ``not`` and brackets; ``is`` (``=``), ``is not`` (``!=``), ``<``, ``>``,
``<=``, ``>=``, ``has`` (a list holds it, or a text contains it), ``is empty``, ``is not
empty``. German works too: und, oder, nicht, ist, ist nicht, hat, enthält, leer.

Fields: any property of a note (``status``, ``due`` ...), and ``tag`` (with the tags below
it: ``tag is work`` finds #work/projects too), ``folder`` (and the folders in it), ``path``,
``name``, ``modified`` (the day it was last changed) and ``words``.

Values: ``"text in quotes"``, a word or several (``status is in progress``), numbers, dates
(2026-12-01, today, yesterday, tomorrow, today-7, today+30) and yes/no (true/false, ja/nein).
Upper and lower case do not matter. A note without the property does not match ``is``,
``<`` and the like, and does match ``is not`` and ``is empty``.

The text is read into a tree and turned into SQL for the notebook index with every name and
value passed as a parameter: nothing typed is ever run as code or as SQL. A query stops
after a few seconds.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from starlight.core.tools import ToolError

MAX_TEXT = 2000
MAX_DEPTH = 30
SECONDS = 3.0
TOKEN = re.compile(r"""\s*(?:(\()|(\))|(<=|>=|!=|=|<|>)|"([^"]*)"|'([^']*)'|([^\s()<>=!"']+))""")
WORDS = {
    "and": "and", "und": "and", "or": "or", "oder": "or", "not": "not", "nicht": "not",
    "is": "is", "ist": "is", "has": "has", "hat": "has", "contains": "has", "enthält": "has",
    "empty": "empty", "leer": "empty",
}  # fmt: skip
YES, NO = {"yes", "true", "ja", "wahr"}, {"no", "false", "nein", "falsch"}
SPECIAL = {
    "tag": "tag", "tags": "tag", "schlagwort": "tag", "folder": "folder", "ordner": "folder",
    "path": "path", "pfad": "path", "name": "name", "title": "name", "titel": "name",
    "modified": "modified", "changed": "modified", "geändert": "modified",
    "words": "words", "wörter": "words",
}  # fmt: skip
ORDERED = ("<", ">", "<=", ">=")


class QueryError(ToolError):
    """The query could not be read; the message says where and why, in plain words."""


@dataclass(frozen=True)
class Value:
    text: str  # as written
    kind: str  # "number", "date", "bool" or "text"
    number: float = 0.0
    day: str = ""  # an ISO date
    yes: bool = False


@dataclass(frozen=True)
class Cond:
    field: str
    op: str  # is, is not, <, >, <=, >=, has, empty, not empty
    value: Value | None = None


@dataclass(frozen=True)
class Not:
    node: Node


@dataclass(frozen=True)
class AllOf:
    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class AnyOf:
    nodes: tuple[Node, ...]


type Node = Cond | Not | AllOf | AnyOf


def value_of(text: str, today: dt.date) -> Value:
    """What a written value is: a number, a date, yes/no or text."""
    raw = text.strip()
    folded = raw.casefold()
    match = re.fullmatch(r"(today|heute)(?:\s*([+-])\s*(\d{1,5}))?", folded)
    if match:
        days = int(match.group(3) or 0) * (-1 if match.group(2) == "-" else 1)
        return Value(raw, "date", day=(today + dt.timedelta(days=days)).isoformat())
    named = {"yesterday": -1, "gestern": -1, "tomorrow": 1, "morgen": 1}
    if folded in named:
        return Value(raw, "date", day=(today + dt.timedelta(days=named[folded])).isoformat())
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return Value(raw, "date", day=dt.date.fromisoformat(raw).isoformat())
        except ValueError:
            raise QueryError(f"{raw} is not a date in the calendar") from None
    if re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", raw):
        return Value(raw, "number", number=float(raw.replace(",", ".")))
    if folded in YES or folded in NO:
        return Value(raw, "bool", yes=folded in YES)
    return Value(raw, "text")


class _Parser:
    def __init__(self, text: str, today: dt.date) -> None:
        if len(text) > MAX_TEXT:
            raise QueryError(f"the query is too long (at most {MAX_TEXT} characters)")
        self.today = today
        self.tokens: list[tuple[str, str]] = []  # (kind, text): ( ) op quoted word
        at = 0
        while at < len(text):
            match = TOKEN.match(text, at)
            if not match or match.end() == at:
                if not text[at:].strip():
                    break
                raise QueryError(f"cannot read the query at {text[at : at + 20]!r}")
            at = match.end()
            opening, closing, op, double, single, word = match.groups()
            if opening:
                self.tokens.append(("(", "("))
            elif closing:
                self.tokens.append((")", ")"))
            elif op:
                self.tokens.append(("op", op))
            elif double is not None or single is not None:
                self.tokens.append(("quoted", double if double is not None else single or ""))
            elif word:
                self.tokens.append(("word", word))
        self.at = 0
        self.depth = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.at] if self.at < len(self.tokens) else ("end", "")

    def keyword(self) -> str:
        kind, text = self.peek()
        return WORDS.get(text.casefold(), "") if kind == "word" else ""

    def near(self) -> str:
        kind, text = self.peek()
        return "the end" if kind == "end" else repr(text)

    def parse(self) -> Node:
        if not self.tokens:
            raise QueryError("the query is empty: say what to look for, e.g. status is open")
        node = self.any_of()
        if self.peek()[0] != "end":
            raise QueryError(f"did not expect {self.near()} here (missing 'and' or 'or'?)")
        return node

    def any_of(self) -> Node:
        nodes = [self.all_of()]
        while self.keyword() == "or":
            self.at += 1
            nodes.append(self.all_of())
        return nodes[0] if len(nodes) == 1 else AnyOf(tuple(nodes))

    def all_of(self) -> Node:
        nodes = [self.not_()]
        while self.keyword() == "and":
            self.at += 1
            nodes.append(self.not_())
        return nodes[0] if len(nodes) == 1 else AllOf(tuple(nodes))

    def not_(self) -> Node:
        if self.keyword() == "not":
            self.at += 1
            return Not(self.not_())
        if self.peek()[0] == "(":
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise QueryError("too many brackets inside each other")
            self.at += 1
            node = self.any_of()
            if self.peek()[0] != ")":
                raise QueryError(f"a bracket is not closed (found {self.near()})")
            self.at += 1
            self.depth -= 1
            return node
        return self.condition()

    def condition(self) -> Cond:
        kind, name = self.peek()
        if kind not in ("word", "quoted") or (kind == "word" and self.keyword()):
            raise QueryError(f"expected a field such as status or tag, found {self.near()}")
        self.at += 1
        field = name.strip()
        kind, text = self.peek()
        word = self.keyword()
        if kind == "op" and text in ORDERED:
            self.at += 1
            return Cond(field, text, self.value(text))
        if (kind == "op" and text in ("=", "!=")) or word == "is":
            self.at += 1
            negative = text == "!="
            if self.keyword() == "not":
                self.at += 1
                negative = True
            if self.keyword() == "empty":
                self.at += 1
                return Cond(field, "not empty" if negative else "empty")
            return Cond(field, "is not" if negative else "is", self.value("is"))
        if word == "has":
            self.at += 1
            return Cond(field, "has", self.value("has"))
        raise QueryError(
            f"after {field!r} comes is, is not, <, >, has or is empty; found {self.near()}"
        )

    def value(self, after: str) -> Value:
        kind, text = self.peek()
        if kind == "quoted":
            self.at += 1
            return Value(text, "text")
        words = []
        while self.peek()[0] == "word" and self.keyword() not in ("and", "or"):
            words.append(self.peek()[1])
            self.at += 1
        if not words:
            raise QueryError(f"expected a value after {after!r}, found {self.near()}")
        return value_of(" ".join(words), self.today)


def parse(text: str, today: dt.date | None = None) -> Node:
    """The query as a tree; QueryError (in plain words) when it cannot be read."""
    return _Parser(text, today or dt.date.today()).parse()


# -- to SQL --------------------------------------------------------------------------------------

PROPERTY = (
    "EXISTS (SELECT 1 FROM properties p, json_each(p.value) j"
    " WHERE p.file_id = f.id AND lower(p.key) = lower(?) AND ({}))"
)
FILLED = "j.type != 'null' AND trim(CAST(j.value AS TEXT)) != ''"
DATE_TEXT = "j.type = 'text' AND j.value GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'"
MODIFIED = "date(f.mtime_ns / 1000000000, 'unixepoch', 'localtime')"


def _like(text: str) -> str:
    return "%" + text.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _property(cond: Cond) -> tuple[str, list[Any]]:
    value, op = cond.value, cond.op
    if op in ("empty", "not empty"):
        sql = PROPERTY.format(FILLED)
        return (f"NOT {sql}" if op == "empty" else sql), [cond.field]
    assert value is not None
    if op == "is not":
        sql, params = _property(Cond(cond.field, "is", value))
        return f"NOT {sql}", params
    if op == "has":
        return PROPERTY.format("lower(CAST(j.value AS TEXT)) LIKE ? ESCAPE '\\'"), [
            cond.field, _like(value.text),
        ]  # fmt: skip
    if op == "is":
        if value.kind == "bool":
            words = sorted(YES if value.yes else NO)
            check = (
                f"(j.type IN ('true', 'false') AND (j.type = 'true') = ?)"
                f" OR (j.type = 'text' AND lower(j.value) IN ({', '.join('?' for _ in words)}))"
            )
            return PROPERTY.format(check), [cond.field, value.yes, *words]
        if value.kind == "number":
            check = (
                "(j.type IN ('integer', 'real') AND j.value = ?)"
                " OR (j.type = 'text' AND j.value = ?)"
            )
            return PROPERTY.format(check), [cond.field, value.number, value.text]
        if value.kind == "date":
            return PROPERTY.format(f"{DATE_TEXT} AND substr(j.value, 1, 10) = ?"), [
                cond.field, value.day,
            ]  # fmt: skip
        return PROPERTY.format("lower(CAST(j.value AS TEXT)) = ?"), [
            cond.field,
            value.text.casefold(),
        ]
    # <, >, <=, >=
    if value.kind == "number":
        return PROPERTY.format(f"j.type IN ('integer', 'real') AND j.value {op} ?"), [
            cond.field, value.number,
        ]  # fmt: skip
    if value.kind == "date":
        return PROPERTY.format(f"{DATE_TEXT} AND substr(j.value, 1, 10) {op} ?"), [
            cond.field, value.day,
        ]  # fmt: skip
    if value.kind == "bool":
        raise QueryError(f"{cond.field} {op} {value.text}: yes and no have no order")
    return PROPERTY.format(f"j.type = 'text' AND lower(j.value) {op} ?"), [
        cond.field, value.text.casefold(),
    ]  # fmt: skip


def _special(kind: str, cond: Cond) -> tuple[str, list[Any]]:
    value, op = cond.value, cond.op
    if kind == "tag":
        any_tag = "EXISTS (SELECT 1 FROM tags t WHERE t.file_id = f.id{})"
        if op == "empty":
            return "NOT " + any_tag.format(""), []
        if op == "not empty":
            return any_tag.format(""), []
        assert value is not None
        tag = value.text.strip().lstrip("#").strip("/").casefold()
        if op in ("is", "is not", "has"):
            test = any_tag.format(" AND (t.tag = ? OR t.tag LIKE ? ESCAPE '\\')")
            below = tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
            return (f"NOT {test}" if op == "is not" else test), [tag, below]
        raise QueryError(f"tags have no order: use tag is {value.text} or tag has {value.text}")
    column = {"folder": "f.folder", "path": "f.path", "name": "f.title"}.get(kind)
    if column is not None:
        if op in ("empty", "not empty"):
            return (f"{column} {'=' if op == 'empty' else '!='} ''"), []
        assert value is not None
        text = value.text.strip().strip("/") if kind == "folder" else value.text.strip()
        if op in ("is", "is not"):
            if kind == "folder":  # the folder, or one inside it
                test = f"(lower({column}) = ? OR lower({column}) LIKE ? ESCAPE '\\')"
                below = (
                    text.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                params: list[Any] = [text.casefold(), below + "/%"]
            else:
                test, params = f"lower({column}) = ?", [text.casefold()]
            return (f"NOT {test}" if op == "is not" else test), params
        if op == "has":
            return f"lower({column}) LIKE ? ESCAPE '\\'", [_like(text)]
        return f"lower({column}) {op} ?", [text.casefold()]
    if kind == "modified":
        if op in ("empty", "not empty") or value is None:
            raise QueryError("every note has a modified date: compare it with a date")
        if value.kind != "date":
            raise QueryError(
                f"modified is a date: write it as 2026-12-01 or today-7, not {value.text}"
            )
        sql_op = {"is": "=", "is not": "!=", "has": "="}.get(op, op)
        return f"{MODIFIED} {sql_op} ?", [value.day]
    # words
    if op in ("empty", "not empty") or value is None or value.kind != "number":
        raise QueryError("words is a number: compare it with one, e.g. words > 200")
    sql_op = {"is": "=", "is not": "!=", "has": "="}.get(op, op)
    return f"f.words {sql_op} ?", [value.number]


def to_sql(node: Node) -> tuple[str, list[Any]]:
    """The tree as an SQL condition on the notebook index's files (``f``) and its parameters.
    Only fixed pieces of SQL are written; every name and value is a parameter."""
    if isinstance(node, Cond):
        kind = SPECIAL.get(node.field.casefold())
        return _special(kind, node) if kind else _property(node)
    if isinstance(node, Not):
        sql, params = to_sql(node.node)
        return f"NOT ({sql})", params
    parts = [to_sql(child) for child in node.nodes]
    joiner = " AND " if isinstance(node, AllOf) else " OR "
    return "(" + joiner.join(f"({sql})" for sql, _ in parts) + ")", [
        p for _, params in parts for p in params
    ]


# -- sorting and showing ---------------------------------------------------------------------------

SORTED = {
    "name": "lower(f.title)", "folder": "lower(f.folder)", "path": "lower(f.path)",
    "modified": "f.mtime_ns", "words": "f.words",
}  # fmt: skip
PROPERTY_SORT = (
    "(SELECT CASE WHEN j.type IN ('integer', 'real') THEN j.value"
    " ELSE lower(CAST(j.value AS TEXT)) END FROM properties p, json_each(p.value) j"
    " WHERE p.file_id = f.id AND lower(p.key) = lower(?) LIMIT 1)"
)


DIRECTIONS = {"asc": False, "up": False, "aufsteigend": False, "auf": False,
              "desc": True, "down": True, "absteigend": True, "ab": True}  # fmt: skip


def order_by(sort: str) -> tuple[str, list[Any]]:
    """``sort`` ("due", "priority desc, name") as SQL; notes without the field come last."""
    parts: list[str] = []
    params: list[Any] = []
    for piece in [p.strip() for p in sort.split(",") if p.strip()][:5]:
        words = piece.split()
        down = False
        if len(words) > 1 and words[-1].casefold() in DIRECTIONS:
            down = DIRECTIONS[words.pop().casefold()]
        field = " ".join(words).strip("\"'")
        kind = SPECIAL.get(field.casefold())
        if kind == "tag":
            raise QueryError("notes cannot be sorted by tag; sort by name or a property")
        expression = SORTED[kind] if kind else PROPERTY_SORT
        if not kind:
            params += [field, field]  # (the expression is written twice)
        parts.append(f"({expression}) IS NULL, {expression}{' DESC' if down else ''}")
    parts.append("lower(f.title)")
    return ", ".join(parts), params


def fields_of(text: str) -> list[str]:
    """The columns asked for ("status, due, tag"), each once."""
    return list(dict.fromkeys(f.strip().strip("\"'") for f in text.split(",") if f.strip()))[:12]


BLOCK_KEYS = {
    "where": "where", "wo": "where", "wenn": "where", "sort": "sort", "sortieren": "sort",
    "sortiert": "sort", "fields": "fields", "felder": "fields", "spalten": "fields",
    "columns": "fields", "limit": "limit", "anzahl": "limit", "max": "limit",
}  # fmt: skip
BLOCK_LINE = re.compile(r"^\s*([A-Za-zÄÖÜäöüß]+)\s*:\s*(.*)$")


def parse_block(text: str) -> dict[str, Any]:
    """A ```query block's lines: where:, sort:, fields:, limit: (German: wo:, sortieren:,
    felder:, anzahl:). Lines without a key belong to where (a block may be only a
    condition). {"where", "sort", "fields", "limit"}."""
    found: dict[str, Any] = {"where": "", "sort": "", "fields": "", "limit": 100}
    where: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue  # (a "#" line is a comment)
        match = BLOCK_LINE.match(line)
        key = BLOCK_KEYS.get(match.group(1).casefold(), "?") if match else "where"
        value = match.group(2).strip() if match else line.strip()
        if key == "?":
            raise QueryError(f"{line.split(':')[0].strip()}: is not a query line; use where:, "
                             "sort:, fields: or limit:")  # fmt: skip
        if key == "where":
            where.append(value)
        elif key == "limit":
            if not value.isdigit():
                raise QueryError("limit: is a number of notes, e.g. limit: 20")
            found["limit"] = max(1, min(int(value), 1000))
        else:
            found[key] = value
    found["where"] = " ".join(w.strip() for w in where if w.strip())
    return found


def run(
    index: Any,
    where: str = "",
    sort: str = "",
    fields: str = "",
    limit: int = 50,
    *,
    seconds: float = SECONDS,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """The notes ``where`` finds (all notes when it is empty), sorted, with ``fields``:
    {"fields": [...], "notes": [{"name", "path", field: value ...}], "count"}."""
    condition, params = to_sql(parse(where, today)) if where.strip() else ("1", [])
    order, order_params = order_by(sort or "name")
    limit = max(1, min(int(limit), 1000))
    try:
        found = index.select(condition, params, order, order_params, limit, seconds)
    except TimeoutError:
        raise QueryError(
            f"the query took longer than {seconds:g} seconds; make it narrower"
        ) from None
    columns = fields_of(fields)
    notes = []
    for info in found:
        row: dict[str, Any] = {"name": info.title, "path": info.path}
        properties = index.properties(info.path) if columns else {}
        lowered = {k.casefold(): v for k, v in properties.items()}
        for column in columns:
            kind = SPECIAL.get(column.casefold())
            if kind == "tag":
                row[column] = index.tags_of(info.path)
            elif kind == "folder":
                row[column] = info.folder
            elif kind == "modified":
                row[column] = dt.datetime.fromtimestamp(info.modified).date().isoformat()
            elif kind == "words":
                row[column] = info.words
            elif kind in ("name", "path"):
                row[column] = info.title if kind == "name" else info.path
            else:
                row[column] = lowered.get(column.casefold())
        notes.append(row)
    return {"fields": columns, "count": len(notes), "notes": notes}
