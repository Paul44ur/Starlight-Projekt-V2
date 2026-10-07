"""Look up known vulnerabilities (CVEs) from the public NVD database - no account, no key.

Use it to check a CVE id, or to search by product and version (pairs well with the recon
module: a scan finds a service, this tells you what's known about it). Requests go through
Starlight's shared, rate-limited web client (NVD without a key allows a few requests at a
time, which the client respects).
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.http import HttpError

NVD = "https://services.nvd.nist.gov/rest/json/cves/2.0"


class Config(BaseModel):
    max_results: int = Field(default=10, ge=1, le=50)


class CveModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    async def _get(self, query: str) -> dict[str, Any]:
        try:
            response = await self.ctx.http.get(f"{NVD}?{query}", respect_robots=False)
        except HttpError as exc:
            raise ToolError(f"could not reach the NVD service: {exc}") from exc
        if response.status == 404:
            return {"vulnerabilities": []}
        if not response.ok:
            raise ToolError(f"the NVD service returned HTTP {response.status}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    @tool
    async def lookup(self, cve_id: str) -> dict[str, Any]:
        """Look up one vulnerability by its CVE id, e.g. "CVE-2021-44228" (Log4Shell).
        Returns the description, severity, score and references."""
        cve_id = cve_id.strip().upper()
        if not cve_id.startswith("CVE-"):
            raise ToolError("give a CVE id like 'CVE-2021-44228'")
        data = await self._get(f"cveId={urllib.parse.quote(cve_id)}")
        items = data.get("vulnerabilities") or []
        if not items:
            raise ToolError(f"no record found for {cve_id}")
        return summarise(items[0].get("cve") or {}, full=True)

    @tool
    async def search(self, product: str, version: str = "", limit: int = 0) -> dict[str, Any]:
        """Search vulnerabilities by product (and optional version), e.g.
        search("openssl", "3.0.0"). Returns the most relevant CVEs, newest first."""
        terms = f"{product} {version}".strip()
        if not terms:
            raise ToolError("give a product to search for, e.g. 'openssl'")
        count = min(limit or self.config.max_results, 50)
        query = urllib.parse.urlencode({"keywordSearch": terms, "resultsPerPage": count})
        data = await self._get(query)
        items = data.get("vulnerabilities") or []
        results = [summarise(item.get("cve") or {}) for item in items[:count]]
        results.sort(key=lambda r: r.get("published") or "", reverse=True)
        return {"query": terms, "total": data.get("totalResults", len(results)), "results": results}


def _description(cve: dict[str, Any]) -> str:
    descriptions = cve.get("descriptions") or []
    for entry in descriptions:
        if entry.get("lang") == "en":
            return str(entry.get("value", ""))
    return str(descriptions[0].get("value", "")) if descriptions else ""


def _severity(cve: dict[str, Any]) -> dict[str, Any]:
    metrics = cve.get("metrics") or {}
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key) or []
        if entries:
            data = entries[0].get("cvssData") or {}
            severity = data.get("baseSeverity") or entries[0].get("baseSeverity")
            return {"score": data.get("baseScore"), "severity": severity, "metric": key}
    return {"score": None, "severity": None, "metric": None}


def summarise(cve: dict[str, Any], full: bool = False) -> dict[str, Any]:
    severity = _severity(cve)
    description = _description(cve)
    result: dict[str, Any] = {
        "id": cve.get("id"),
        "severity": severity["severity"],
        "score": severity["score"],
        "published": cve.get("published"),
        "summary": description
        if full
        else (description[:300] + ("..." if len(description) > 300 else "")),
    }
    if full:
        result["last_modified"] = cve.get("lastModified")
        result["references"] = [ref.get("url") for ref in (cve.get("references") or [])][:10]
    return result
