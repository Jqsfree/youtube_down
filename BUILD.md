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

## 活动下载队列

`BatchDownloadWorker` 运行中可持续追加任务：

- GUI 启动 worker 前调用 `enable_live_queue()`；初始列表跑完后 worker 不会退出，
  而是在 `_idle_exit_seconds`（默认 3s）内等待 `enqueue()`。
- 下载中「加入队列」「粘贴」保持可用；新 ID 经 `_enqueue_live()` 直接进活动队列，
  进度分母由 `total_changed` 同步增长。
- `enqueue()` 在 worker 已收尾 / 已取消时返回 `False`，GUI 回退到下一组 `_csv_queue`。
- `close_queue()`（取消）立即结束等待；`_queue_open=False` 时保持旧的「一次性列表」行为，
  直接调用 `_run_sequential` / `_run_brutal_parallel` 的测试不受影响。

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
├── app_updater.py          # GitHub Release 检查 / 下载 / 自替换
├── yt_dlp_updater.py       # yt-dlp overlay 热更新
├── platforms/
│   ├── base.py             # Platform / MediaInput / ABC
│   ├── youtube.py
│   ├── bilibili.py
│   ├── tiktok.py
│   └── facade.py           # 按平台路由（YoutubeDownloader）
├── download_cli.py         # 命令行入口
├── assets/                 # 图标（svg 源 + png/ico/icns 产物）
├── scripts/                # 诊断、压力测试与图标生成脚本
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

浏览器 Cookie 必须由 GUI 重新读取操作或显式 CLI 参数授权，不使用 `browser_cookie3`。导入文件由每次 yt-dlp 操作使用按平台过滤的独立临时副本；CLI 浏览器导出仅包含支持的平台域名，快照随 CLI 会话结束删除。`private_files.py` 负责 POSIX/Windows 私有目录权限。

## 构建

### 本地（验证打包逻辑）

```bash
uv sync --locked --group build
# Windows
uv run --no-sync pyinstaller --onefile --windowed --name "YouTubeDownloader" \
  --icon assets/icon.ico --add-data "assets;assets" --add-data "VERSION;." \
  --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all curl_cffi \
  --hidden-import PySide6.QtCore --hidden-import PySide6.QtWidgets \
  --hidden-import platforms --hidden-import platforms.youtube \
  --hidden-import platforms.bilibili --hidden-import platforms.tiktok \
  --hidden-import theme --hidden-import batch_io \
  main.py

# macOS
uv run --no-sync pyinstaller --onedir --windowed --name "YouTubeDownloader" \
  --osx-bundle-identifier com.jqsfree.youtubedownloader \
  --icon assets/icon.icns --add-data "assets:assets" --add-data "VERSION:." \
  --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all curl_cffi \
  --hidden-import PySide6.QtCore --hidden-import PySide6.QtWidgets \
  --hidden-import platforms --hidden-import platforms.youtube \
  --hidden-import platforms.bilibili --hidden-import platforms.tiktok \
  --hidden-import theme --hidden-import batch_io \
  main.py
```

平台差异：

- Windows 用 `--onefile`（单 exe 是 Windows 的惯例）。
- **macOS 必须用 `--onedir`**：`--onefile --windowed` 已被 PyInstaller 6.22 标记弃用
  （v7 会直接报错），且每次启动都要把上百 MB 解包到临时目录。两者产出的 zip 体积
  基本一致（实测 113 MB vs 116 MB）。
- `--add-data` 分隔符：Windows `;`，macOS/Linux `:`；图标：Windows `icon.ico`，
  macOS `icon.icns`。

## 图标资产

`assets/icon.svg` 是唯一源文件，`scripts/make_icon.py` 生成其余格式：

```bash
python scripts/make_icon.py      # png(1024) / ico(7 尺寸) / icns(仅 macOS)
python scripts/make_icon.py --check
```

- 运行时通过 `theme.asset_path()` 定位（frozen 时读 `sys._MEIPASS/assets`），
  `theme.app_icon()` 在 `main.py` 与主窗口上设置。
- ICO 用纯 Python 写 PNG-in-ICO，不依赖 Pillow；ICNS 依赖 macOS 的 `iconutil`。
- 生成结果需提交进仓库，CI 不再重复生成。

## 应用自更新

`app_updater.py` 读取 `VERSION` 与 GitHub latest release 比较，下载对应平台资产后
用一个分离脚本「等旧进程退出 → 替换 → 重启」：

| 平台 | 资产 | 替换方式 |
|------|------|---------|
| Windows | `YouTubeDownloader-Windows.exe` | 私有目录 `.ps1` 重命名重试循环，JSON 传递路径 |
| macOS | `YouTubeDownloader-macOS.zip` | `ditto -x -k` 解压 + `xattr -dr` 去隔离 |

资产名必须与 `build.yml` 的 `artifact_name` 一致，否则更新会提示「没有本平台安装包」。

### 完整性校验

`build.yml` 为每个产物生成 `SHA256SUMS.txt`（格式 `<sha256>  <文件名>`），两张清单
在 release job 合并成一个资产。`download_update()` 下载完会查清单校验 sha256，
不符则删包并报错；没有有效清单或资产条目时拒绝自动更新，提示手动下载。

替换前会先停掉所有后台线程（`_shutdown_for_update`）：`QApplication.quit()`
不触发 `closeEvent`，运行中的 `QThread` 被析构会 abort，Windows 上还会因为
exe 仍被占用导致脚本替换失败。

### CI 与草稿发布

- `ci.yml`：PR、main 普通提交和手动触发，仅运行离线测试与版本/依赖同步检查。安装依赖需要访问包源，“离线”指测试不请求真实站点。
- `smoke.yml`：每日 UTC 03:17 或手动触发，独立运行现有 YouTube 联网冒烟；不创建发布。
- `build.yml`：推送 `vX.Y.Z` tag 后构建双平台产物并创建草稿；手动运行默认只构建。手动创建草稿必须填写已存在的 tag 并显式勾选 `create_draft`。
- tag、VERSION、pyproject 必须一致；手动传入 tag 时 checkout 该 tag，提交必须与 tag 指向一致。普通检查/构建只读，只有 release job 有 contents:write。
- 发布前重新校验两平台安装包摘要及构建记录，生成合并清单。已有相同 tag 的正式发布或草稿均拒绝覆盖；中断或失败后使用新补丁版本恢复，不修改历史资产。
- 草稿说明从当前版本 CHANGELOG 条目生成。维护者完成 Windows/macOS 启动、下载、升级验收后再在 GitHub 手动发布；流水线不自动将草稿设为公开最新版。

### 依赖与外部工具

发布环境使用 Python 3.12、uv 0.12.8。`pyproject.toml` 是依赖声明，`uv.lock` 锁定运行、dev（pytest/PyYAML）、build（PyInstaller）依赖。CI 和打包均使用 `uv sync --locked`，不在构建时升级 yt-dlp。

```bash
uv sync --locked --group build
uv run --no-sync python -m pytest tests/ -q
```

修改依赖后执行：

```bash
uv lock
uv export --locked --no-dev --no-emit-project --no-header -o requirements.txt > /dev/null
```

requirements.txt 是带哈希的运行依赖导出文件，不手工编辑。CI 重新导出并比较，避免与锁文件分叉。

`scripts/build-tools.json` 固定 Windows FFmpeg、双平台 Deno 的归档 URL 和 SHA256；下载后先校验归档摘要，再只提取指定工具文件。macOS 沿用 Homebrew FFmpeg，要求版本精确为清单中的 `formula_version`，版本漂移时拒绝构建；记录已安装 Homebrew 配方信息。Homebrew 的传递依赖和 runner 镜像仍可能变化，本批不承诺字节一致重建，也不会静默放宽版本检查。

工具安装在单元测试之后，避免本地二进制干扰工具解析测试。构建后从 Windows onefile 归档提取内嵌工具到临时目录运行；macOS 从 `.app` 中定位工具运行。Windows 核对内嵌工具与构建输入摘要一致；macOS 因 PyInstaller 会重写 Mach-O 路径并签名，改为验证 app 签名及工具实际版本/启动，同时分别记录输入和产物摘要。此处签名验证仅检查打包完整性，不代表已具备 Developer ID 公证。

每个平台上传 `build-info-<平台>.json`，包含源提交、uv.lock 摘要、实际 Python 包版本、工具下载来源、工具版本与摘要、最终资产摘要；macOS 另含 Homebrew 信息。源码 tag 和这两份记录共同用于追溯。

本地依赖解析、YAML/发布逻辑测试不能替代远程 Actions 双平台构建。第一批真实升级验收继续作为正式发布前门槛。
