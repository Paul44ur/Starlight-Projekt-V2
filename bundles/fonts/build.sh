#!/bin/sh
# Copies the heading font into starlight/web/static/vendor (see README.md). Needs Node.js 20+.
set -e
cd "$(dirname "$0")"
npm ci --no-audit --no-fund
node build.mjs
