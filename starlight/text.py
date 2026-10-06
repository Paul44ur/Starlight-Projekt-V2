"""Turn HTML into readable text for the model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

NOISE = [
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "iframe",
    "template",
]


@dataclass
class PageText:
    title: str
    text: str
    links: list[str] = field(default_factory=list)


def html_to_text(html: str, base_url: str = "", max_chars: int | None = None) -> PageText:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    links = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href"))
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        links.append(urljoin(base_url, href))
    for tag in soup(NOISE):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = root.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + " …"
    return PageText(title, text, list(dict.fromkeys(links)))
