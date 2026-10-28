# YouTube Downloader — 构建与架构笔记

## 技术栈

| 组件 | 选型 | 原因 |
|------|------|------|
| GUI | PySide6 | Qt 官方绑定，原生控件，支持 DPI 缩放 |
| 下载核心 | yt-dlp | 活跃维护，YouTube/Bilibili/TikTok 适配快 |
| 音视频合并 | ffmpeg | yt-dlp 直接调用；ffprobe 用于下载后校验 |
| 后台线程 | QThread | 信号/槽更新 GUI |
| 打包 | PyInstaller | 单文件分发 |
| CI/CD | GitHub Actions | Windows + macOS 构建并发布 Release |

## 分层

```
gui.py + theme.py       ← 纯 UI / QSS，不 import yt_dlp
    ↓
worker.py               ← QThread，Qt Signal 连接两端
    ↓
platforms/ + downloader.py + batch_io.py   ← 纯逻辑，不 import PySide6
```

`downloader.py` 与平台插件可被 CLI / Web 复用；单元测试不需要显示服务器。

## 两阶段 Cookie 策略

```
Stage 1: 无 Cookie 下载 —— 99% 公开视频直接成功
Stage 2: 仅对 BOT_VERIFICATION / AUTH_REQUIRED / PRIVATE_VIDEO
         用浏览器 Cookie 重试
```

若浏览器 Cookie 读取失败（如「copy chrome cookie」错误），本次会话跳过后续所有 Cookie 尝试。

## 错误分类（10 类）

`_CATEGORY_RULES` 按优先级匹配关键词，每类带 `retry_cookie`：

```
SUCCESS / BOT_VERIFICATION / AUTH_REQUIRED / PRIVATE_VIDEO
RATE_LIMIT / VIDEO_UNAVAILABLE / NETWORK_ERROR
CANCELLED / ENVIRONMENT / UNKNOWN
```

结果写入输出根目录的 `下载总览.csv`（含 `错误类型`、`错误信息`）。

## 项目结构

```
youtube_down/
├── main.py                 # 入口
├── gui.py                  # 主窗口（纯 UI）
├── theme.py                # 主题 / QSS / QSettings
├── worker.py               # BatchDownloadWorker、PlaylistExpandWorker 等
├── downloader.py           # yt-dlp 封装、错误分类、校验、环境检测
├── batch_io.py             # 输入发现、输出镜像、下载总览.csv
├── platforms/
│   ├── base.py             # Platform / MediaInput / ABC
│   ├── youtube.py
│   ├── bilibili.py
│   ├── tiktok.py
│   └── facade.py           # 按平台路由（YoutubeDownloader）
├── download_cli.py         # 命令行入口
├── scripts/                # 诊断与压力测试脚本（不参与 pytest 收集）
├── tests/                  # 单元测试
├── smoke_test.py           # CI 冒烟测试
└── .github/workflows/build.yml
```

## 下载后校验

四项依次执行，任一失败即报错并写入总览：

1. 文件存在且大小 > 1KB
2. `ffprobe` 能解析时长（有效媒体；ffprobe 缺失记为 `ENVIRONMENT`）
3. 实际时长与预期相差 ≤ 5%
4. 指定 `min_height` 时，ffprobe 校验实际高度

仅音频模式把视频轨检查替换为音频轨检查。

## 环境依赖

| 组件 | 检查方式 | 用途 |
|------|---------|------|
| yt-dlp（`[default,curl-cffi]`） | `import yt_dlp` | 下载核心、浏览器指纹 |
| ffmpeg | `shutil.which` | 合并 / 转码 / 内嵌字幕 |
| ffprobe | `shutil.which` | 下载后校验（致命依赖） |
| yt-dlp-ejs + deno | 探测 | YouTube JS 挑战 |

Cookie 由 yt-dlp 直接读取浏览器 Cookie 数据库（`cookiesfrombrowser`），不使用 `browser_cookie3`。

## 构建

### 本地（验证打包逻辑）

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "YouTubeDownloader" \
  --add-data "VERSION:." \
  --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all curl_cffi \
  --hidden-import PySide6.QtCore --hidden-import PySide6.QtWidgets \
  --hidden-import platforms --hidden-import platforms.youtube \
  --hidden-import platforms.bilibili --hidden-import platforms.tiktok \
  --hidden-import theme --hidden-import batch_io \
  main.py
```

Windows 用 `;` 作为 `--add-data` 分隔符；macOS/Linux 用 `:`。

### CI

推送到 `main` 触发 `.github/workflows/build.yml`：

1. Windows / macOS 矩阵，Python 3.12
2. `pip install -r requirements.txt pytest pyinstaller`
3. 单元测试（`QT_QPA_PLATFORM=offscreen`）
4. 下载 ffmpeg / ffprobe / deno 并内嵌
5. `smoke_test.py`
6. PyInstaller 单文件打包 → 上传 artifact
7. `release` job 按 `VERSION` 创建 GitHub Release

产物：`YouTubeDownloader-Windows.exe`、`YouTubeDownloader-macOS.zip`。

## 下载总览.csv

utf-8-sig，输出根目录唯一一份，按 `(来源文件, video_id)` upsert：

```csv
来源文件,行号,video_id,平台,链接,标题,状态,错误类型,错误信息,输出文件,更新时间
videos.csv,2,dQw4w9WgXcQ,youtube,https://www.youtube.com/watch?v=dQw4w9WgXcQ,标题,成功,,,out/videos/0002_dQw4w9WgXcQ.mp4,2026-01-01 12:00:00
```

写入带跨进程文件锁（`fcntl.flock` / `msvcrt.locking`），原子替换。
