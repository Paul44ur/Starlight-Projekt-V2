#!/usr/bin/env bash
# Starlight V2 installer for Linux. Run from the project folder:
#   bash scripts/install.sh [--service]
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing Starlight and its dependencies..."
uv sync --extra all
uv run playwright install chromium

command -v tesseract >/dev/null 2>&1 || echo "Optional: install tesseract-ocr for screen reading."
command -v docker >/dev/null 2>&1 || echo "Optional: install Docker so generated code runs in a container."

uv run starlight status
uv run starlight logs scan || true  # exits 1 when it finds secrets: list them, finish the install

if [[ "${1:-}" == "--service" ]]; then
    echo "Starting core and viewer with systemd --user..."
    uv run starlight service install
fi
echo
echo "Done. Try it:  uv run starlight run"
echo "Viewer:        uv run starlight viewer   ->  http://127.0.0.1:8765"
