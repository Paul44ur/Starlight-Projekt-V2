#!/bin/sh
# Rebuilds starlight/web/static/vendor/mermaid.js (see README.md). Needs Node.js 20 or newer.
set -e
cd "$(dirname "$0")"
npm ci --no-audit --no-fund
node build.mjs
