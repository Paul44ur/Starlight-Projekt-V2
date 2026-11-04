# The app icons (PNG)

Phones want pictures for the home screen. Starlight ships them ready-made in
`starlight/web/static/icons`. You do not need to run anything here to use Starlight. This
folder only draws them again.

## What is here

- `make_icons.py`: draws the PNG files from the two logos, `static/icon.svg` (Starlight) and
  `static/kill-icon.svg` (the kill switch), and writes `icons.sha256`: the logos they came from.

Each logo gives four pictures:

| File | Size | For |
| --- | --- | --- |
| `…-180.png` | 180 px, square corners | an iPhone's home screen (it rounds the corners itself and paints transparent ones black) |
| `…-192.png`, `…-512.png` | 192 and 512 px, round corners like the logo | Android and installed apps |
| `…-maskable-512.png` | 512 px, square, the picture in the middle 80 % | Android's shaped icons (a circle, a drop) |

The browser tab keeps the SVG.

## Draw them again

After changing `icon.svg` or `kill-icon.svg`:

1. Install the browser once: `uv run --extra browser playwright install chromium` (or set
   `STARLIGHT_CHROMIUM` to a Chrome or Chromium program).
2. Run `uv run --extra browser python bundles/icons/make_icons.py`.
3. Run the tests: `tests/test_brand.py` checks the pictures and `icons.sha256` (it counts
   the logos' line endings as the same on Windows and Linux).
4. Commit the PNG files and `icons.sha256` together with the logo.
