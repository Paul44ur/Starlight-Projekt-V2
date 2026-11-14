"""Draws Starlight's app icons as PNG pictures from its two SVG logos.

Phones want pictures for the home screen: an iPhone takes a 180 px square (it rounds the
corners itself, and paints transparent corners black), Android takes 192 and 512 px, and
a "maskable" one it may cut into a circle or another shape. The browser tab keeps the SVG.

It also draws what else is made from the Starlight logo: the icon of Starlight.exe and of
Starlight's windows (launcher/starlight.ico, and its copy in static/icons), the logo as PNG
(assets/logo), and the small window icon kept inside launcher/launcher_gui.py (ICON_PNG).

Run it again after changing static/icon.svg or static/kill-icon.svg (a test reminds you):

    uv run --extra browser python bundles/icons/make_icons.py

It needs Playwright's Chromium (`uv run --extra browser playwright install chromium`), or
set STARLIGHT_CHROMIUM to a Chrome or Chromium program.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "starlight" / "web" / "static"
OUT = STATIC / "icons"
LOGOS = ROOT / "assets" / "logo"  # the logo as files: starlight.svg is static/icon.svg
LAUNCHER = ROOT / "launcher"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
BACKGROUND = '<rect width="512" height="512" rx="112" '  # each logo starts with this
# A maskable icon keeps its picture inside the middle 80 % (the shape may cut the rest).
SAFE = '<g transform="translate(256 256) scale(.8) translate(-256 -256)">'


def digest(logo: bytes) -> str:
    """The logo's SHA-256, whatever line endings the checkout gave it (Windows: CRLF)."""
    return hashlib.sha256(logo.replace(b"\r\n", b"\n")).hexdigest()


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


def ico(pictures: dict[int, bytes]) -> bytes:
    """An .ico file from PNG pictures by size. Up to 128 px a picture is stored as a bitmap
    (what every Windows program reads); 256 px is stored as the PNG it is."""
    from PIL import Image

    entries = []
    for size, png in sorted(pictures.items()):
        if size >= 256:
            entries.append((size, png))
            continue
        image = Image.open(io.BytesIO(png)).convert("RGBA")
        rows = [image.crop((0, y, size, y + 1)) for y in reversed(range(size))]  # bottom-up
        colours = b"".join(row.tobytes("raw", "BGRA") for row in rows)
        mask = b""
        for row in rows:  # one bit per pixel, set where the pixel is see-through
            bits = bytearray((size + 31) // 32 * 4)
            for x, alpha in enumerate(row.getchannel("A").tobytes()):
                if alpha == 0:
                    bits[x // 8] |= 0x80 >> (x % 8)
            mask += bytes(bits)
        header = struct.pack(
            "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(colours) + len(mask), 0, 0, 0, 0
        )
        entries.append((size, header + colours + mask))
    out = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    for size, data in entries:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(data for _, data in entries)


def window_icon(png: bytes) -> None:
    """Puts the 64 px logo into launcher/launcher_gui.py (ICON_PNG: the window's icon)."""
    text = base64.b64encode(png).decode("ascii")
    lines = "".join(f'    "{text[i : i + 100]}"\n' for i in range(0, len(text), 100))
    source = LAUNCHER / "launcher_gui.py"
    code = source.read_text("utf-8")
    block = re.compile(r'ICON_PNG = \(\n(?:    "[A-Za-z0-9+/=]+"\n)+\)')
    if len(block.findall(code)) != 1:
        raise SystemExit("launcher_gui.py no longer has one ICON_PNG block")
    source.write_text(block.sub(f"ICON_PNG = (\n{lines})", code), encoding="utf-8")


def main() -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(exist_ok=True)
    sums = []
    wanted: dict[str, tuple[str, int]] = {}
    for logo in ("icon", "kill-icon"):
        source = (STATIC / f"{logo}.svg").read_bytes()
        sums.append(f"{digest(source)}  {logo}.svg")
        wanted |= variants(logo, source.decode("utf-8"))
    chromium = os.environ.get("STARLIGHT_CHROMIUM") or None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium)

        def draw(svg: str, size: int) -> bytes:
            page = browser.new_page(viewport={"width": size, "height": size})
            sized = svg.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
            page.set_content(f'<body style="margin:0">{sized}</body>')
            png: bytes = page.screenshot(omit_background=True)
            page.close()
            return png

        for file, (svg, size) in wanted.items():
            (OUT / file).write_bytes(draw(svg, size))
            print(f"{file}: {size} x {size}, {(OUT / file).stat().st_size // 1024} KB")
        # What else is made from the Starlight logo.
        logo = (STATIC / "icon.svg").read_text("utf-8")
        (LOGOS / "starlight.svg").write_text(logo, encoding="utf-8")
        (LOGOS / "starlight.png").write_bytes(draw(logo, 512))
        light = (LOGOS / "starlight-light.svg").read_text("utf-8")  # (for a light page)
        (LOGOS / "starlight-light.png").write_bytes(draw(light, 512))
        icon = ico({size: draw(logo, size) for size in ICO_SIZES})
        (LAUNCHER / "starlight.ico").write_bytes(icon)
        (OUT / "starlight.ico").write_bytes(icon)  # (the same file: a test compares them)
        window_icon(draw(logo, 64))
        print(f"starlight.ico: {len(icon) // 1024} KB; assets/logo and ICON_PNG drawn too")
        browser.close()
    # The logos these were drawn from (tests/test_brand.py compares them).
    (OUT / "icons.sha256").write_text("\n".join(sums) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
