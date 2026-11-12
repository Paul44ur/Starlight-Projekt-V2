# The heading font (Space Grotesk)

Starlight's headings use Space Grotesk. The font is served by Starlight itself, from
`starlight/web/static/vendor/`, so nothing is loaded from the internet. You do not need
Node.js to run Starlight. This folder only rebuilds the font files.

## What is here

- `package.json` and `package-lock.json`: the exact version used
  (`@fontsource-variable/space-grotesk`).
- `build.mjs`: copies the latin and latin-ext variable woff2 files and the licence into
  `static/vendor`, and writes `fonts.sha256`.
- `build.sh` (Linux, macOS) and `build.ps1` (Windows): install and build in one step.

The files it makes: `space-grotesk-latin.woff2`, `space-grotesk-latin-ext.woff2`,
`space-grotesk-LICENSE.txt` and `fonts.sha256`. The `@font-face` rules (with the
`unicode-range` of each file) are in `static/style.css`.

## Rebuild

1. Install Node.js 20 or newer.
2. Run `./build.sh` (or `.\build.ps1` in PowerShell).
3. Run the tests: `tests/test_night_sky_tokens.py` checks the files against `fonts.sha256`.
4. Commit the files in `static/vendor` together.

## Updating the version

Change the version in `package.json`, then run `npm install` here and rebuild. Choose a
version that is at least two weeks old. If the package's CSS has other `unicode-range`
values, copy them into `style.css`.

## Licence

Space Grotesk is licensed under the SIL Open Font License 1.1. `space-grotesk-LICENSE.txt`
next to the font files has the text.
