"""What you spend: each expense with its day, amount, what it was and a category, kept in
data/modules/expenses. A receipt (a photo, a scan or a PDF) can be read with the model: it
finds the shop, the day and the total. Each month is summed up by category and against your
budget, and can be exported as a CSV file for a spreadsheet.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json
from starlight.core.model import Message
from starlight.core.safepaths import PathGuard

IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
READ_RECEIPT = (
    "This is a receipt or a bill. Answer with JSON only: "
    '{"shop": "...", "day": "YYYY-MM-DD", "total": 12.34, "category": "one of: CATEGORIES"}. '
    "total is the amount paid, as a number. Use null for what you cannot read."
)


def parse_amount(value: Any) -> float:
    """12.5, "12,50", "1.234,56 €", "$9.99" -> a number; ToolError if it is not one."""
    if isinstance(value, int | float):
        return round(float(value), 2)
    text = re.sub(r"[^\d,.\-]", "", str(value))
    if "," in text and "." in text:  # the last one is the decimal mark
        text = (
            text.replace(".", "").replace(",", ".")
            if text.rfind(",") > text.rfind(".")
            else text.replace(",", "")
        )
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return round(float(text), 2)
    except ValueError:
        raise ToolError(f"'{value}' is not an amount") from None


def parse_day(text: str, today: dt.date) -> dt.date:
    raw = text.strip().lower()
    if raw in ("", "today", "heute"):
        return today
    if raw in ("yesterday", "gestern"):
        return today - dt.timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?", raw)
    if match:
        return dt.date(today.year, int(match.group(2)), int(match.group(1)))
    raise ToolError("which day? Say today, yesterday or a date like 2026-10-30 or 30.10.")


class Config(BaseModel):
    currency: str = "EUR"
    monthly_budget: float = Field(default=0, ge=0)
    categories: list[str] = Field(
        default_factory=lambda: [
            "groceries", "eating out", "household", "transport", "health", "clothes",
            "leisure", "gifts", "bills", "other",
        ]
    )  # fmt: skip


class ExpensesModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "expenses.json"

    def _all(self) -> list[dict[str, Any]]:
        return load_json(self.file, [])

    def _category(self, category: str, what: str) -> str:
        wanted = category.strip().lower()
        if wanted in self.config.categories:
            return wanted
        for known in self.config.categories:  # "Groceries at Aldi" -> groceries
            if known in wanted or known in what.lower():
                return known
        return wanted or "other"

    @tool
    def add(self, amount: str, what: str, category: str = "", day: str = "") -> dict[str, Any]:
        """Note an expense: how much, what for, a category (groceries, eating out,
        household, transport, health, clothes, leisure, gifts, bills, other) and the day."""
        entry = {
            "id": uuid.uuid4().hex[:6],
            "day": parse_day(day, dt.date.today()).isoformat(),
            "amount": parse_amount(amount),
            "what": what.strip() or "expense",
            "category": self._category(category, what),
        }
        entries = self._all()
        entries.append(entry)
        save_json(self.file, entries)
        return {"added": entry, "currency": self.config.currency}

    @tool
    def remove(self, id: str) -> dict[str, Any]:
        """Delete an expense by its id (from expenses.month)."""
        entries = self._all()
        kept = [e for e in entries if e.get("id") != id.strip()]
        if len(kept) == len(entries):
            raise ToolError(f"no expense with the id {id}")
        save_json(self.file, kept)
        return {"removed": id}

    def _month(self, month: str) -> tuple[str, list[dict[str, Any]]]:
        month = month.strip() or dt.date.today().strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ToolError("the month as YYYY-MM, e.g. 2026-10")
        return month, [e for e in self._all() if str(e.get("day", "")).startswith(month)]

    @tool
    def month(self, month: str = "") -> dict[str, Any]:
        """A month's expenses (this month if not said, else YYYY-MM): each one, the total
        per category, and what is left of your monthly budget."""
        month, entries = self._month(month)
        by_category: dict[str, float] = {}
        for entry in entries:
            key = str(entry.get("category") or "other")
            by_category[key] = round(by_category.get(key, 0) + float(entry["amount"]), 2)
        total = round(sum(by_category.values()), 2)
        out: dict[str, Any] = {
            "month": month,
            "currency": self.config.currency,
            "total": total,
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "expenses": sorted(entries, key=lambda e: e["day"]),
        }
        if self.config.monthly_budget:
            out["budget"] = self.config.monthly_budget
            out["left"] = round(self.config.monthly_budget - total, 2)
        return out

    @tool
    def export_csv(self, month: str = "") -> dict[str, Any]:
        """Save a month's expenses as a CSV file (for Excel) in your Starlight folder."""
        month, entries = self._month(month)
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["day", "amount", "currency", "what", "category"])
        for entry in sorted(entries, key=lambda e: e["day"]):
            amount = f"{entry['amount']:.2f}"
            writer.writerow([entry["day"], amount, self.config.currency, entry["what"],
                             entry["category"]])  # fmt: skip
        target = self.ctx.output_dir / f"Expenses {month}.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(buffer.getvalue(), encoding="utf-8-sig")  # (Excel reads the BOM)
        return {"saved": str(target), "count": len(entries)}

    async def _receipt_text(self, file: Path) -> tuple[str, list[bytes]]:
        """The receipt's text (a PDF) or its picture (a photo or scan) for the model."""
        if file.suffix.lower() in IMAGES:
            return "", [file.read_bytes()]
        if file.suffix.lower() == ".pdf":
            try:
                import pypdf
            except ImportError:
                raise ToolError("reading PDF receipts needs the 'pdf' extra (pypdf)") from None
            reader = pypdf.PdfReader(str(file))
            return "\n".join(page.extract_text() or "" for page in reader.pages[:3])[:6000], []
        return file.read_text(encoding="utf-8", errors="replace")[:6000], []

    @tool
    async def read_receipt(self, path: str, add: bool = True) -> dict[str, Any]:
        """Read a receipt or bill (a photo, a scan or a PDF): the shop, the day and the
        total. With add (the default) it is noted as an expense right away."""
        found = PathGuard(self.ctx.paths.root, self.ctx.output_dir).resolve(path)
        file = found.path
        if not file.is_file():
            raise ToolError(f"{file} does not exist")
        if file.stat().st_size > 30 * 1024**2:
            raise ToolError("that file is too large for a receipt (over 30 MB)")
        if not found.free and not await self.ctx.ui.confirm(
            f"Let Starlight read this receipt?\n  {file}"
        ):
            raise ToolError(f"the user did not allow reading {file}")
        text, images = await self._receipt_text(file)
        model = self.ctx.model_for("vision" if images else "summary")
        if model is None:
            raise ToolError("no model is configured")
        prompt = READ_RECEIPT.replace("CATEGORIES", ", ".join(self.config.categories))
        reply = await model.chat(
            [Message("user", f"{prompt}\n\n{text}".strip(), images=images)], []
        )
        match = re.search(r"\{.*\}", str(reply.content), re.DOTALL)
        try:
            data = json.loads(match.group(0)) if match else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict) or data.get("total") in (None, ""):
            raise ToolError("the total could not be read from that receipt")
        shop = str(data.get("shop") or file.stem)
        day = str(data.get("day") or "")
        try:
            when = parse_day(day, dt.date.today()).isoformat()
        except ToolError:
            when = dt.date.today().isoformat()
        total = parse_amount(data["total"])
        category = self._category(str(data.get("category") or ""), shop)
        read = {"shop": shop, "day": when, "total": total, "category": category}
        if not add:
            return {"read": read}
        added = self.add(str(total), shop, category, when)
        return {"read": read, "added": added["added"]}
