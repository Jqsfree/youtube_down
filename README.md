# Multi-Platform Downloader

基于 **yt-dlp + PySide6** 的本地桌面下载器，支持 YouTube、Bilibili、TikTok 的单条与批量媒体下载。提供图形界面（GUI）和命令行（CLI），下载结果保存在本机。

> **版本说明（2026-11-05）**：最新**已发布安装包**为 [v1.4.0](https://github.com/Jqsfree/youtube_down/releases/tag/v1.4.0)。v1.5.0 已产生草稿但因真实下载验收未通过而未发布；源码正准备 **v1.5.1** 修复版本，包含后续可靠性修复；在新 Release **尚未**完成构建和发布时，**v1.4.0 仍是可下载的最新正式安装包**。版本号更新不等于安装包已交付。

## 下载与支持范围

| 运行方式 | 平台 | 状态 |
| --- | --- | --- |
| [Windows x64 EXE](https://github.com/Jqsfree/youtube_down/releases/download/v1.4.0/YouTubeDownloader-Windows.exe) | Windows x64 | v1.4.0 正式包，内含 ffmpeg/ffprobe/deno |
| [macOS ZIP](https://github.com/Jqsfree/youtube_down/releases/download/v1.4.0/YouTubeDownloader-macOS.zip) | Apple Silicon | v1.4.0 正式包，内含 ffmpeg/ffprobe/deno |
| 源码运行 | Python 3.12+，Windows/macOS；Linux 可尝试 | 需自行准备运行依赖及 ffmpeg/ffprobe；Linux 尚无正式安装包或本仓库原生交付验收 |

完整发布资产与摘要见 [Releases](https://github.com/Jqsfree/youtube_down/releases)；校验文件为 [SHA256SUMS.txt](https://github.com/Jqsfree/youtube_down/releases/download/v1.4.0/SHA256SUMS.txt)。当前不提供 Intel Mac、Windows ARM 或 Linux 的对应正式安装包。macOS 包未经 Apple Developer ID 公证，首次打开可能需要通过系统安全设置手动允许。

## 五分钟开始

**GUI：** 下载符合平台的安装包 → 打开软件 → 粘贴视频链接或导入 CSV/文件夹 → 选择输出目录和清晰度 → 点击「下载」。运行时可以继续追加链接；暂停在当前视频完成后生效，取消会结束当前批次。输出目录包含 `下载总览.csv`，可以查询成功、失败、跳过记录。

**CLI：** 使用源码环境（Python 3.12+），在仓库根目录执行：

~~~bash
uv sync --locked
uv run --no-sync python -m download_cli -o ./out "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
# 或批量导入文件夹
uv run --no-sync python -m download_cli -o ./out ./inputs/
~~~

源码运行需要可用的 `ffmpeg` 和 `ffprobe`；Windows 可使用 `scripts/setup_dev.ps1` 配置开发环境。GUI 入口：

~~~bash
uv run --no-sync python main.py
~~~

注意：直接通过 `pip install -r requirements.txt` 运行也受支持，但开发与测试以 `uv.lock` 为准；`requirements.txt` 是锁文件的生成产物。

## 主要能力与边界

| 能力 | 行为 |
| --- | --- |
| 输入 | 视频 URL/ID；CSV、TSV、TXT；递归导入文件夹；播放列表/频道 URL 展开 |
| 批量执行 | 顺序下载或可选高并发模式；支持 `--dry-run` JSON 预检；有效媒体复用、运行中追加任务（main 新功能未发布） |
| 输出 | 按输入来源生成子目录，集中维护 `下载总览.csv` |
| 媒体选项 | 视频清晰度、仅音频（m4a/mp3/opus）、字幕、元数据与封面 |
| 权限与 Cookie | 默认不自动读取浏览器；GUI 按平台导入，CLI 显式授权 |
| 完整性 | 下载后使用 ffprobe 检查媒体；GUI 与 CLI 的格式选择、Cookie 重试策略不完全相同 |
| 更新 | 正式安装包支持检查 GitHub Release；源码运行不会自动自替换 |

**限制：** YouTube 等平台的登录、年龄验证、机器人校验、限流与格式可用性由站点状态和账户权限决定；软件不保证所有链接均可下载。高并发可能更容易触发限流。下载和使用内容时应遵守平台规则及适用法律。

## 文档导航

- [使用手册：GUI、CLI、输入输出、Cookie](docs/usage.md)
- [故障排查：错误类别、日志、修复步骤](docs/troubleshooting.md)
- [开发和测试：本地运行、CI、真实下载验收](docs/development.md)
- [当前架构与设计取舍](docs/architecture.md)
- [构建、签名限制和发布流程](BUILD.md)
- [版本变更](CHANGELOG.md) · [开发者/Agent 约束](AGENTS.md) · [文档索引](docs/README.md)

发现问题时请使用 [GitHub Issues](https://github.com/Jqsfree/youtube_down/issues)，附上平台、版本、复现步骤、预期与实际结果、**脱敏日志**。不要上传 Cookie、代理口令或账户凭据。
