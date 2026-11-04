"""Draws Starlight's app icons as PNG pictures from its two SVG logos.

Phones want pictures for the home screen: an iPhone takes a 180 px square (it rounds the
corners itself, and paints transparent corners black), Android takes 192 and 512 px, and
a "maskable" one it may cut into a circle or another shape. The browser tab keeps the SVG.

Run it again after changing static/icon.svg or static/kill-icon.svg (a test reminds you):

    uv run --extra browser python bundles/icons/make_icons.py

It needs Playwright's Chromium (`uv run --extra browser playwright install chromium`), or
set STARLIGHT_CHROMIUM to a Chrome or Chromium program.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "starlight" / "web" / "static"
OUT = STATIC / "icons"
BACKGROUND = '<rect width="512" height="512" rx="112" '  # each logo starts with this
# A maskable icon keeps its picture inside the middle 80 % (the shape may cut the rest).
SAFE = '<g transform="translate(256 256) scale(.8) translate(-256 -256)">'


def square(svg: str) -> str:
    """The logo with square corners: the phone rounds them itself."""
    if svg.count(BACKGROUND) != 1:
        raise SystemExit("the logo no longer starts with its 512 px background square")
    return svg.replace(BACKGROUND, BACKGROUND.replace(' rx="112"', ""))


def maskable(svg: str) -> str:
    """Square corners, and the picture shrunk into the safe middle."""
    svg = square(svg)
    background_end = svg.index("/>", svg.index('<rect width="512" height="512"')) + 2
    return svg[:background_end] + SAFE + svg[background_end:].replace("</svg>", "</g></svg>")


def variants(name: str, svg: str) -> dict[str, tuple[str, int]]:
    return {
        f"{name}-180.png": (square(svg), 180),
        f"{name}-192.png": (svg, 192),
        f"{name}-512.png": (svg, 512),
        f"{name}-maskable-512.png": (maskable(svg), 512),
    }


def main() -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(exist_ok=True)
    sums = []
    wanted: dict[str, tuple[str, int]] = {}
    for logo in ("icon", "kill-icon"):
        source = (STATIC / f"{logo}.svg").read_bytes()
        sums.append(f"{hashlib.sha256(source).hexdigest()}  {logo}.svg")
        wanted |= variants(logo, source.decode("utf-8"))
    chromium = os.environ.get("STARLIGHT_CHROMIUM") or None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium)
        for file, (svg, size) in wanted.items():
            page = browser.new_page(viewport={"width": size, "height": size})
            sized = svg.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
            page.set_content(f'<body style="margin:0">{sized}</body>')
            shot = page.screenshot(omit_background=True)
            page.close()
            picture = Image.open(io.BytesIO(shot)).convert("RGBA")
            picture.save(OUT / file, optimize=True)
            print(f"{file}: {size} x {size}, {(OUT / file).stat().st_size // 1024} KB")
        browser.close()
    # The logos these were drawn from (tests/test_brand.py compares them).
    (OUT / "icons.sha256").write_text("\n".join(sums) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
