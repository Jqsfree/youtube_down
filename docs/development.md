# 开发与验收

适用：当前仓库 `main`。修改前阅读 [AGENTS.md](../AGENTS.md) 和 [架构说明](architecture.md)，本页提供操作步骤与验收信号。

## 环境与运行

- Python 3.12+、[uv](https://docs.astral.sh/uv/)、Git；需要本机可以运行的 ffmpeg、ffprobe。部分 YouTube 场景还需要 deno。
- Windows/macOS 生产安装包包含媒体工具；源码环境与打包环境不能互相替代验收。
- Ubuntu/Linux 可源码运行，但当前无正式 Linux 发布资产。

~~~bash
git clone https://github.com/Jqsfree/youtube_down.git
cd youtube_down
uv sync --locked --group build
uv run --no-sync python main.py
uv run --no-sync python -m download_cli --help
~~~

Windows 如未准备媒体工具，可先运行 `powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1`。

## 测试分层与证据

| 层级 | 命令或工作流 | 可证明 | 不能证明 |
| --- | --- | --- | --- |
| 单元/集成（离线） | `uv run --no-sync python -m pytest tests/ -q` | 代码契约、Fake 引擎、Qt offscreen 回归 | 实际网站可下载、打包可升级 |
| 元数据联网冒烟 | `uv run --no-sync python smoke_test.py`；[Network smoke](../.github/workflows/smoke.yml) | 指定视频的 `get_info` 与运行环境在当时可用 | 真实媒体已传输并合并 |
| 真实媒体端到端 | 手动 [Real download acceptance](../.github/workflows/real-download.yml) | 在运行环境中实际下载、媒体时长探测、总览写入和再执行续传 | 所有平台/视频/账户永久可用 |
| 原生安装/升级 | 手动 [build workflow](../.github/workflows/build.yml) 的 `check_install=true` | 临时副本的安装替换、失败回滚与部分占用边界 | 用户机器所有安装环境、启动后健康回滚 |

普通 PR 和 main 自动执行 Windows/macOS 离线检查。第三方网络测试因站点风控、访问限制和素材变化，不应作为普通离线提交的唯一阻断证据。

## 本地命令

~~~bash
uv sync --locked --group build
uv run --no-sync python -m pytest tests/ -q
uv run --no-sync python -m pytest tests/test_batch_io.py -q
# 有 Qt 相关变更：建议在无图形显示环境设置 QT_QPA_PLATFORM=offscreen 后运行
QT_QPA_PLATFORM=offscreen uv run --no-sync python -m pytest tests/test_worker_signals.py -q
~~~

最后一条是 POSIX shell 示例；Windows PowerShell 可先执行 `$env:QT_QPA_PLATFORM="offscreen"`。

## 手动真实下载验收

仅使用有权限下载、长度短且可稳定访问的公开测试视频。不要提交包含账号凭据或私人 URL 的工作流输入。

在仓库的 **Actions → Real download acceptance (manual) → Run workflow** 输入 `video_url`；选择 Windows/macOS 两平台任务的实际结果。或本地运行：

~~~bash
uv run --no-sync python -m scripts.real_download_smoke --url "https://www.youtube.com/watch?v=VIDEO_ID"
~~~

该脚本首先通过 CLI 下载，再以 ffprobe 检查媒体时长与总览，第二次运行时核查输出文件修改时间未改变。它**不覆盖** GUI 全部行为、播放列表扩展、所有平台视频类型或 Windows 自更新。

验收记录应写明：来源视频的测试性质、操作系统、代码 SHA、Actions Run ID、通过/失败/跳过、外部限流情况。未执行就标记「未执行」，失败不得写成「通过」。

## 变更步骤

1. 阅读相关入口、调用链、测试、历史兼容要求；先提供可复现的失败证据。
2. 在独立分支中做最小改动；涉及行为变化补充回归测试，新增 CLI 参数必须更新文档。
3. 执行相关测试 → 全量离线测试 → GitHub 双平台 CI；依赖、打包或更新路径变更再做原生验证。
4. 在 PR 写清目标、变更范围、兼容影响、测试命令与结果、无法验收的部分。
5. CI 通过后审查 diff、敏感信息、文档与发布版本差异，再合并；**不因为文档修改而自动创建 Release**。

## 发布与依赖

`pyproject.toml` 为声明来源，`uv.lock` 负责锁定版本，`requirements.txt` 由锁文件生成。构建入口、签名限制、工具归档校验以及 Draft Release 见 [BUILD.md](../BUILD.md)。

任何从 main 新增的改动，只有重新按版本构建并发布正式资产后，才能写入「最新版安装包已包含」。