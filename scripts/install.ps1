# Starlight V2 installer for Windows 11. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-Service]
param([switch]$Service)
# Stops on PowerShell errors only. A program that fails (uv, winget) just sets
# $LASTEXITCODE in Windows PowerShell 5.1, so each one is checked below.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    # winget changed the user PATH; read it again so uv is found in this window
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    # Look for uv rather than at winget's exit code: winget also reports a failure when
    # uv was installed already but not on this window's PATH.
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "uv could not be installed; see https://docs.astral.sh/uv/"
        exit 1
    }
}

Write-Host "Installing Starlight and its dependencies..."
uv sync --extra all
if ($LASTEXITCODE) { exit $LASTEXITCODE }
uv run playwright install chromium
if ($LASTEXITCODE) { exit $LASTEXITCODE }

if (-not (Get-Command tesseract -ErrorAction SilentlyContinue) -and
    -not (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe")) {
    Write-Host "Optional: install Tesseract OCR for screen reading:"
    Write-Host "  winget install --id UB-Mannheim.TesseractOCR -e"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Optional: install Docker Desktop so generated code runs in a container."
}

uv run starlight status
if ($LASTEXITCODE) { exit $LASTEXITCODE }
uv run starlight logs scan  # exits 1 when it finds secrets: they are listed, the install goes on

if ($Service) {
    Write-Host "Starting core and viewer now and at every logon (Startup folder)..."
    uv run starlight service install
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
Write-Host ""
Write-Host "Done. Next: docs\setup-windows.md (model, memory, WhatsApp)."
Write-Host "Try it:   uv run starlight run"
Write-Host "Viewer:   uv run starlight viewer   ->  http://127.0.0.1:8765"
