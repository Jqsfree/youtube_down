# Windows local test script (ASCII-only for GBK PowerShell compatibility)
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
        throw "Command not found: $Name"
    }
}

Write-Step "Check Python"
Assert-Command python
$pyVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "Python $pyVersion"
if ([double]$pyVersion -lt 3.11) {
    throw "Python 3.11+ required."
}

Write-Step "Install dependencies"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pytest

Write-Step "Check ffmpeg / ffprobe"
python -c "from downloader import _resolve_tool; f=_resolve_tool('ffmpeg'); p=_resolve_tool('ffprobe'); print('ffmpeg:', f or 'MISSING'); print('ffprobe:', p or 'MISSING'); import sys; sys.exit(0 if f else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARN: ffmpeg not found. Run: powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1" -ForegroundColor Yellow
} else {
    python -c "from downloader import _resolve_tool; import subprocess; subprocess.run([_resolve_tool('ffmpeg'), '-version'], check=False)" 2>&1 | Select-Object -First 1
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
python -c "import sys; from PySide6.QtWidgets import QApplication; from gui import MainWindow; app = QApplication(sys.argv); w = MainWindow(); print('GUI init OK'); app.quit()"
if ($LASTEXITCODE -ne 0) {
    throw "GUI init failed."
}

Write-Host ""
Write-Host "All tests passed." -ForegroundColor Green

if ($LaunchGui) {
    Write-Step "Launch GUI"
    python main.py
}
