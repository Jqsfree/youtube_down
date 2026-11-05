# 使用手册

适用：仓库 `main` 源码。正式安装包与源码存在版本差异，先阅读 [README 的版本说明](../README.md#下载与支持范围)。

## 选择运行方式

- 普通用户：下载 [Releases](https://github.com/Jqsfree/youtube_down/releases) 中与系统架构相符的正式安装包，启动 GUI。
- 开发者或 Ubuntu 用户（Linux 源码运行尚未进行正式交付验收）：Python 3.12+，从仓库根目录运行 `uv sync --locked`，准备 `ffmpeg`、`ffprobe`；使用 `uv run --no-sync python main.py` 打开 GUI，或用 `uv run --no-sync python -m download_cli -h` 查看 CLI 帮助。
- Windows 源码环境可运行 `powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1` 准备媒体工具。正式安装包已内嵌 ffmpeg/ffprobe/deno。

## GUI 操作流程

1. 粘贴视频链接或 ID、拖入链接，或导入 CSV/TSV/TXT 文件及文件夹。
2. 播放列表/频道输入会展开为单个视频；GUI 默认最多 500 条。
3. 选定输出目录、最低清晰度和可选项目（仅音频、字幕、封面、元数据、代理、限速）。
4. 点击「下载」。正在下载时允许继续添加任务；后加任务由活动队列或下一批接收。
5. 暂停作用于视频之间，不保证立刻暂停当前下载；取消会尝试终止本批。
6. 输出目录打开 `下载总览.csv`；在 GUI 可通过「历史」查看状态与失败原因。

可用选项及可下载清晰度取决于视频与平台；`浏览器指纹（实验）` 仅影响信息提取。

## CLI 下载前预览（main 新功能）

对批量输入先生成计划，核对待下载、复用已有文件和重复来源，再开始真实下载：

~~~bash
uv run --no-sync python -m download_cli --dry-run -o ./out ./inputs/
uv run --no-sync python -m download_cli -o ./out ./inputs/
~~~

`--dry-run` 输出 JSON，包括每条任务的 `source`、`media_id`、`action`（`download` / `reuse` / `duplicate`）、输出路径和历史状态；它不会创建输出目录、更新总览、下载媒体或读取 Cookie。**播放列表/频道 URL 的展开可能联网**，预览并非绝对离线；本地已有媒体的检查可能运行 ffprobe。预览的 `reuse` 只表示现有文件通过轻量探测，不是完整帧解码或内容哈希认证。大批量任务先用小样本验证输出布局。

## CLI 常用命令

所有示例均从仓库根目录执行。使用有权下载的媒体 URL，不要直接把示例 ID 当成稳定的网络验收素材。

~~~bash
# 单条 URL；-o 指定输出根目录
uv run --no-sync python -m download_cli -o ./out "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# CSV/TSV/TXT 或整个目录；递归发现输入表格
uv run --no-sync python -m download_cli -o ./out ./inputs/

# 播放列表/频道：至多展开 50 条（0 为不设上限）
uv run --no-sync python -m download_cli -o ./out "https://www.youtube.com/playlist?list=PLAYLIST_ID" --playlist-limit 50

# 仅音频
uv run --no-sync python -m download_cli -o ./out ./urls.txt --audio --audio-format mp3

# 需要附加字幕、元数据、封面
uv run --no-sync python -m download_cli -o ./out ./urls.txt --subs --metadata --thumbnail

# 指定代理与单条限速；默认不会自动使用代理
uv run --no-sync python -m download_cli -o ./out ./urls.txt --proxy http://127.0.0.1:7897 --limit-rate 5M
~~~

常用参数：

| 参数 | 含义及当前默认值 |
| --- | --- |
| `-o, --output-dir` | 必选，输出根目录与集中总览路径 |
| `--min-height` | 最低分辨率，默认 720；仅音频模式不使用 |
| `--workers` | 视频并发数，默认 1；`--brutal` 未指定时默认 8 |
| `--fragment-workers` | 单视频分片并发，普通模式默认 12，brutal 默认 8 |
| `--playlist-limit` | 播放列表/频道展开上限，默认 500，0 不限制 |
| `--sleep-min` / `--sleep-max` | 视频间隔默认 2 / 4 秒 |
| `--no-skip-existing` | 不按已有媒体文件跳过 |
| `--cookies` | 明确指定 Netscape 格式 Cookie 文件 |
| `--cookies-from-browser` | 明确指定浏览器；默认不读取 |
| `--no-auto-recover` | 关闭 CLI 遇到风控后的自动恢复尝试 |

以 `python -m download_cli --help` 的实际输出为最终参数来源。

**退出状态：** 成功（包括无待下载任务）返回 0；存在任务失败返回 1；输入/目录或历史总览不可用等返回 2；Cookie 准备失败返回 4；Ctrl-C 返回 130。部分系统级错误可能直接抛异常，详见 [排查](troubleshooting.md)。

## 输入、输出和续传

输入支持 CSV、TSV、TXT、含这些文件的目录，或链接/ID。输入表格包含可识别的媒体列，兼容部分无表头格式；建议显式提供 `url` 列。

~~~text
inputs/
  set-a.csv
  group/set-b.txt
out/
  set-a/<media-id>.mp4
  group/set-b/<media-id>.mp4
  下载总览.csv
~~~

上述目录只是示意；实际子目录、文件扩展名和部分行号前缀取决于输入类型与下载结果。总览按 `(来源文件, video_id)` 维护唯一结果行，并记录状态、错误类型、输出文件与更新时间；同一来源内重复行可能被去重。不要自行修改工作中的 `下载总览.csv`。

重新执行相同 CLI 命令可以跳过已校验的媒体文件。**不等于进程内断点无损恢复**：任务被取消或风控熔断后，尚未完成的任务可在重跑时重新处理。总览记录损坏时 `main` 源码会拒绝覆盖历史；先备份，再修复 CSV 或使用新输出目录。

## Cookie 与权限

Cookie 相当于账户凭据。默认不自动读取浏览器。仅在账户有权限且有必要时在 GUI 显式导入平台 Cookie，或在 CLI 传 `--cookies ./cookies.txt` / `--cookies-from-browser chrome`。浏览器导出会建立应用专用的临时副本；不要把 Cookie 文件加入仓库、上传到 Issue 或发在日志中。

GUI 批量模式先尝试无 Cookie，遇到特定身份验证/风控错误时才按策略重试；CLI 使用显式授权的 Cookie 策略。两者不是完全相同的下载选项。更多见 [架构说明](architecture.md#gui-与-cli-策略差异) 与 [故障排查](troubleshooting.md)。

## 使用边界

- 只有与视频匹配的格式可用时才可能达到设定清晰度；字幕或封面也可能不存在。
- `--brutal` 增加并发和重试可能触发站点限流，不推荐作为所有任务的默认选项。
- 默认 `--proxy-check-url` 会检查本机示例代理端口的出口 IP；不等于下载时默认启用代理。可传 `--proxy-check-url ""` 跳过此预检。
- 自动更新针对打包应用；运行源码不会直接覆盖你的 Git 工作区。

另请参阅 [故障排查](troubleshooting.md) 和 [开发验收](development.md)。