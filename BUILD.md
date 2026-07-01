# YouTube Downloader — 构建与架构笔记

## 技术栈

| 组件 | 选型 | 原因 |
|------|------|------|
| GUI | PySide6 | Qt 官方 Python 绑定，原生控件，支持 DPI 缩放，跨平台 |
| 下载核心 | yt-dlp | 活跃维护的 youtube-dl 分支，YouTube 适配最快 |
| 音视频合并 | ffmpeg | 业界标准，yt-dlp 直接调用 |
| 后台线程 | QThread | PySide6 原生，信号/槽更新 GUI，零卡死 |
| 打包 | PyInstaller | 单文件分发，无需用户安装 Python |
| CI/CD | GitHub Actions | 自动构建 Windows .exe |

## 为什么这样设计

### GUI 与下载逻辑完全解耦

```
gui.py          ← 纯 UI，不 import yt_dlp
    ↓ 调用
downloader.py   ← 纯逻辑，不 import PySide6
    ↓ 桥接
worker.py       ← QThread，信号连接两端
```

好处：
- `downloader.py` 可以独立被其他项目（CLI、Web）调用
- 单元测试不需要启动 GUI
- 换 GUI 框架不影响下载逻辑

### 两阶段 Cookie 策略

```
Stage 1: 无 Cookie 下载
  └─ 99% 公开视频直接成功

Stage 2: Cookie 重试（仅 3 种错误触发）
  ├─ BOT_VERIFICATION  → YouTube 要求验证
  ├─ AUTH_REQUIRED     → 年龄/登录限制
  └─ PRIVATE_VIDEO     → 私有视频
```

如果默认先尝 Cookie，10000 个视频 = 20000 次网络请求（全浪费）。
反过来，99% 只需 1 次请求。

### 错误分类（9 类）

```
SUCCESS           下载成功
BOT_VERIFICATION  YouTube 反爬检测 → 触发 Cookie 重试
AUTH_REQUIRED     需要登录/年龄验证 → 触发 Cookie 重试
PRIVATE_VIDEO     私有视频 → 触发 Cookie 重试
RATE_LIMIT        HTTP 429 限流 → 不重试
VIDEO_UNAVAILABLE 已删除/下架/版权 → 不重试
NETWORK_ERROR     超时/SSL/连接中断 → 不重试
CANCELLED         用户取消 → 不重试
UNKNOWN           未分类 → 不重试
```

所有结果写入 `batch_results_*.csv`，包含 `error_category`、`cookie_used`。

## 项目结构

```
youtube_downloader/
├── main.py                  # 入口，QApplication + MainWindow
├── gui.py                   # 主窗口（纯 UI）
├── downloader.py            # yt-dlp 封装 + 错误分类 + 环境检测
├── worker.py                # DownloadWorker / BatchDownloadWorker
├── requirements.txt         # Python 依赖
├── README.md                # 使用说明
├── BUILD.md                 # 本文档
└── .github/workflows/
    └── build.yml            # CI：自动构建 Windows .exe
```

## 核心流程

### 单视频

```text
输入 Video ID → get_info() → 选格式 → download()
                                    ↓
                              progress_hooks
                                    ↓
                          GUI 实时刷新进度/速度/ETA
```

### 批量下载（Load CSV）

```text
Load CSV
  ↓
取前 5 个 video_id → 后台线程分析格式交集 → 展示共有格式
  ↓
用户选格式 → 点击下载
  ↓
for each video:
  ├─ Stage 1: get_info(cookies=F) → download(cookies=F)
  │   ├─ 成功 → 下一个
  │   └─ 失败 → classify_error()
  │       ├─ retry_cookie=True  → Stage 2
  │       └─ retry_cookie=False → 记录失败
  │
  └─ Stage 2: get_info(cookies=T) → download(fmt="best")
      ├─ 成功 → 下一个 (cookie_used=true)
      └─ 失败 → 记录失败 → 重检浏览器 → 再试一次
```

### 格式降级

```text
用户选 720p mp4 Video+Audio
  ↓
某视频没有 720p → 自动降级到 480p mp4 Video+Audio
  ↓
还没有？→ 降级到 360p
  ↓
还没有？→ "best"（yt-dlp 自选最优）
```

## 下载后校验

```text
文件存在 → size > 1KB → ffprobe 能解析 duration
  ↓              ↓              ↓
  失败           失败            失败
  → 报错         → 0字节空壳      → 文件损坏
```

## 环境依赖

### 必需

| 组件 | 检查方式 | 用途 |
|------|---------|------|
| yt-dlp | `import yt_dlp` | 下载核心 |
| ffmpeg | `shutil.which("ffmpeg")` | 音视频合并 |
| ffprobe | `shutil.which("ffprobe")` | 下载后校验文件完整性 |

### Cookie 模式额外需要

| 组件 | 原因 |
|------|------|
| yt-dlp-ejs | yt-dlp 的 JS 挑战解决脚本 |
| deno | JS 运行时，执行挑战脚本 |
| 浏览器 Cookie 目录 | Chrome/Firefox/etc 的 SQLite cookie DB |

所有依赖在启动时自动检测，缺失弹窗提示。

## 构建 .exe

### 本地（Linux，验证打包逻辑用）

```bash
pip install pyinstaller
pyinstaller --onefile --windowed \
  --name "YouTubeDownloader" \
  --hidden-import PySide6.QtCore \
  --hidden-import PySide6.QtWidgets \
  --hidden-import yt_dlp \
  --hidden-import yt_dlp.extractor \
  --hidden-import yt_dlp.downloader \
  main.py
./dist/YouTubeDownloader
```

### GitHub Actions（生成 Windows .exe）

推送到 `main` 分支自动触发。workflow：
1. `windows-latest` runner
2. 安装 PySide6 + yt-dlp + PyInstaller
3. 下载 ffmpeg Windows 二进制
4. PyInstaller 打包单文件，内嵌 ffmpeg
5. 上传 artifact

在 Actions 页面下载 `YouTubeDownloader.exe`。

## Cookie 浏览器检测

```text
Linux:   ~/.config/google-chrome/{Default,Profile 1,Profile 2,...}/Cookies
         ~/.mozilla/firefox/
Windows: %LOCALAPPDATA%\Google\Chrome\User Data\{Default,Profile 1,...}\Cookies

优先级: Chrome(Default→Profile*) → Firefox → Edge → Chromium → Brave → Opera
```

yt-dlp 通过 `browser_cookie3` 库直接读取 SQLite，使用系统 keyring 解密。不需要打开浏览器。

## 结果 CSV 格式

```csv
video_id,status,error_category,error_message,cookie_used
dQw4w9WgXcQ,success,SUCCESS,,false
jNQXAC9IVRw,success,SUCCESS,/path/to/file.mp4,true
INVALID_ID,failed,VIDEO_UNAVAILABLE,Video unavailable,false
```
