"""Exact answers instead of the model's guesses: arithmetic, unit conversion and date maths.

`calculate` evaluates a formula safely (it is parsed, never run as code), `convert` changes
units (length, weight, volume, temperature, data, speed, time, area, energy, pressure) and
the date tools count days and add weeks or months. Local, no network.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.schedule import ScheduleError, parse_when

_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sqrt": math.sqrt,
    "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "sin": lambda x: math.sin(math.radians(x)),
    "cos": lambda x: math.cos(math.radians(x)),
    "tan": lambda x: math.tan(math.radians(x)),
    "asin": lambda x: math.degrees(math.asin(x)),
    "acos": lambda x: math.degrees(math.acos(x)),
    "atan": lambda x: math.degrees(math.atan(x)),
    "log": math.log10,
    "log10": math.log10,
    "log2": math.log2,
    "ln": math.log,
    "exp": math.exp,
    "abs": abs,
    "round": lambda x, digits=0: _round_half_up(x, digits),
    "floor": math.floor,
    "ceil": math.ceil,
    "min": min,
    "max": max,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "lcm": math.lcm,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
MAX_EXPONENT = 10_000
MAX_FACTORIAL = 1_000


class Config(BaseModel):
    max_digits: int = Field(default=4000, ge=10)


class CalcModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    def _today(self) -> date:
        return datetime.now().date()

    @tool
    def calculate(self, expression: str) -> dict[str, Any]:
        """Work out a formula exactly, e.g. "(17.5 * 3) / 4", "2^10", "sqrt(2)",
        "15% of 80", "19% * 249.90", "sin(30)" (degrees), "log(1000)", "factorial(20)".
        Use this for any arithmetic instead of working it out in your head."""
        result = evaluate(expression, self.config.max_digits)
        return {"expression": expression.strip(), "result": result, "text": format_number(result)}

    @tool
    def convert(self, value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
        """Convert between units, e.g. 5 km -> mi, 72 F -> C, 2.5 GB -> MB, 100 km/h -> mph,
        3 cups -> ml, 1500 kcal -> kJ, 1013 hPa -> bar."""
        result = convert(value, from_unit, to_unit)
        return {
            "value": value,
            "from": from_unit,
            "to": to_unit,
            "result": result,
            "text": f"{format_number(value)} {from_unit} = {format_number(result)} {to_unit}",
        }

    @tool
    def days_between(self, start: str, end: str = "today") -> dict[str, Any]:
        """Days between two dates ("2026-01-01", "24.12.", "24.12.2026", "today",
        "friday"), with weeks and weekdays (Monday-Friday) counted too."""
        first, second = parse_date(start, self._today()), parse_date(end, self._today())
        days = (second - first).days
        low, high = sorted((first, second))
        workdays = sum(1 for n in range((high - low).days) if (low + timedelta(n)).weekday() < 5)
        return {
            "from": first.isoformat(),
            "to": second.isoformat(),
            "days": days,
            "weeks": round(days / 7, 2),
            "weekdays": workdays,
        }

    @tool
    def days_until(self, day: str) -> dict[str, Any]:
        """How many days until a date ("24.12." is this year's or next year's)."""
        target = parse_date(day, self._today(), future=True)
        days = (target - self._today()).days
        return {"date": target.isoformat(), "weekday": target.strftime("%A"), "days": days}

    @tool
    def date_add(self, day: str, amount: str) -> dict[str, Any]:
        """A date plus or minus a span: amount like "3 weeks", "-10 days", "2 months",
        "1 year", "90 days"."""
        start = parse_date(day, self._today())
        match = re.fullmatch(r"\s*([+-]?\d+)\s*([a-z]+)\s*", amount.lower())
        if not match:
            raise ToolError("give an amount like '3 weeks', '-10 days' or '2 months'")
        count, unit = int(match.group(1)), match.group(2).rstrip("s")
        if unit in ("day", "d"):
            result = start + timedelta(days=count)
        elif unit in ("week", "w", "wk"):
            result = start + timedelta(weeks=count)
        elif unit in ("month", "mo"):
            result = add_months(start, count)
        elif unit in ("year", "y", "yr"):
            result = add_months(start, 12 * count)
        else:
            raise ToolError("use days, weeks, months or years")
        return {"date": result.isoformat(), "weekday": result.strftime("%A")}


# -- arithmetic --------------------------------------------------------------------------


def _round_half_up(value: float, digits: int = 0) -> int | float:
    """Rounding as taught at school: 2.5 -> 3 and 0.125 -> 0.13 (Python's round gives 2 and
    0.12: it rounds halves to even, and 0.125 is stored a hair below)."""
    digits = int(digits)
    if isinstance(value, int) and digits >= 0:
        return value
    if not isinstance(value, int | float) or (
        isinstance(value, float) and not math.isfinite(value)
    ):
        raise ValueError("only a finite real number can be rounded")
    number = Decimal(repr(value))
    places = number.adjusted() + digits + 3  # digits the result has: 1e30 needs 31
    with localcontext(prec=max(28, places)):
        exact = number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return int(exact) if digits <= 0 else float(exact)


def _prepare(expression: str) -> str:
    text = expression.strip().replace("^", "**").replace("\u00d7", "*").replace("÷", "/")
    text = text.replace("\u2212", "-").rstrip("=").strip()
    # "15% of 80" -> (15/100*80); "249.90 + 19%" -> 249.90 plus 19% of it, as on a
    # calculator; a lone "19%" -> (19/100)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"(\1/100)*", text, flags=re.IGNORECASE)
    # (as on a calculator, of everything before it: "100 / 4 + 25%" is 25 plus 25%)
    text = re.sub(r"^(.*[\w).])\s*([+-])\s*(\d+(?:\.\d+)?)\s*%\s*$", r"(\1)*(1\2\3/100)", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", text)
    return re.sub(r"\bmod\b", "%", text)  # "17 mod 5" is the remainder


def evaluate(expression: str, max_digits: int = 4000) -> int | float:
    text = _prepare(expression)
    if not text:
        raise ToolError("give a formula, e.g. '(3 + 4) * 2'")
    if len(text) > 500:
        raise ToolError("that formula is too long")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"could not read the formula: {exc.msg}") from exc

    def walk(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return node.value
        if isinstance(node, ast.Name) and node.id.lower() in _CONSTANTS:
            return _CONSTANTS[node.id.lower()]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Pow) and (
                abs(right) > MAX_EXPONENT
                or (abs(left) > 1 and right > 0 and math.log10(abs(left)) * right > max_digits)
            ):
                raise ToolError("that power is too large")  # checked before working it out
            return _BINARY[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](walk(node.operand))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.lower() in _FUNCTIONS
            and not node.keywords
        ):
            args = [walk(a) for a in node.args]
            if node.func.id.lower() == "factorial" and args and args[0] > MAX_FACTORIAL:
                raise ToolError(f"factorial is limited to {MAX_FACTORIAL}")
            return _FUNCTIONS[node.func.id.lower()](*args)
        raise ToolError(f"'{ast.unparse(node)}' is not something I can calculate")

    try:
        result = walk(tree)
    except ZeroDivisionError as exc:
        raise ToolError("division by zero") from exc
    except (ValueError, ArithmeticError, TypeError) as exc:
        raise ToolError(f"cannot calculate that: {exc}") from exc
    if isinstance(result, complex):  # (-8)^0.5; sqrt(-8) refuses as well
        raise ToolError("the result is not a real number")
    if isinstance(result, float) and not math.isfinite(result):
        raise ToolError("the result is too large (or not defined)")
    if isinstance(result, int) and result.bit_length() * math.log10(2) > max_digits:
        raise ToolError(f"the result has more than {max_digits} digits")
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        return int(result)
    return result  # type: ignore[no-any-return]


def format_number(value: float) -> str:
    if isinstance(value, int):
        return f"{value:,}".replace(",", " ") if abs(value) >= 10_000 else str(value)
    if value != 0 and (abs(value) >= 1e15 or abs(value) < 1e-6):
        return f"{value:.6e}"
    return f"{value:.10f}".rstrip("0").rstrip(".")


# -- units -------------------------------------------------------------------------------

# Each unit: (category, factor to the base unit). Temperature is handled separately.
_UNITS: dict[str, tuple[str, float]] = {}


def _add(category: str, factor: float, *names: str) -> None:
    for name in names:
        _UNITS[name] = (category, factor)


_add("length", 1, "m", "meter", "meters", "metre", "metres")
_add("length", 1000, "km", "kilometer", "kilometers", "kilometre", "kilometres")
_add("length", 0.01, "cm", "centimeter", "centimeters")
_add("length", 0.001, "mm", "millimeter", "millimeters")
_add("length", 1609.344, "mi", "mile", "miles")
_add("length", 0.9144, "yd", "yard", "yards")
_add("length", 0.3048, "ft", "foot", "feet")
_add("length", 0.0254, "in", "inch", "inches", '"')
_add("length", 1852, "nmi", "nautical mile", "nautical miles")
_add("mass", 1, "kg", "kilogram", "kilograms", "kilo", "kilos")
_add("mass", 0.001, "g", "gram", "grams")
_add("mass", 1e-6, "mg", "milligram", "milligrams")
_add("mass", 1000, "t", "tonne", "tonnes", "ton", "tons")
_add("mass", 0.45359237, "lb", "lbs", "pound", "pounds")
_add("mass", 0.028349523125, "oz", "ounce", "ounces")
_add("mass", 6.35029318, "st", "stone")
_add("volume", 1, "l", "liter", "liters", "litre", "litres")
_add("volume", 0.001, "ml", "milliliter", "milliliters", "millilitre")
_add("volume", 0.01, "cl", "centiliter")
_add("volume", 1000, "m3", "cubic meter", "cubic meters")
_add("volume", 3.785411784, "gal", "gallon", "gallons")
_add("volume", 0.946352946, "qt", "quart", "quarts")
_add("volume", 0.473176473, "pt", "pint", "pints")
_add("volume", 0.2365882365, "cup", "cups")
_add("volume", 0.0295735295625, "floz", "fl oz", "fluid ounce", "fluid ounces")
_add("volume", 0.01478676478125, "tbsp", "tablespoon", "tablespoons")
_add("volume", 0.00492892159375, "tsp", "teaspoon", "teaspoons")
_add("data", 1, "b", "byte", "bytes")
_add("data", 0.125, "bit", "bits")
for _i, _p in enumerate(("k", "m", "g", "t", "p"), start=1):
    _add("data", 1000.0**_i, f"{_p}b")
    _add("data", 1024.0**_i, f"{_p}ib")
    _add("data", 1000.0**_i / 8, f"{_p}bit")
_add("speed", 1, "m/s", "mps")
_add("speed", 1 / 3.6, "km/h", "kmh", "kph")
_add("speed", 0.44704, "mph")
_add("speed", 1852 / 3600, "kn", "knot", "knots")
_add("time", 1, "s", "sec", "second", "seconds")
_add("time", 0.001, "ms", "millisecond", "milliseconds")
_add("time", 60, "min", "minute", "minutes")
_add("time", 3600, "h", "hr", "hour", "hours")
_add("time", 86400, "d", "day", "days")
_add("time", 604800, "wk", "week", "weeks")
_add("time", 31557600, "yr", "year", "years")
_add("area", 1, "m2", "sqm", "square meter", "square meters")
_add("area", 1e6, "km2", "square kilometer", "square kilometers")
_add("area", 1e4, "ha", "hectare", "hectares")
_add("area", 4046.8564224, "acre", "acres")
_add("area", 0.09290304, "ft2", "sqft", "square foot", "square feet")
_add("energy", 1, "j", "joule", "joules")
_add("energy", 1000, "kj", "kilojoule", "kilojoules")
_add("energy", 4.184, "cal", "calorie", "calories")
_add("energy", 4184, "kcal", "kilocalorie", "kilocalories")
_add("energy", 3600, "wh")
_add("energy", 3.6e6, "kwh")
_add("pressure", 1, "pa", "pascal")
_add("pressure", 100, "hpa", "mbar")
_add("pressure", 1e5, "bar")
_add("pressure", 6894.757293168, "psi")
_add("pressure", 101325, "atm")
_TEMPERATURE = {
    "c": "c", "°c": "c", "celsius": "c",
    "f": "f", "°f": "f", "fahrenheit": "f",
    "k": "k", "kelvin": "k",
}  # fmt: skip


def _unit(name: str) -> str:
    key = name.strip().lower().replace("²", "2").replace("³", "3").replace("per", "/")
    return re.sub(r"\s*/\s*", "/", key)


def convert(value: float, from_unit: str, to_unit: str) -> float:
    source, target = _unit(from_unit), _unit(to_unit)
    if source in _TEMPERATURE and target in _TEMPERATURE:
        celsius = {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[_TEMPERATURE[source]]
        result = {"c": celsius, "f": celsius * 9 / 5 + 32, "k": celsius + 273.15}[
            _TEMPERATURE[target]
        ]
        return round(result, 10)
    if source not in _UNITS:
        raise ToolError(f"unknown unit '{from_unit}'")
    if target not in _UNITS:
        raise ToolError(f"unknown unit '{to_unit}'")
    (kind, factor), (other, to_factor) = _UNITS[source], _UNITS[target]
    if kind != other:
        raise ToolError(f"cannot convert {kind} ({from_unit}) to {other} ({to_unit})")
    return float(f"{value * factor / to_factor:.12g}")  # 12 significant digits: 1 B = 1e-12 TB


# -- dates -------------------------------------------------------------------------------


def parse_date(text: str, today: date, future: bool = False) -> date:
    """ISO dates, 24.12.2026, 24.12. (this year; with ``future`` the next one), today,
    tomorrow, yesterday, weekday names."""
    raw = text.strip().lower()
    if raw in ("", "today", "now"):
        return today
    if raw == "yesterday":
        return today - timedelta(days=1)
    short = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?", raw)
    if short:
        try:
            day = date(today.year, int(short.group(2)), int(short.group(1)))
        except ValueError as exc:
            raise ToolError(f"'{text}' is not a valid date") from exc
        if future and day < today:
            for year in range(today.year + 1, today.year + 9):  # 29 February: the next leap year
                try:
                    return day.replace(year=year)
                except ValueError:
                    continue
        return day
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    try:
        return parse_when(raw, datetime.combine(today, datetime.min.time())).date()
    except ScheduleError as exc:
        raise ToolError(f"could not understand the date '{text}'; use e.g. 2026-12-24") from exc


def add_months(start: date, months: int) -> date:
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    return start.replace(year=year, month=month, day=min(start.day, days_in_month(year, month)))


def days_in_month(year: int, month: int) -> int:
    following = date(year + month // 12, month % 12 + 1, 1)
    return (following - timedelta(days=1)).day
