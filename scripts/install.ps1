# Starlight V2 installer for Windows 11. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 [-Service]
param([switch]$Service)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
}

Write-Host "Installing Starlight and its dependencies..."
uv sync --extra all
uv run playwright install chromium

if (-not (Get-Command tesseract -ErrorAction SilentlyContinue) -and
    -not (Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe")) {
    Write-Host "Optional: install Tesseract OCR for screen reading:"
    Write-Host "  winget install --id UB-Mannheim.TesseractOCR -e"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Optional: install Docker Desktop so generated code runs in a container."
}

uv run starlight status
uv run starlight logs scan

if ($Service) {
    Write-Host "Starting core and viewer now and at every logon (Startup folder)..."
    uv run starlight service install
}
Write-Host ""
Write-Host "Done. Next: docs\setup-windows.md (model, memory, WhatsApp)."
Write-Host "Try it:   uv run starlight run"
Write-Host "Viewer:   uv run starlight viewer   ->  http://127.0.0.1:8765"
