# 故障排查

目标：先定位故障发生的**阶段**，再区分本站下载故障、环境问题、账户限制、程序缺陷。此文档不是规避平台限制的保证。

## 最小排查路径

1. 记录软件版本（已安装 Release 版本或 main 的 Git SHA）、OS/CPU、GUI/CLI、输入类型、是否使用 Cookie/代理。
2. 从 `下载总览.csv` 查看视频的 `状态`、`错误类型` 和 `错误信息`；记录发生于读取信息、下载、合并、校验还是写总览阶段。
3. 用单个**公开且确认可访问**的视频、小并发和新输出目录复现；不要立即加大并发。
4. 核实本机 `ffmpeg -version`、`ffprobe -version`、`uv run --no-sync python -m download_cli --help`，必要时检查系统代理可达性。
5. 查看脱敏日志；若仍不能定位，再按下表缩小范围。

## 常见症状

| 症状或错误类别 | 可能原因 | 核对与处理 |
| --- | --- | --- |
| `ENVIRONMENT`、无法合并或校验 | 缺 ffmpeg/ffprobe/deno，或工具版本/路径异常 | 检查 `ffmpeg -version`、`ffprobe -version`；安装正确工具并重试；查看打包工具校验结果 |
| `BOT_VERIFICATION`、`RATE_LIMIT` | 站点风控、访问限制、短时请求过多 | 降低并发和频率，等待后重试；先排除代理和网络异常；不要假定 Cookie 必定解决 |
| `AUTH_REQUIRED`、`PRIVATE_VIDEO` | 视频需要登录、年龄/会员权限或为私人内容 | 确认账户本身有观看权限；仅显式授权对应站点 Cookie；无法访问的私人内容不会因此变成可下载 |
| `VIDEO_UNAVAILABLE` | 视频删除、地区限制或所选格式不可用 | 在浏览器确认视频是否仍可访问；调整清晰度，检查来源 URL |
| `NETWORK_ERROR` | 连接超时、TLS/代理异常等 | 先检查直连和代理可达性，单条下载验证；不要把平台故障误判成代码错误 |
| `CANCELLED` | 用户中止或任务取消 | 检查总览和已有文件，重跑相同输入 |
| 刚完成下载但不存在/无效 | 媒体输出、合并或 ffprobe 校验失败 | 检查输出文件、磁盘空间及 ffmpeg/ffprobe；在本地复现并保留脱敏诊断 |
| 历史 `下载总览.csv` 不可读取 | 编码、表头或行结构损坏 | **停止写入，备份原文件**；使用新输出目录复现或离线修复备份，不要先删除记录 |
| GUI 显示历史/下载失败 | 总览损坏、权限、任务异常 | 先读 GUI 错误提示及日志；确认输出目录可写 |
| 检查更新 403/404/校验失败 | GitHub API 限流、发布资产缺失或摘要不匹配 | 通过仓库 [Releases](https://github.com/Jqsfree/youtube_down/releases) 核对版本/资产；校验失败禁止强制安装 |
| Windows 子进程黑窗、更新重启异常 | 可能运行旧版打包软件 | 核对安装包版本：2026-11-05 修复已在 main，**尚未纳入 v1.4.0 既有发布资产**；后续发布新包并做原生安装验证 |

错误类别由关键字匹配及部分真实 HTTP 状态推断，属于**诊断线索**，不是独立的服务器原因证明。

## 日志位置与安全

- GUI：下载页「打开日志目录」，通常在 `~/Downloads/youtube_downloader.log`。
- CLI：`--results-dir` 指定目录；未设置则输出目录下的 `youtube_downloader.log`。
- 现行日志默认轮转：2 MiB 当前日志 + 3 份备份；旧版本日志不会由本逻辑统一清除。

提交 Issue 前应检查日志是否包含私人视频链接、账号标识、代理地址、令牌或本机路径；自动脱敏不是无需人工审核的保证。**严禁公开 Cookie 文件、Authorization 头、代理密码、个人账户内容。**

## 将故障报告提交到 GitHub

使用 [Issues](https://github.com/Jqsfree/youtube_down/issues) 并提供：

~~~text
版本：Release vX.Y.Z 或 main <commit SHA>
系统：Windows/macOS/Linux + 架构
入口：GUI 或 CLI（隐藏 Cookie/密码）
最小复现：输入类型、主要参数、操作顺序
预期结果：
实际结果：总览状态、错误类别、出错阶段
验证：ffmpeg/ffprobe 版本、是否仅特定链接失败
附件：经过人工检查并脱敏的日志摘录（不要上传原始 Cookie）
~~~

与原生引擎有关的问题，可以先参考 [yt-dlp 的官方说明](https://github.com/yt-dlp/yt-dlp#readme)；这是分离 GUI 层与下载引擎层问题的常用方法。