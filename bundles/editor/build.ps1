# Rebuilds starlight\web\static\vendor\editor.js (see README.md). Needs Node.js 20 or newer.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
npm ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node build.mjs
exit $LASTEXITCODE
