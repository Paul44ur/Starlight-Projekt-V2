# ruff: noqa: RUF001
"""Random input for everything that reads text from outside: formulas, prices, feeds,
calendar files, mail, and the text a model streams. Whatever comes in, the parser either
answers or raises its documented error (ToolError) - never anything else, and never hangs.

Hypothesis generates the inputs and shrinks a failure to the shortest one. A failing
example is printed; add it to the matching ``@example`` so it stays a regression test."""

from __future__ import annotations

import contextlib
import importlib.util
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from types import ModuleType

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from starlight.core.modules import discover, import_module_class
from starlight.core.tools import ToolError
from tests.conftest import REPO

settings.register_profile(
    "starlight",
    max_examples=300,
    deadline=2000,  # ms: one parse that takes longer than this is a (slow) hang
    suppress_health_check=[HealthCheck.too_slow],
    print_blob=True,
)
settings.register_profile(  # STARLIGHT_FUZZ=deep pytest tests/test_fuzz.py: ten times as many
    "deep", settings.get_profile("starlight"), max_examples=3000
)
settings.load_profile("deep" if os.environ.get("STARLIGHT_FUZZ") == "deep" else "starlight")


def module_of(name: str) -> ModuleType:
    found, _ = discover(REPO / "modules")
    import_module_class(*found[name])
    return next(m for key, m in sys.modules.items() if key.endswith(f"{name}.module"))


def file_module(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


calc = module_of("calc")
watch = module_of("watch")
news = module_of("news")
ics = file_module("starlight_fuzz_ics", "modules/calendar/ics.py")

anything = st.text(max_size=300)
formula_chars = "0123456789+-*/()^%.,eE xsqrtpifactoralnmod!=×÷−"
formulas = st.text(alphabet=formula_chars, max_size=520)

# -- the calculator --------------------------------------------------------------------------------


@given(st.one_of(anything, formulas))
@example("9" * 400)
@example("(" * 300 + "1" + ")" * 300)
@example("-" * 480 + "1")
@example("2**2**2**2**2")
@example("1e999 * 1e999")
@example("10**10**10")
@example("factorial(99999)")
@example("0**-1")
def test_the_calculator_answers_or_says_why_not(text: str) -> None:
    try:
        value = calc.evaluate(text)
    except ToolError:
        return
    assert isinstance(value, int | float)
    calc.format_number(value)  # and whatever came out can be shown


@given(
    st.text(max_size=40),
    st.one_of(
        st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 1, 1)),
    ),
    st.booleans(),
)
@example("31.2.", date(2026, 10, 13), True)
@example("29.2.", date(2027, 1, 1), True)
@example("99999999999999999999.1.", date(2026, 1, 1), False)
def test_a_date_is_read_or_refused(text: str, today: date, future: bool) -> None:
    with contextlib.suppress(ToolError):
        assert isinstance(calc.parse_date(text, today, future), date)


@given(st.floats(allow_nan=True, allow_infinity=True), st.text(max_size=12), st.text(max_size=12))
@example(1e308, "km", "mm")
def test_unit_conversion_answers_or_says_why_not(value: float, source: str, target: str) -> None:
    with contextlib.suppress(ToolError):
        calc.convert(value, source, target)


# -- prices and watch conditions -------------------------------------------------------------------


@given(st.one_of(anything, st.text(alphabet="0123456789.,'   -", max_size=60)))
@example("9" * 400)
@example("1.234.567,89,1")
def test_a_number_is_a_finite_number_or_nothing(text: str) -> None:
    value = watch.parse_number(text)
    assert value is None or math.isfinite(value)


@given(
    st.one_of(
        anything,
        st.text(alphabet="0123456789.,€$£ EURUSDabc<>/    ", max_size=120),
    )
)
@example("€" + "9" * 400)
@example("1 299,00 €")
def test_a_price_in_page_text_is_finite_or_nothing(text: str) -> None:
    value = watch.find_price(text)
    assert value is None or math.isfinite(value)


@given(st.text(max_size=40))
@example("9" * 400 + "h")
@example("0m")
@example("every 0 seconds")
def test_a_watch_interval_is_a_positive_number_of_seconds_or_refused(text: str) -> None:
    try:
        seconds = watch.parse_every(text)
    except ToolError:
        return
    assert isinstance(seconds, int) and seconds >= 1


@given(st.text(max_size=80))
@example("price below " + "9" * 400)
@example("price below 1e5")
def test_a_watch_condition_is_read_or_refused(text: str) -> None:
    try:
        kind, value = watch.parse_condition(text)
    except ToolError:
        return
    assert kind in ("changes", "below", "above", "contains", "gone")
    if kind in ("below", "above"):
        assert math.isfinite(value)


# -- news feeds ------------------------------------------------------------------------------------

xml_bits = st.sampled_from(
    ["<rss>", "</rss>", "<channel>", "</channel>", "<item>", "</item>", "<entry>", "</entry>",
     "<title>", "</title>", "<link>", "</link>", "<link href='x'/>", "<pubDate>", "</pubDate>",
     "&amp;", "&#0;", "&#xD800;", "<![CDATA[", "]]>", "<!--", "-->", "<?xml version='1.0'?>",
     "<!DOCTYPE a [<!ENTITY b 'c'>]>", "&b;", "text", " ", "\n"]
)  # fmt: skip


@given(st.one_of(anything, st.lists(xml_bits, max_size=40).map("".join)))
@example(
    "<!DOCTYPE l [<!ENTITY a 'aaaa'><!ENTITY b '&a;&a;&a;&a;'>]>"
    "<rss><item><title>&b;</title></item></rss>"
)
@example("<rss>" + "<item>" * 3000 + "</rss>")
def test_a_news_feed_is_read_or_refused(text: str) -> None:
    try:
        items = news.parse_feed(text)
    except ToolError:
        return
    assert all(isinstance(item["title"], str) and item["title"] for item in items)


# -- calendar files --------------------------------------------------------------------------------

ics_lines = st.sampled_from(
    ["BEGIN:VCALENDAR", "END:VCALENDAR", "BEGIN:VEVENT", "END:VEVENT", "BEGIN:VALARM",
     "END:VALARM", "UID:a", "SUMMARY:Meeting", "SUMMARY;LANGUAGE=de:Besprechung",
     "DTSTART:20261013T100000", "DTSTART:20261013T100000Z", "DTSTART;VALUE=DATE:20261013",
     "DTSTART;TZID=Europe/Vienna:20261013T100000", "DTSTART;TZID=Nowhere/Land:20261013T100000",
     "DTSTART:99999999T999999", "DTEND:20261013T110000", "DURATION:PT1H", "DURATION:P99999D",
     "DURATION:garbage", "RRULE:FREQ=DAILY", "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=5",
     "RRULE:FREQ=MONTHLY;BYDAY=-1FR", "RRULE:FREQ=MONTHLY;BYMONTHDAY=31",
     "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29", "RRULE:FREQ=SECONDLY;INTERVAL=0",
     "RRULE:FREQ=DAILY;INTERVAL=-3", "RRULE:FREQ=DAILY;COUNT=99999999999",
     "RRULE:FREQ=WEEKLY;BYDAY=XX", "RRULE:garbage", "EXDATE:20261014T100000",
     "EXDATE:nonsense", "LOCATION:Room 1\\, 2nd floor", "DESCRIPTION:line\\nbreak", " folded",
     "X-WR-CALNAME:x", ":", "NAME", "", "\t"]
)  # fmt: skip
WINDOW = (datetime(2026, 1, 1), datetime(2027, 1, 1))


@given(st.one_of(anything, st.lists(ics_lines, max_size=30).map("\r\n".join)))
def test_a_calendar_file_never_stops_the_reader(text: str) -> None:
    for event in ics.parse(text):
        occurrences = event.occurrences(*WINDOW)
        assert all(isinstance(moment, datetime) for moment in occurrences)
        assert len(occurrences) <= 100_000


@given(
    st.text(min_size=1, max_size=60),
    st.text(max_size=80),
    st.text(max_size=80),
    st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2090, 1, 1)),
    st.integers(min_value=0, max_value=10**6),
    st.booleans(),
)
@example(
    "a, b; c\\ d\nline two\r\nthree", "Room\\1", "x" * 400, datetime(2026, 10, 13, 10), 60, False
)
def test_a_written_calendar_reads_back_the_same(
    title: str, location: str, notes: str, start: datetime, minutes: int, all_day: bool
) -> None:
    if all_day:
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    start = start.replace(microsecond=0)
    event = ics.Event(
        uid="fuzz-1",
        title=title,
        start=start,
        end=start + timedelta(minutes=minutes),
        all_day=all_day,
        location=location,
        notes=notes,
    )
    [back] = ics.parse(ics.write([event]))
    normal = " ".join
    assert normal(back.title.split()) == normal(title.split())
    assert back.start == start and back.all_day == all_day


# -- mail ------------------------------------------------------------------------------------------

mail = module_of("mail")
header_bits = st.sampled_from(
    ["=?utf-8?q?J=C3=BCrgen?=", "=?utf-8?b?!!!?=", "=?unknown-8bit?q?a?=", "=?x-nonsense?q?a?=",
     "=?utf-8?q?=FF?=", "Plain", "\n continued", "\r\n", "\x00", "ü", "<a@b.c>", "\"Q\" <q@x>"]
)  # fmt: skip


@given(st.lists(header_bits, max_size=12).map(" ".join))
def test_a_mail_header_is_always_readable_text(raw: str) -> None:
    from email import message_from_bytes

    text = mail._decode(raw)
    assert isinstance(text, str) and "\n" not in text
    parsed = message_from_bytes(
        f"Subject: {raw}\r\nFrom: {raw}\r\n\r\nbody".encode("utf-8", "replace")
    )
    mail._decode(parsed["Subject"])
    mail._reply_target(parsed)


@given(st.binary(max_size=600))
@example(
    b"Content-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\n"
    b"Content-Type: text/html\r\n\r\n<p>hi\r\n--x--"
)
@example(b"Content-Type: text/plain; charset=unknown-8bit\r\n\r\n\xff\xfe")
@example(b"Content-Type: text/plain; charset=\xff\r\nContent-Transfer-Encoding: base64\r\n\r\n!!!")
def test_any_message_has_a_body_text(raw: bytes) -> None:
    from email import message_from_bytes

    message = message_from_bytes(raw)
    assert isinstance(mail._body(message), str)
    mail._reply_target(message)


# -- text a model streams (tool calls written as text, loops, role markers) ------------------------

from starlight.models.output import StreamFilter, recover_tool_calls  # noqa: E402

KNOWN = {"hello.greet", "calc.calculate"}
stream_bits = st.sampled_from(
    ['{"name"', '"hello__greet"', '"hello.greet"', ', "arguments": {', '"name": "x"', "}", "{",
     "```json", "```", "```tool_call", "<tool_call>", "</tool_call>", "\nuser\n",
     "\n\U0001f402user\n",
     "\nassistant\n", "text ", "\n", "ï¿½", "�", "a" * 50, " "]
)  # fmt: skip
streamed = st.one_of(anything, st.lists(stream_bits, max_size=40).map("".join))


@given(streamed)
def test_text_that_may_hold_tool_calls_is_always_handled(text: str) -> None:
    calls, before = recover_tool_calls(text, KNOWN)
    assert all(call.name in KNOWN for call in calls)
    assert text.startswith(before) or not calls


@given(st.lists(streamed, min_size=1, max_size=12))
def test_a_stream_filter_never_shows_more_than_it_was_given(pieces: list[str]) -> None:
    stream = StreamFilter()
    shown = "".join(stream.feed(piece) for piece in pieces)
    shown += stream.held()
    stream.call_finished(KNOWN)
    assert len(shown) <= sum(len(p) for p in pieces)
    assert "\nuser\n" not in shown or "\nuser\n" in "".join(pieces)


def test_a_very_long_stream_is_not_scanned_in_quadratic_time() -> None:
    text = '{"name": "x"} ' * 4000  # thousands of candidate blocks
    started = time.monotonic()
    recover_tool_calls(text, KNOWN)
    stream = StreamFilter()
    for start in range(0, len(text), 500):
        stream.feed(text[start : start + 500])
    assert time.monotonic() - started < 5
