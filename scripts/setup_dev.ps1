# Download dev binaries into resources/ (mirrors CI build.yml)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1

param(
    [switch]$SkipDeno
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

$FfmpegDir = Join-Path $Root "resources\ffmpeg"
$DenoDir = Join-Path $Root "resources\deno"
New-Item -ItemType Directory -Force -Path $FfmpegDir | Out-Null

Write-Step "Download ffmpeg + ffprobe"
$FfmpegZip = Join-Path $env:TEMP "ffmpeg-dev.zip"
Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $FfmpegZip
Expand-Archive -Path $FfmpegZip -DestinationPath $env:TEMP -Force
$Extracted = Get-ChildItem -Path $env:TEMP -Directory -Filter "ffmpeg-*" | Select-Object -First 1
Copy-Item (Join-Path $Extracted.FullName "bin\ffmpeg.exe") (Join-Path $FfmpegDir "ffmpeg.exe") -Force
Copy-Item (Join-Path $Extracted.FullName "bin\ffprobe.exe") (Join-Path $FfmpegDir "ffprobe.exe") -Force
Remove-Item $FfmpegZip -Force -ErrorAction SilentlyContinue

Write-Step "Verify ffmpeg"
python -c "from downloader import _resolve_tool; p=_resolve_tool('ffmpeg'); assert p, 'ffmpeg not found'; print('ffmpeg:', p)"

if (-not $SkipDeno) {
    Write-Step "Download deno (optional, Cookie mode)"
    New-Item -ItemType Directory -Force -Path $DenoDir | Out-Null
    $DenoZip = Join-Path $env:TEMP "deno-dev.zip"
    Invoke-WebRequest -Uri "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" -OutFile $DenoZip
    Expand-Archive -Path $DenoZip -DestinationPath $DenoDir -Force
    Remove-Item $DenoZip -Force -ErrorAction SilentlyContinue
    python -c "from downloader import _resolve_tool; p=_resolve_tool('deno'); print('deno:', p or 'skipped')"
}

Write-Host ""
Write-Host "Dev binaries ready under resources/. Run: python main.py" -ForegroundColor Green
