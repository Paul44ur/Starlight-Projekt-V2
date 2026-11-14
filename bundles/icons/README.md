# The app icons (PNG)

Phones want pictures for the home screen. Starlight ships them ready-made in
`starlight/web/static/icons`. You do not need to run anything here to use Starlight. This
folder only draws them again.

## What is here

- `make_icons.py`: draws the PNG files from the two logos, `static/icon.svg` (Starlight) and
  `static/kill-icon.svg` (the kill switch), and writes `icons.sha256`: the logos they came from.
  It also draws everything else that is made from the Starlight logo: `launcher/starlight.ico`
  and its copy `static/icons/starlight.ico`, the logo files in `assets/logo` (the SVG copy and
  the two PNGs), and the 64 px window icon inside `launcher/launcher_gui.py` (`ICON_PNG`).

The logo is flat: a gold star (`--primary`) on the night colour (`--panel`), with no gradient
and no shine. `assets/logo/starlight-light.svg` is the one for a light page (a light ground,
the star with a thin darker edge); change it by hand, the script only draws its PNG.

Each logo gives four pictures:

| File | Size | For |
| --- | --- | --- |
| `…-180.png` | 180 px, square corners | an iPhone's home screen (it rounds the corners itself and paints transparent ones black) |
| `…-192.png`, `…-512.png` | 192 and 512 px, round corners like the logo | Android and installed apps |
| `…-maskable-512.png` | 512 px, square, the picture in the middle 80 % | Android's shaped icons (a circle, a drop) |

The browser tab keeps the SVG. `starlight.ico` is a copy of `launcher/starlight.ico` (the
icon of Starlight.exe), for Starlight's own chat window; a test keeps the two the same. It
holds the logo at 16, 24, 32, 48, 64, 128 and 256 px.

## Draw them again

After changing `icon.svg` or `kill-icon.svg`:

1. Install the browser once: `uv run --extra browser playwright install chromium` (or set
   `STARLIGHT_CHROMIUM` to a Chrome or Chromium program).
2. Run `uv run --extra browser python bundles/icons/make_icons.py`.
3. Run the tests: `tests/test_brand.py` checks the pictures and `icons.sha256` (it counts
   the logos' line endings as the same on Windows and Linux).
4. Commit everything the script wrote together with the logo: the PNG files, `icons.sha256`,
   the two `.ico` files, `assets/logo` and `launcher/launcher_gui.py`.
