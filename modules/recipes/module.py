"""Recipes as Markdown files in your Starlight folder (Recipes/<name>.md), so you can read and
edit them anywhere. Starlight finds them, scales the amounts to more or fewer servings, and
puts several recipes' ingredients together as one shopping list.

A recipe file:

    # Pancakes
    Serves: 4

    ## Ingredients
    - 250 g flour
    - 1/2 l milk

    ## Steps
    1. Mix ...
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
# An amount at the start of an ingredient: 2, 1.5, 1,5, 1/2, 1 1/2, ½
AMOUNT = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?|[½¼¾⅓⅔])(?=\s|[a-zA-Z]|$)")
VULGAR = {"½": Fraction(1, 2), "¼": Fraction(1, 4), "¾": Fraction(3, 4),
          "⅓": Fraction(1, 3), "⅔": Fraction(2, 3)}  # fmt: skip


def read_amount(text: str) -> Fraction:
    text = text.strip()
    if text in VULGAR:
        return VULGAR[text]
    if " " in text:
        whole, part = text.split()
        return Fraction(int(whole)) + Fraction(part)
    return Fraction(text.replace(",", "."))


def show_amount(value: Fraction) -> str:
    """3 -> "3", 1.5 -> "1.5", 1/3 -> "1/3": what a cook reads easily."""
    if value.denominator == 1:
        return str(value.numerator)
    if value.denominator in (2, 4, 5, 10, 20):
        return f"{float(value):g}"
    whole, rest = divmod(value.numerator, value.denominator)
    part = f"{rest}/{value.denominator}"
    return f"{whole} {part}" if whole else part


def scale(ingredient: str, factor: Fraction) -> str:
    match = AMOUNT.match(ingredient)
    if match is None or factor == 1:
        return ingredient.strip()
    amount = read_amount(match.group(1)) * factor
    return show_amount(amount) + ingredient[match.end() :].rstrip()


def parse_recipe(text: str) -> dict[str, Any]:
    title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "")
    serves = re.search(r"(?im)^\s*(?:serves|portionen|servings)\s*:\s*(\d+)", text)
    section = ""
    ingredients: list[str] = []
    steps: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        bullet = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$", line)  # a list item
        if bullet is None:
            continue
        item = bullet.group(1).strip()
        if section.startswith(("ingredient", "zutat")):
            ingredients.append(item)
        elif section.startswith(("step", "method", "zubereitung", "schritt", "instruction")):
            steps.append(item)
    return {"title": title, "serves": int(serves.group(1)) if serves else 0,
            "ingredients": ingredients, "steps": steps}  # fmt: skip


class Config(BaseModel):
    folder: str = "Recipes"


class RecipesModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    @property
    def folder(self) -> Path:
        return self.ctx.output_dir / (_BAD.sub("", self.config.folder).strip() or "Recipes")

    def _file(self, title: str) -> Path:
        name = _BAD.sub("", title).strip().strip(".")[:80]
        if _RESERVED.match(name):  # "Con", "Nul": names Windows keeps for devices
            name = f"_{name}"
        if not name:
            raise ToolError("give the recipe a name")
        return self.folder / f"{name}.md"

    def _find(self, title: str) -> Path:
        exact = self._file(title)
        if exact.is_file():
            return exact
        wanted = title.strip().casefold()
        found = (
            [p for p in self.folder.glob("*.md") if wanted in p.stem.casefold()]
            if self.folder.is_dir()
            else []
        )
        if len(found) == 1:
            return found[0]
        if found:
            raise ToolError(
                f"'{title}' matches several recipes: " + ", ".join(sorted(p.stem for p in found))
            )
        raise ToolError(f"no recipe called '{title}'")

    @tool
    def save(
        self, title: str, ingredients: list[str], steps: list[str], serves: int = 2
    ) -> dict[str, Any]:
        """Save a recipe: its ingredients (with amounts, e.g. "250 g flour"), the steps,
        and for how many people the amounts are."""
        if not ingredients:
            raise ToolError("a recipe needs its ingredients")
        path = self._file(title)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# {title.strip()}", f"Serves: {max(1, serves)}", "", "## Ingredients"]
        lines += [f"- {i.strip()}" for i in ingredients if i.strip()]
        lines += ["", "## Steps"] + [
            f"{n}. {s.strip()}" for n, s in enumerate(steps, 1) if s.strip()
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"saved": str(path)}

    @tool
    def find(self, text: str = "") -> dict[str, Any]:
        """Recipes whose name or ingredients mention something (all of them if empty)."""
        wanted = text.strip().casefold()
        found = []
        for path in sorted(self.folder.glob("*.md")) if self.folder.is_dir() else []:
            content = path.read_text(encoding="utf-8")
            if not wanted or wanted in content.casefold():
                found.append(path.stem)
        return {"count": len(found), "recipes": found}

    @tool
    def show(self, title: str, serves: int = 0) -> dict[str, Any]:
        """A recipe, with its amounts for another number of people if asked."""
        recipe = parse_recipe(self._find(title).read_text(encoding="utf-8"))
        if serves and recipe["serves"]:
            factor = Fraction(serves, recipe["serves"])
            recipe["ingredients"] = [scale(i, factor) for i in recipe["ingredients"]]
            recipe["scaled_from"], recipe["serves"] = recipe["serves"], serves
        return recipe

    @tool
    def shopping_list(self, titles: list[str], serves: int = 0) -> dict[str, Any]:
        """The ingredients of several recipes as one list (for that many people each, if
        said), the same ingredient with the same unit added up."""
        totals: dict[str, Fraction] = {}
        others: list[str] = []
        for title in titles:
            recipe = self.show(title, serves)
            for ingredient in recipe["ingredients"]:
                match = AMOUNT.match(ingredient)
                if match is None:
                    others.append(ingredient)
                    continue
                rest = ingredient[match.end() :].strip()
                totals[rest] = totals.get(rest, Fraction(0)) + read_amount(match.group(1))
        items = [f"{show_amount(amount)} {rest}".strip() for rest, amount in totals.items()]
        items += [o for o in dict.fromkeys(others) if o not in items]
        return {"for": titles, "items": items}
