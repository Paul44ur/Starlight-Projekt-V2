# Copies the heading font into starlight\web\static\vendor (see README.md). Needs Node.js 20+.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
npm ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node build.mjs
exit $LASTEXITCODE
