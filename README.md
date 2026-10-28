# Multi-Platform Downloader

基于 yt-dlp + PySide6 的桌面下载工具，支持 YouTube / Bilibili / TikTok。

## 下载安装包（推荐）

最新版：[v1.2.0](https://github.com/Jqsfree/youtube_down/releases/tag/v1.2.0)

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows (x64) | [YouTubeDownloader-Windows.exe](https://github.com/Jqsfree/youtube_down/releases/download/v1.2.0/YouTubeDownloader-Windows.exe) | 双击运行；已内嵌 ffmpeg / ffprobe / deno |
| macOS (Apple Silicon) | [YouTubeDownloader-macOS.zip](https://github.com/Jqsfree/youtube_down/releases/download/v1.2.0/YouTubeDownloader-macOS.zip) | 解压后打开 `.app`；已内嵌 ffmpeg / ffprobe / deno |

### macOS 首次打开

若提示「无法验证开发者」：右键 App → 打开，或在「系统设置 → 隐私与安全性」中允许。

会员 / 风控视频请在 GUI 按平台导入 Cookie。

## 源码运行

### 系统要求

- Python 3.12+
- ffmpeg / ffprobe（放入 PATH 或 `resources/ffmpeg/`）

### 安装

```bash
pip install -r requirements.txt
```

### Windows 开发环境（推荐）

无需全局安装 ffmpeg，一键下载到 `resources/ffmpeg/`：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1
python main.py
```

也可手动 `winget install Gyan.FFmpeg`，或将 ffmpeg 放入 PATH / `resources\ffmpeg\`。

### 启动

```bash
python main.py                 # GUI
python -m download_cli -h      # 批量 CLI
```

## Windows 本地测试

在项目根目录打开 **PowerShell** 或 **命令提示符**：

```powershell
# 一键测试：安装依赖 + 单元测试 + 冒烟测试 + GUI 初始化
powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1

# 测试通过后直接启动 GUI
powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1 -LaunchGui

# 离线环境（跳过需要网络的 get_info 测试）
powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1 -SkipNetwork
```

也可以双击 `scripts\win_test.bat`。

> 若 PowerShell 报中文乱码或解析错误，请使用最新版 `scripts/win_test.ps1`（脚本输出为英文，避免 Windows 编码问题）。

### 前置条件

| 组件 | 说明 |
|------|------|
| Python 3.12+ | [python.org](https://www.python.org/downloads/) 安装时勾选 "Add to PATH" |
| ffmpeg | 运行 `scripts\setup_dev.ps1`，或加入 PATH，或放到 `resources\ffmpeg\` |
| 网络 | 冒烟测试会请求 YouTube 获取视频信息 |

可选（Cookie 重试模式）：

- **deno**：Cookie 模式下 YouTube JS 挑战需要
- **Chrome/Edge/Safari**：用于读取浏览器 Cookie
- **手动 Cookie 文件**：若无法自动读取浏览器 Cookie，可在 GUI 导入 Netscape 格式的 `cookies.txt`（可用扩展 **Get cookies.txt LOCALLY** 导出）。Cookie 模式还需要 deno（`scripts\setup_dev.ps1` 会一并下载，或 `winget install Denoland.Deno` / `brew install deno`）

### 手动分步测试

```bash
python -m pip install -r requirements.txt pytest
python -m pytest tests/ -v
python smoke_test.py
python main.py
```

## 使用（GUI）

1. 导入 CSV / 文件夹 / 粘贴链接（支持拖动链接），或输入单个 Video ID
2. 播放列表 / 频道 URL 会自动展开（最多 500 条）
3. 选择清晰度、保存目录；可勾选 **仅音频**（m4a / mp3 / opus）、字幕（中英 / 中文 / 英文）、元数据、封面
4. 可设置代理与单条限速；「浏览器指纹（实验）」仅影响取信息阶段，需安装 `curl-cffi`
5. 点击 **下载**（批量可开暴力并行）；暂停会在当前视频结束后生效，取消会停止整批
6. 结果写入输出根目录的 `下载总览.csv`；失败项可右键重试，「历史」按钮可查看总览

## 批量 CLI

```bash
python -m download_cli -o ./out inputs/            # 文件夹/CSV
python -m download_cli -o ./out "https://www.youtube.com/playlist?list=..." --playlist-limit 50
python -m download_cli -o ./out urls.txt --audio --audio-format mp3
python -m download_cli -o ./out urls.txt --subs --metadata --thumbnail
python -m download_cli -o ./out urls.txt --proxy http://127.0.0.1:7897 --limit-rate 5M
```

常用参数：`--min-height 1080`、`--workers 4`、`--brutal`、`--no-skip-existing`、
`--playlist-limit`（0 = 不限）、`--audio` / `--audio-format`、`--subs` / `--subs-langs` /
`--metadata` / `--thumbnail`、`--proxy` / `--limit-rate`。

## 项目结构

```
youtube_down/
├── main.py            # GUI 入口
├── gui.py / theme.py  # 纯 UI（不 import yt_dlp）
├── worker.py          # QThread，连接 UI 与逻辑
├── downloader.py      # yt-dlp 封装、校验、Cookie
├── download_cli.py    # 批量 CLI
├── batch_io.py        # 输入发现、输出镜像、下载总览
├── pacing.py          # 自适应节流
├── platforms/         # YouTube / Bilibili / TikTok
└── .github/workflows/build.yml  # Windows + macOS 打包
```

## 解耦设计

- `gui.py` 不直接调用 yt-dlp
- 下载逻辑在 `downloader.py` / `platforms/`，可供 CLI 复用
- `worker.py` 负责后台线程，通过 Qt Signals 更新 GUI

推送到 `main` 会自动构建 Windows `.exe` 与 macOS `.zip`，并发布到 [Releases](https://github.com/Jqsfree/youtube_down/releases)。
