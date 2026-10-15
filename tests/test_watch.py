"""The page watcher: change, price and text watches against a stand-in web server, polite
fetching (robots.txt, minimum interval), and delivery plus events on a hit."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.events import Event


class Yes:
    async def confirm(self, question: str) -> bool:
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


class Site:
    """A shop page whose price and stock text the test changes."""

    def __init__(self) -> None:
        self.price = "59,99 €"
        self.stock = "Sold out"
        self.news = "Version 1.0 released"
        self.robots = "User-agent: *\nDisallow: /private\n"
        self.hits: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.hits.append(path)
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if path == "/missing":
            return httpx.Response(404, text="not here")
        page = (
            f"<html><head><title>Fancy Kettle</title></head><body><main>"
            f"<h1>Fancy Kettle</h1><p class='note'>Ships in 2 days, from 4,99 € shipping</p>"
            f"<span class='price'>{self.price}</span><div id='stock'>{self.stock}</div>"
            f"<p class='news'>{self.news}</p></main></body></html>"
        )
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})


@pytest.fixture
def site() -> Site:
    return Site()


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("watch")
    paths.config_file.write_text(
        paths.config_file.read_text() + "http:\n  per_domain_interval: 0\n"
    )
    (paths.module_configs / "watch.yaml").write_text("check_interval: 0\n")
    return paths


def module(k: Kernel) -> Any:
    instance = k.modules.records["watch"].instance
    assert instance is not None
    return instance


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


async def test_price_below_fires_once_and_is_delivered(project: Paths, site: Site) -> None:
    async with Kernel(project, http_transport=httpx.MockTransport(site.handler), ui=Yes()) as k:
        delivered: list[str] = []
        events: list[Event] = []

        async def on_deliver(event: Event) -> None:
            delivered.append(event.data["text"])

        async def on_price(event: Event) -> None:
            events.append(event)

        k.bus.subscribe("deliver", on_deliver, owner="t")
        k.bus.subscribe("watch.price", on_price, owner="t")
        added = await call(
            k, "watch.add", url="https://shop.example/kettle", when="price below 50",
            part=".price",
        )  # fmt: skip
        assert added["watching"] == "Fancy Kettle" and added["price"] == 59.99
        assert "already" not in added
        watch = module(k)
        clock = [watch._now()]
        watch._now = lambda: clock[0]

        clock[0] += 3601
        assert (await watch.check_due())[0]["hit"] is False  # still 59,99
        site.price = "44,90 €"
        clock[0] += 3601
        [seen] = await watch.check_due()
        assert seen["hit"] and seen["price"] == 44.9
        await k.drain()
        assert any("Fancy Kettle is now 44.9 (below 50)" in d for d in delivered)
        assert events[0].data["price"] == 44.9 and events[0].source == "watch"
        clock[0] += 3601
        assert (await watch.check_due())[0]["hit"] is False  # no repeat while it stays low
        assert await watch.check_due() == []  # nothing due yet
        history = await call(k, "watch.history", name="fancy kettle")
        assert [h["value"] for h in history["history"]] == [59.99, 44.9]


async def test_change_and_text_watches(project: Paths, site: Site) -> None:
    async with Kernel(project, http_transport=httpx.MockTransport(site.handler), ui=Yes()) as k:
        await call(k, "watch.add", url="https://shop.example/k", name="News", part=".news")
        await call(
            k, "watch.add", url="https://shop.example/k", name="Stock", when="gone sold out",
            part="#stock", every="15m",
        )  # fmt: skip
        assert (await call(k, "watch.check_now", name="news"))["hit"] is False
        site.news = "Version 2.0 released"
        site.stock = "In stock"
        news = await call(k, "watch.check_now", name="news")
        stock = await call(k, "watch.check_now", name="stock")
        assert news["hit"] and "News changed" in news["message"]
        assert stock["hit"] and "no longer shows 'sold out'" in stock["message"]
        listed = await call(k, "watch.list")
        assert {w["name"]: w["hits"] for w in listed["watches"]} == {"News": 1, "Stock": 1}
        assert (await call(k, "watch.remove", name="News"))["removed"] == "News"
        assert [w["name"] for w in (await call(k, "watch.list"))["watches"]] == ["Stock"]


async def test_polite_and_careful(project: Paths, site: Site) -> None:
    async with Kernel(project, http_transport=httpx.MockTransport(site.handler), ui=Yes()) as k:
        too_often = await k.dispatcher.call(
            "watch.add", {"url": "https://shop.example/k", "every": "5m"}
        )
        assert not too_often.ok and "at least every 15 minutes" in (too_often.error or "")
        private = await k.dispatcher.call("watch.add", {"url": "https://shop.example/private"})
        assert not private.ok and "robots" in (private.error or "").lower()
        assert "/private" not in site.hits  # never fetched
        missing = await k.dispatcher.call("watch.add", {"url": "https://shop.example/missing"})
        assert not missing.ok and "404" in (missing.error or "")
        bad = await k.dispatcher.call("watch.add", {"url": "shop.example", "when": "changes"})
        assert not bad.ok and "https://" in (bad.error or "")
        nothing = await k.dispatcher.call(
            "watch.add", {"url": "https://shop.example/k", "part": ".nope"}
        )
        assert not nothing.ok and "nothing on the page matches" in (nothing.error or "")
        assert (await call(k, "watch.list"))["count"] == 0
        assert k.dispatcher.get("watch.add").permission.value == "confirm"  # type: ignore[union-attr]


async def test_a_watch_added_while_the_page_loads_does_not_undo_other_changes(
    project: Paths, site: Site
) -> None:
    async with Kernel(project, http_transport=httpx.MockTransport(site.handler), ui=Yes()) as k:
        await call(k, "watch.add", url="https://shop.example/k", name="News", part=".news")
        watch = module(k)
        read = watch._read

        async def read_while_news_is_removed(url: str, part: str) -> tuple[str, str]:
            result = await read(url, part)
            watch._save([])  # e.g. a remove finishing while the page was being read
            return result

        watch._read = read_while_news_is_removed
        await call(k, "watch.add", url="https://shop.example/k", name="Stock", part="#stock")
        names = [w["name"] for w in (await call(k, "watch.list"))["watches"]]
    assert names == ["Stock"]


async def test_one_broken_watch_does_not_lose_the_round(project: Paths, site: Site) -> None:
    async with Kernel(project, http_transport=httpx.MockTransport(site.handler), ui=Yes()) as k:
        delivered: list[str] = []

        async def on_deliver(event: Event) -> None:
            delivered.append(event.data["text"])

        k.bus.subscribe("deliver", on_deliver, owner="t")
        await call(k, "watch.add", url="https://shop.example/k", name="News", part=".news")
        watch = module(k)
        broken = {"id": "bad", "name": "Broken", "url": "https://shop.example/k", "kind": "changes"}
        watch._save([broken, *watch._load()])  # a record without "every_s"
        clock = [watch._now() + 3601]
        watch._now = lambda: clock[0]
        site.news = "Version 2.0 released"
        results = await watch.check_due()
        await k.drain()
        by_name = {r["name"]: r for r in results}
        assert by_name["Broken"]["hit"] is False and "check failed" in by_name["Broken"]["error"]
        assert by_name["News"]["hit"] is True
        assert any("News changed" in d for d in delivered)
        saved = {i["name"]: i for i in watch._load()}
        assert saved["News"]["hits"] == 1 and saved["Broken"]["due"] > clock[0]
        assert [r["name"] for r in await watch.check_due()] == []  # neither is due again yet


def test_prices_with_the_currency_first() -> None:
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent / "modules"))
    try:
        from watch.module import find_price  # type: ignore[import-not-found]
    finally:
        sys.path.pop()
    cases = {
        "€1299": 1299.0,
        "$1299.99": 1299.99,
        "EUR 1299,00": 1299.0,
        "CHF 2500.-": 2500.0,
        "USD 10000": 10000.0,
        "1299 €": 1299.0,
        "Nur 1.234,56 € heute": 1234.56,
        "$1,234.56": 1234.56,
        "Size 42 129,99 €": 129.99,
        "12.500 €": 12500.0,
    }
    assert {text: find_price(text) for text in cases} == cases
