# Windows local test script
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1 -LaunchGui
#   powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1 -SkipNetwork

param(
    [switch]$LaunchGui,
    [switch]$SkipNetwork
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command not found: $Name. Install it and add to PATH."
    }
}

Write-Step "Check Python"
Assert-Command python
$pyVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "Python $pyVersion"
if ([double]$pyVersion -lt 3.11) {
    throw "Python 3.11+ is required."
}

Write-Step "Install dependencies"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pytest

Write-Step "Check ffmpeg / ffprobe"
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    Write-Host "Warning: ffmpeg not found. Add to PATH or place under resources\ffmpeg\" -ForegroundColor Yellow
} else {
    ffmpeg -version | Select-Object -First 1
}
if (-not $ffprobe) {
    Write-Host "Warning: ffprobe not found. Post-download validation may be limited." -ForegroundColor Yellow
}

Write-Step "Run unit tests"
python -m pytest tests/ -v
if ($LASTEXITCODE -ne 0) {
    throw "Unit tests failed."
}

if (-not $SkipNetwork) {
    Write-Step "Run smoke test (get_info)"
    python smoke_test.py
    if ($LASTEXITCODE -ne 0) {
        throw "Smoke test failed."
    }
} else {
    Write-Host "Skipped network smoke test (-SkipNetwork)."
}

Write-Step "GUI init check"
python -c "import sys; from PySide6.QtWidgets import QApplication; from gui import MainWindow; app = QApplication(sys.argv); w = MainWindow(); print('GUI init OK:', w.windowTitle())"
if ($LASTEXITCODE -ne 0) {
    throw "GUI init failed."
}

Write-Host ""
Write-Host "All tests passed." -ForegroundColor Green

if ($LaunchGui) {
    Write-Step "Launch GUI"
    python main.py
}
