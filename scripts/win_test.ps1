# Windows 本地测试脚本
# 用法:
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
        throw "未找到 $Name，请先安装并加入 PATH。"
    }
}

Write-Step "检查 Python"
Assert-Command python
$pyVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "Python $pyVersion"
if ([double]$pyVersion -lt 3.11) {
    throw "需要 Python 3.11 或更高版本。"
}

Write-Step "安装依赖"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pytest

Write-Step "检查 ffmpeg / ffprobe"
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    Write-Host "警告: 未找到 ffmpeg。请安装后加入 PATH，或放到 resources\ffmpeg\ 目录。" -ForegroundColor Yellow
} else {
    ffmpeg -version | Select-Object -First 1
}
if (-not $ffprobe) {
    Write-Host "警告: 未找到 ffprobe，下载后校验功能会受限。" -ForegroundColor Yellow
}

Write-Step "运行单元测试"
python -m pytest tests/ -v
if ($LASTEXITCODE -ne 0) {
    throw "单元测试失败。"
}

if (-not $SkipNetwork) {
    Write-Step "运行冒烟测试 (get_info)"
    python smoke_test.py
    if ($LASTEXITCODE -ne 0) {
        throw "冒烟测试失败。"
    }
} else {
    Write-Host "已跳过网络冒烟测试 (-SkipNetwork)。"
}

Write-Step "GUI 初始化检查"
python -c "import sys; from PySide6.QtWidgets import QApplication; from gui import MainWindow; app = QApplication(sys.argv); w = MainWindow(); print('GUI init OK:', w.windowTitle())"
if ($LASTEXITCODE -ne 0) {
    throw "GUI 初始化失败。"
}

Write-Host ""
Write-Host "全部测试通过。" -ForegroundColor Green

if ($LaunchGui) {
    Write-Step "启动 GUI"
    python main.py
}
