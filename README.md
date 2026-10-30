# Multi-Platform Downloader

基于 yt-dlp + PySide6 的桌面下载工具，支持 YouTube / Bilibili / TikTok。

## 下载安装包（推荐）

最新版：[v1.3.1](https://github.com/Jqsfree/youtube_down/releases/tag/v1.3.1)

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows (x64) | [YouTubeDownloader-Windows.exe](https://github.com/Jqsfree/youtube_down/releases/download/v1.3.1/YouTubeDownloader-Windows.exe) | 双击运行；已内嵌 ffmpeg / ffprobe / deno |
| macOS (Apple Silicon) | [YouTubeDownloader-macOS.zip](https://github.com/Jqsfree/youtube_down/releases/download/v1.3.1/YouTubeDownloader-macOS.zip) | 解压后打开 `.app`；已内嵌 ffmpeg / ffprobe / deno |
| 校验 | [SHA256SUMS.txt](https://github.com/Jqsfree/youtube_down/releases/download/v1.3.1/SHA256SUMS.txt) | 应用内更新会自动校验 |

> **v1.3.0 起支持应用内更新。** 1.2.0 及更早版本没有更新入口，需手动下载一次
> v1.3.0；之后即可在「设置 → 检查更新」一键升级。

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
- **Chrome/Edge/Safari**：在 GUI 点击重新读取后才启用浏览器 Cookie，默认不读取、不自动导出。CLI 需显式指定 `--cookies-from-browser chrome`。
- **手动 Cookie 文件**：若不使用浏览器读取，可在 GUI 导入 Netscape 格式的 `cookies.txt`（可用扩展 **Get cookies.txt LOCALLY** 导出）。Cookie 模式还需要 deno（`scripts\setup_dev.ps1` 会一并下载，或 `winget install Denoland.Deno` / `brew install deno`）

Cookie 等同账号凭据。导入文件保持原样，yt-dlp 仅操作按平台过滤的临时副本，并在该次操作结束（含异常、取消）后清理。CLI 显式浏览器导出只保存受支持平台域名，存于当前进程私有临时目录，退出时清理；强制杀进程可能留下临时目录。旧版 `~/.youtube_downloader/auto_browser_cookies.txt` 或输出目录 `.yt_cookies.txt` 不再自动读取；确认无旧进程使用后可手动删除。

应用内更新必须提供有效的 SHA256 清单和当前资产条目；缺失时只能手动下载。SHA256 用于完整性验证，不等同发布者身份认证。安装脚本目前只处理替换失败回滚，不保证新版本启动后的健康回滚。尚未提供 Intel Mac 和 Windows ARM 的对应更新包。

### 手动分步测试

```bash
uv sync --locked --group build
uv run --no-sync python -m pytest tests/ -v
uv run --no-sync python smoke_test.py
python main.py
```

## 使用（GUI）

1. 导入 CSV / 文件夹 / 粘贴链接（支持拖动链接），或输入单个 Video ID
2. 播放列表 / 频道 URL 会自动展开（最多 500 条）
3. 选择清晰度、保存目录；可勾选 **仅音频**（m4a / mp3 / opus）、字幕（中英 / 中文 / 英文）、元数据、封面
4. 可设置代理与单条限速；「浏览器指纹（实验）」仅影响取信息阶段，需安装 `curl-cffi`
5. 点击 **下载**（批量可开暴力并行）；暂停会在当前视频结束后生效，取消会停止整批
6. 运行中仍可继续「加入队列」或粘贴新 ID，任务会立刻排进当前活动队列继续下载
7. 结果写入输出根目录的 `下载总览.csv`；失败项可右键重试，「历史」按钮可查看总览

## 检查更新与图标

- 设置页「检查更新」会读取 GitHub Releases 最新版本：有新版可一键下载并自动替换、重启；
  启动后 24 小时内也会静默检查一次。源码运行时只会打开 Releases 页面。
- 下载时会校验 release 的 `SHA256SUMS.txt`（有该资产时强制校验，哈希不符即删除并报错）。
- 图标源文件是 `assets/icon.svg`，打包用 `icon.ico`（Windows）/ `icon.icns`（macOS）。
  改完图标后运行 `python scripts/make_icon.py` 重新生成。

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
├── app_updater.py     # GitHub Release 检查 / 下载 / 自替换
├── download_cli.py    # 批量 CLI
├── batch_io.py        # 输入发现、输出镜像、下载总览
├── pacing.py          # 自适应节流
├── platforms/         # YouTube / Bilibili / TikTok
├── assets/            # 图标（icon.svg / png / ico / icns）
└── .github/workflows/build.yml  # Windows + macOS 打包
```

## 解耦设计

- `gui.py` 不直接调用 yt-dlp
- 下载逻辑在 `downloader.py` / `platforms/`，可供 CLI 复用
- `worker.py` 负责后台线程，通过 Qt Signals 更新 GUI

推送 `vX.Y.Z` tag 会构建 Windows `.exe` 与 macOS `.zip`，通过检查后仅创建 [Releases](https://github.com/Jqsfree/youtube_down/releases) 草稿；维护者验收后手动发布。

### 开发检查与发布

PR 和 main 普通提交仅运行离线测试；真实 YouTube 冒烟独立每日/手动运行。发布使用 `vX.Y.Z` tag，构建成功后只创建草稿，需完成人工升级验证再发布。手动构建默认不创建发布，同版本资产禁止覆盖。依赖和工具更新方式见 [BUILD.md](BUILD.md#依赖与外部工具)。

### 查找故障日志

GUI 下载页点击“打开日志目录”；当前日志为 Downloads 下的 `youtube_downloader.log`。CLI 日志在结果目录（未指定时为输出目录）下。日志自动轮转，记录版本、任务阶段和错误类别，已知凭据字段、Cookie 路径与 URL 会被隐藏；旧版历史日志保留原样。

核心逻辑保持 downloader/YoutubeDownloader 兼容入口；Cookie IO、媒体探测/校验和错误分类分别由 cookies、media_probe、download_errors 承担，均可脱离 GUI 使用。开发结构与兼容约束见 [BUILD.md](BUILD.md#核心逻辑拆分与兼容)。

批量执行可通过不依赖 Qt 的 `batch_runner.BatchRunner` 复用，GUI/CLI 入口保持兼容。总览保存失败时会停止后续任务并保留下载文件；CLI Ctrl-C 返回中断状态 130。完整策略差异与控制约束见 [BUILD.md](BUILD.md#批量执行核心)。
