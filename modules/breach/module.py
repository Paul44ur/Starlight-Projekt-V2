"""Check whether a password has appeared in known data breaches, and rate its strength.

It uses the Have I Been Pwned "range" API, which is privacy-preserving: only the first five
characters of the password's SHA-1 hash are sent, never the password itself, and the match is
worked out on this computer. The password is never logged. No account or API key is needed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.http import HttpError

RANGE_API = "https://api.pwnedpasswords.com/range/"


class Config(BaseModel):
    pass


class BreachModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    @tool
    async def password_pwned(self, password: str) -> dict[str, Any]:
        """Check whether a password has appeared in known data breaches. Only a short hash
        prefix is sent, never the password; the password is not logged. Returns how many
        times it has been seen (0 means not found in the breach set)."""
        if not password:
            raise ToolError("give a password to check")
        # SHA-1 is required by the HIBP range API; it is not used as a security primitive here.
        digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        prefix, suffix = digest[:5], digest[5:]
        try:
            response = await self.ctx.http.get(f"{RANGE_API}{prefix}", respect_robots=False)
        except HttpError as exc:
            raise ToolError(f"could not reach the breach service: {exc}") from exc
        if not response.ok:
            raise ToolError(f"the breach service returned HTTP {response.status}")
        count = 0
        for line in response.text.splitlines():
            found_suffix, _, times = line.partition(":")
            if found_suffix.strip().upper() == suffix:
                count = int(times.strip() or 0)
                break
        self.ctx.logger.info("password checked", pwned=count > 0)  # never log the password
        if count:
            advice = "This password is unsafe - it is in public breach lists. Change it."
        else:
            advice = "Not found in the breach set (this does not prove it is strong)."
        return {"pwned": count > 0, "times_seen": count, "advice": advice}

    @tool
    def how_safe(self, password: str) -> dict[str, Any]:
        """Rate a password's strength locally (nothing is sent anywhere): length and the mix
        of lower/upper case, digits and symbols, minus points for common patterns."""
        if not password:
            raise ToolError("give a password to rate")
        length = len(password)
        classes = sum(
            bool(re.search(pattern, password))
            for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
        )
        score = min(length, 20) + classes * 5
        weaknesses: list[str] = []
        if length < 12:
            weaknesses.append("shorter than 12 characters")
        if classes < 3:
            weaknesses.append("few character types (mix letters, digits and symbols)")
        if re.search(r"(.)\1\1", password):
            weaknesses.append("a character repeats three or more times")
        if password.lower() in _COMMON or re.fullmatch(r"\d+", password):
            weaknesses.append("looks like a very common password")
            score = min(score, 10)
        rating = "strong" if score >= 35 else "ok" if score >= 25 else "weak"
        return {"rating": rating, "score": min(score, 40), "out_of": 40, "weaknesses": weaknesses}


_COMMON = {
    "password", "passwort", "123456", "123456789", "qwerty", "qwertz", "111111",
    "letmein", "admin", "welcome", "iloveyou", "monkey", "dragon", "abc123",
}  # fmt: skip
