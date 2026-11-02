# The Notes page's graphs (d3-force)

Starlight ships the graph layout as one ready-made file, `starlight/web/static/vendor/graph.js`.
You do not need Node.js to run Starlight. This folder only rebuilds that file.

## What is here

- `package.json` and `package-lock.json`: the exact versions used.
- `entry.js`: what the page can import from the bundle.
- `build.mjs`: makes `graph.js`, `graph.sha256` and `graph-LICENSES.txt` in `static/vendor`.
- `build.sh` (Linux, macOS) and `build.ps1` (Windows): install and build in one step.

## Rebuild

1. Install Node.js 20 or newer.
2. Run `./build.sh` (or `.\build.ps1` in PowerShell).
3. Run the tests: `tests/test_notes_graph.py` checks the file against `graph.sha256`.
4. Commit `graph.js`, `graph.sha256` and `graph-LICENSES.txt` together.

The same versions give the same file, byte for byte.

## Updating a version

Change the version in `package.json`, then run `npm install` here and rebuild. Choose
versions that are at least two weeks old, and install with `npm install --before=<date two
weeks ago>` so the packages they pull in are too. Read the changes of each package first.

## Licences

All bundled packages (d3-force and the three small d3 packages it uses) use the ISC licence.
`graph-LICENSES.txt` next to `graph.js` lists each one with its full licence text. The build
writes it from the packages that end up in the file.
