"""Stock, currency and crypto prices from free sources - no account, no API key.

* stocks: stooq (worldwide; US tickers like "aapl" work, or add a suffix like "aapl.us")
* currency: frankfurter.app (European Central Bank reference rates)
* crypto: CoinGecko

Everything goes through Starlight's shared, rate-limited web client. Prices are indicative,
not financial advice.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.http import HttpError

STOOQ = "https://stooq.com/q/l/"
# stooq's quote CSV. f = the fields: s symbol, n name, d2 date, t2 time, o/h/l/c/v open, high,
# low, close, volume. h = a header row: its names (Symbol, Name, Date, Time, Open, High, Low,
# Close, Volume) are the keys read in `stock`, so dropping "h" or a field breaks them. e = format.
STOOQ_QUERY = "f=snd2t2ohlcv&h&e=csv"
STOOQ_NO_DATA = "N/D"  # what stooq puts in every column for a symbol it does not know
FRANKFURTER = "https://api.frankfurter.app/latest"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"

# Common crypto tickers -> CoinGecko ids, so "btc" works as well as "bitcoin".
_COINS = {
    "btc": "bitcoin", "eth": "ethereum", "sol": "solana", "ada": "cardano",
    "xrp": "ripple", "doge": "dogecoin", "ltc": "litecoin", "dot": "polkadot",
    "bnb": "binancecoin", "usdt": "tether", "usdc": "usd-coin", "matic": "matic-network",
}  # fmt: skip


class Config(BaseModel):
    default_currency: str = "eur"  # used for crypto and as the FX target default


class FinanceModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    async def _get(self, url: str, what: str) -> Any:
        try:
            response = await self.ctx.http.get(
                url, respect_robots=False, use_cache=False
            )  # live prices
        except HttpError as exc:
            raise ToolError(f"could not reach the {what} service: {exc}") from exc
        if not response.ok:
            raise ToolError(f"the {what} service returned HTTP {response.status}")
        return response

    @tool
    async def stock(self, symbol: str) -> dict[str, Any]:
        """The latest price of a stock, index or ETF. `symbol` can be a ticker like "AAPL",
        "MSFT", "^SPX", or with a market suffix like "VOW.DE". Prices are delayed."""
        sym = symbol.strip().lower()
        if not sym:
            raise ToolError("give a ticker symbol, e.g. 'AAPL'")
        # A bare ticker ("aapl") is tried as a US listing first; one with a market suffix
        # ("vow.de") or an index ("^spx") as it is.
        tried = [sym] if ("." in sym or sym.startswith("^")) else [f"{sym}.us", sym]
        for candidate in tried:
            url = f"{STOOQ}?s={candidate}&{STOOQ_QUERY}"
            response = await self._get(url, "stock")
            row = next(csv.DictReader(io.StringIO(response.text)), None)
            close = (row or {}).get("Close", "")
            if row and close not in ("", STOOQ_NO_DATA):
                return {
                    "symbol": (row.get("Symbol") or candidate).upper(),
                    "name": row.get("Name") or None,
                    "price": close,
                    "open": row.get("Open"),
                    "high": row.get("High"),
                    "low": row.get("Low"),
                    "volume": row.get("Volume"),
                    "date": row.get("Date"),
                }
        raise ToolError(f"no price found for '{symbol}' (check the ticker)")

    @tool
    async def fx(self, amount: float, from_currency: str, to_currency: str = "") -> dict[str, Any]:
        """Convert money between currencies at today's reference rate, e.g.
        fx(100, "USD", "EUR"). Leaves `to_currency` empty to use your default currency."""
        base = from_currency.strip().upper()
        target = (to_currency or self.config.default_currency).strip().upper()
        if not base or not target:
            raise ToolError("give the currencies, e.g. from 'USD' to 'EUR'")
        if base == target:
            return {"amount": amount, "from": base, "to": target, "result": amount, "rate": 1.0}
        url = f"{FRANKFURTER}?amount={amount}&from={base}&to={target}"
        response = await self._get(url, "currency")
        data = response.json() if isinstance(response.json(), dict) else {}
        rates = data.get("rates") or {}
        if target not in rates:
            raise ToolError(f"could not convert {base} to {target} (check the currency codes)")
        result = rates[target]
        return {
            "amount": amount,
            "from": base,
            "to": target,
            "result": round(result, 4),
            "rate": round(result / amount, 6) if amount else None,
            "date": data.get("date"),
        }

    @tool
    async def crypto(self, coin: str, vs: str = "") -> dict[str, Any]:
        """The price of a cryptocurrency, e.g. crypto("bitcoin") or crypto("btc"). `vs` is
        the currency to price it in (default from config)."""
        key = coin.strip().lower()
        coin_id = _COINS.get(key, key)
        currency = (vs or self.config.default_currency).strip().lower()
        if not coin_id:
            raise ToolError("give a coin, e.g. 'bitcoin' or 'btc'")
        url = f"{COINGECKO}?ids={coin_id}&vs_currencies={currency}&include_24hr_change=true"
        response = await self._get(url, "crypto")
        data = response.json() if isinstance(response.json(), dict) else {}
        entry = data.get(coin_id)
        if not entry or currency not in entry:
            raise ToolError(f"no price found for '{coin}' in {currency.upper()} (check the name)")
        change = entry.get(f"{currency}_24h_change")
        return {
            "coin": coin_id,
            "currency": currency.upper(),
            "price": entry[currency],
            "change_24h": f"{change:+.2f}%" if isinstance(change, (int, float)) else None,
        }
