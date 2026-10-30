"""The tool-use exam: does a model call Starlight's tools the right way? Eight small tasks,
each with a set of stand-in tools (nothing runs: each tool gives a fixed answer), checked
for the tool chosen, its arguments, and an answer when no tool is needed. A model that
fails many of them will not get far with Starlight, however fast it is."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from starlight.core.model import Message, ModelProvider, ToolCall
from starlight.core.stats import Stats

SYSTEM = (
    "You are Starlight, an assistant on the user's PC. Use a tool when one fits the "
    "request; answer directly when none is needed. Answer in the user's language."
)


def _tool(name: str, description: str, /, **params: tuple[str, str]) -> dict[str, Any]:
    """A stand-in tool: params map a name to (JSON type, description)."""
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {
                key: {"type": kind, "description": text} for key, (kind, text) in params.items()
            },
            "required": list(params),
        },
    }


TOOLS = [
    _tool("weather.forecast", "The weather forecast for a place.",
          place=("string", "a town or city"), days=("integer", "how many days ahead, 1-7")),
    _tool("calc.calculate", "Work out a calculation exactly.",
          expression=("string", "the calculation, e.g. 12*(3+4)")),
    _tool("reminders.add", "Remind the user of something later.",
          text=("string", "what to remind of"), when=("string", "when, e.g. 'in 2 hours'")),
    _tool("apps.open", "Start an installed program.", name=("string", "the program")),
    _tool("media.volume", "Set the speaker volume.", level=("integer", "0 to 100 percent")),
    _tool("web.search", "Search the web.", query=("string", "what to search for")),
    _tool("clock.time_in", "The time now in another place.", place=("string", "a city")),
    _tool("notes.note", "Write a short note.", text=("string", "the note")),
]  # fmt: skip

ANSWERS = {  # what each stand-in tool "returns"
    "weather.forecast": "Hamburg tomorrow: 14 °C, light rain, wind 20 km/h.",
    "calc.calculate": "434",
    "reminders.add": "Reminder set.",
    "apps.open": "Spotify is open.",
    "media.volume": "Volume is 30 %.",
    "web.search": "Munich - Wikipedia: Munich has a population of 1,512,491 (2023).",
    "clock.time_in": "It is 21:05 in Tokyo.",
    "notes.note": "Noted.",
}


def _text(value: Any) -> str:
    return str(value or "").lower()


def _called(calls: list[ToolCall], name: str) -> ToolCall | None:
    return next((c for c in calls if c.name == name), None)


type Check = Callable[[list[ToolCall], str], str]  # "" when passed, else what went wrong


def _expects(name: str, test: Callable[[dict[str, Any]], bool], wanted: str) -> Check:
    def check(calls: list[ToolCall], answer: str) -> str:
        call = _called(calls, name)
        if call is None:
            used = ", ".join(c.name for c in calls)
            return f"did not use {name}" + (f" (used {used})" if used else " (no tool)")
        return "" if test(call.arguments) else f"{name} with the wrong arguments: {wanted}"

    return check


def _no_tool(calls: list[ToolCall], answer: str) -> str:
    if calls:
        return f"used a tool it did not need ({calls[0].name})"
    return "" if "bonjour" in answer.lower() else "no fitting answer"


def _volume(args: dict[str, Any]) -> bool:
    level = args.get("level")
    return level == 30 or (isinstance(level, str) and level.strip(" %") == "30")


def _searched_and_answered(calls: list[ToolCall], answer: str) -> str:
    problem = _expects(
        "web.search",
        lambda a: any(w in _text(a.get("query")) for w in ("munich", "münchen", "muenchen")),
        "the search should name Munich",
    )(calls, answer)
    if problem:
        return problem
    figures = ("1,512,491", "1.512.491", "1512491", "1.5 million", "1,5 million", "1.51")
    return "" if any(f in answer for f in figures) else "did not answer with what it found"


def _both(calls: list[ToolCall], answer: str) -> str:
    time = _expects(
        "clock.time_in", lambda a: "tok" in _text(a.get("place")), "the place is Tokyo"
    )(calls, answer)
    note = _expects(
        "notes.note", lambda a: "milk" in _text(a.get("text")), "the note is 'buy milk'"
    )(calls, answer)
    return "; ".join(p for p in (time, note) if p)


@dataclass
class Task:
    name: str
    request: str
    check: Check


TASKS = [
    Task("picks the right tool", "What will the weather be like in Hamburg tomorrow?",
         _expects("weather.forecast", lambda a: "hamburg" in _text(a.get("place")),
                  "the place is Hamburg")),
    Task("calculates instead of guessing", "What is 17.5 % of 2480?",
         _expects("calc.calculate",
                  lambda a: "2480" in _text(a.get("expression")).replace(",", "")
                  and "17.5" in _text(a.get("expression")).replace(",", "."),
                  "the calculation needs 17.5 and 2480")),
    Task("answers without a tool when none is needed", "Translate 'good morning' into French.",
         _no_tool),
    Task("fills in two arguments", "Remind me in 2 hours to call Mum.",
         _expects("reminders.add",
                  lambda a: "mum" in _text(a.get("text")) and "2" in _text(a.get("when")),
                  "text 'call Mum', when 'in 2 hours'")),
    Task("understands German", "Mach bitte Spotify auf.",
         _expects("apps.open", lambda a: "spotify" in _text(a.get("name")), "the program")),
    Task("gives a number as a number", "Turn the volume down to 30 percent.",
         _expects("media.volume", _volume, "level 30")),
    Task("uses what a tool found", "Look up how many people live in Munich and tell me.",
         _searched_and_answered),
    Task("does two things at once", "What time is it in Tokyo? And note down 'buy milk'.",
         _both),
]  # fmt: skip


@dataclass
class Result:
    task: str
    passed: bool
    note: str = ""
    seconds: float = 0.0
    calls: list[str] = field(default_factory=list)


async def take_task(provider: ModelProvider, task: Task, rounds: int = 3) -> Result:
    """One task: the model may call tools for up to ``rounds`` turns, then it is checked."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    messages = [Message("system", SYSTEM), Message("user", task.request)]
    calls: list[ToolCall] = []
    answer = ""
    for _ in range(rounds):
        response = await provider.chat(messages, TOOLS)
        answer = response.content or ""
        if not response.tool_calls:
            break
        calls += response.tool_calls
        messages.append(Message("assistant", answer, tool_calls=response.tool_calls))
        for call in response.tool_calls:
            known = call.name in ANSWERS
            text = ANSWERS[call.name] if known else f"ERROR: unknown tool '{call.name}'"
            messages.append(Message("tool", text, tool_call_id=call.id, name=call.name))
    unknown = [c.name for c in calls if c.name not in ANSWERS]
    note = f"called a tool that does not exist: {unknown[0]}" if unknown else ""
    note = note or task.check(calls, answer)
    return Result(
        task.name,
        not note,
        note,
        round(loop.time() - started, 1),
        [c.name for c in calls],
    )


def verdict(score: int, total: int) -> str:
    if score >= total - 1:
        return "Good: this model uses Starlight's tools reliably."
    if score >= total * 0.6:
        return "Usable: it gets most tool calls right; expect the odd miss."
    return (
        "Weak at tool use: many of Starlight's abilities will not work well with it. A model "
        "trained for tool use (Qwen3, Qwen2.5 Instruct, Mistral Small ...) does better."
    )


async def exam(
    provider: ModelProvider,
    stats: Stats | None = None,
    timeout: float = 180.0,
    on_result: Callable[[Result], None] | None = None,
) -> dict[str, Any]:
    """Take every task; a task that errors or takes longer than ``timeout`` fails."""
    results: list[Result] = []
    for task in TASKS:
        try:
            result = await asyncio.wait_for(take_task(provider, task), timeout)
        except TimeoutError:
            result = Result(task.name, False, f"no answer within {timeout:.0f} s")
        except Exception as exc:  # the model server refused or is not there
            result = Result(task.name, False, f"{type(exc).__name__}: {exc}"[:200])
        results.append(result)
        if on_result is not None:
            on_result(result)
    score = sum(r.passed for r in results)
    if stats is not None:
        stats.record("model.exam_score", score, provider=provider.name, of=len(results))
    return {
        "provider": provider.name,
        "score": score,
        "of": len(results),
        "verdict": verdict(score, len(results)),
        "tasks": [r.__dict__ for r in results],
    }
