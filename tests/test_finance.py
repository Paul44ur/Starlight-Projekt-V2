"""The finance module: stock (stooq CSV), currency (frankfurter) and crypto (CoinGecko),
all through the shared web client, no API key."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths

STOOQ_CSV = (
    "Symbol,Name,Date,Time,Open,High,Low,Close,Volume\n"
    "AAPL.US,APPLE,2026-10-06,22:00:00,250,255,249,254.5,1000000\n"
)
STOOQ_EMPTY = (
    "Symbol,Name,Date,Time,Open,High,Low,Close,Volume\nZZZ.US,,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n"
)


def transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if host == "stooq.com":
            symbol = request.url.params.get("s", "")
            if symbol.startswith("aapl"):
                return httpx.Response(200, text=STOOQ_CSV)
            return httpx.Response(200, text=STOOQ_EMPTY)
        if host == "api.frankfurter.app":
            return httpx.Response(
                200,
                json={"amount": 100.0, "base": "USD", "date": "2026-10-06", "rates": {"EUR": 92.5}},
            )
        if host == "api.coingecko.com":
            return httpx.Response(200, json={"bitcoin": {"eur": 54321.0, "eur_24h_change": 2.5}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("finance")
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    return await k.dispatcher.call(f"finance.{tool}", args)


async def test_stock(project: Paths) -> None:
    async with Kernel(project, http_transport=transport()) as k:
        result = await call(k, "stock", symbol="AAPL")
    out = result.output
    assert out["symbol"] == "AAPL.US" and out["price"] == "254.5"
    assert out["name"] == "APPLE"


async def test_stock_not_found(project: Paths) -> None:
    async with Kernel(project, http_transport=transport()) as k:
        result = await call(k, "stock", symbol="zzz")
    assert not result.ok and "no price found" in str(result.error)


async def test_fx(project: Paths) -> None:
    async with Kernel(project, http_transport=transport()) as k:
        result = await call(k, "fx", amount=100, from_currency="USD", to_currency="EUR")
    out = result.output
    assert out["result"] == 92.5 and out["from"] == "USD" and out["to"] == "EUR"


async def test_fx_same_currency_is_identity(project: Paths) -> None:
    async with Kernel(project, http_transport=transport()) as k:
        result = await call(k, "fx", amount=50, from_currency="EUR", to_currency="EUR")
    assert result.output["result"] == 50 and result.output["rate"] == 1.0


async def test_crypto_ticker_alias(project: Paths) -> None:
    async with Kernel(project, http_transport=transport()) as k:
        result = await call(k, "crypto", coin="btc", vs="eur")
    out = result.output
    assert out["coin"] == "bitcoin" and out["price"] == 54321.0
    assert out["currency"] == "EUR" and out["change_24h"] == "+2.50%"


async def test_prices_are_live_not_cached(project: Paths) -> None:
    """Asking twice gets the new price: live data must not come from the page cache."""
    prices = iter(["100.0", "101.5"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        csv = "Symbol,Name,Date,Time,Open,High,Low,Close,Volume\n"
        csv += f"AAPL.US,APPLE,2026-10-07,10:00:00,1,1,1,{next(prices)},1\n"
        return httpx.Response(200, text=csv)

    async with Kernel(project, http_transport=httpx.MockTransport(handler)) as k:
        first = await call(k, "stock", symbol="AAPL")
        second = await call(k, "stock", symbol="AAPL")
    assert (first.output["price"], second.output["price"]) == ("100.0", "101.5")
