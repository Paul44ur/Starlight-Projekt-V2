# The Notes page's diagrams (Mermaid)

Starlight ships Mermaid as one ready-made file, `starlight/web/static/vendor/mermaid.js`
(about 3.7 MB). The Notes page loads it only when a note has a ```` ```mermaid ```` block.
You do not need Node.js to run Starlight. This folder only rebuilds that file.

## What is here

- `package.json` and `package-lock.json`: the exact versions used.
- `entry.js`: what the page imports from the bundle (Mermaid itself).
- `elk-stub.js`: stands in for elkjs (see below).
- `build.mjs`: makes `mermaid.js`, `mermaid.sha256` and `mermaid-LICENSES.txt` in
  `static/vendor`.
- `build.sh` (Linux, macOS) and `build.ps1` (Windows): install and build in one step.

## Rebuild

1. Install Node.js 20 or newer.
2. Run `./build.sh` (or `.\build.ps1` in PowerShell).
3. Run the tests: `tests/test_mermaid.py` checks the file against `mermaid.sha256`.
4. Commit `mermaid.js`, `mermaid.sha256` and `mermaid-LICENSES.txt` together.

The same versions give the same file, byte for byte.

## Updating a version

Change the version in `package.json`, then run `npm install --before=<date two weeks ago>`
here and rebuild, so Mermaid and the packages it pulls in are at least two weeks old. Read
Mermaid's changes first, and check the page still sets the dagre layout (below).

## The ELK layout is left out

Mermaid 12 brings elkjs, a layout engine of about 1.5 MB under the EPL-2.0 licence. The
build puts `elk-stub.js` in its place, so the file stays smaller and every licence in it is
a permissive one. The Notes page sets the dagre layout (Mermaid 11's default) for all
diagrams. Only a diagram that asks for `layout: elk` itself gets the message that this
layout is not included.

## Licences

Mermaid is MIT. The packages it uses are MIT, ISC, Apache-2.0, BSD-3-Clause, Unlicense, or
DOMPurify's MPL-2.0 or Apache-2.0 (your choice). `mermaid-LICENSES.txt` next to
`mermaid.js` lists each one with its full licence text. The build writes it from the
packages that end up in the file, and a test checks every licence against that list.
