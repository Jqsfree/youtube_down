# v1.5.2 发布候选验收

状态：**待验证，不得公开发布**（2026-11-05）。

## 变更背景

- v1.5.0 的 Windows/macOS 安装构建与原生升级/回滚演练通过，真实下载 E2E 未通过：Windows CP1252 中文输出报错，macOS YouTube 风控。见 [构建](https://github.com/Jqsfree/youtube_down/actions/runs/41981645668)、[E2E](https://github.com/Jqsfree/youtube_down/actions/runs/41982042086)。
- v1.5.1 修复 CLI 自身输出和日志生命周期，双平台构建与原生升级/回滚通过；[重跑的 E2E](https://github.com/Jqsfree/youtube_down/actions/runs/41983592257) 又发现 `pacing.py` 自行向 Windows CP1252 stdout 输出中文，绕过了 CLI 的 `_print`。另一平台仍遭遇 YouTube 机房 IP 风控。
- 不覆写 v1.5.0/v1.5.1 的标签、Draft 或资产。v1.5.2 仅修复这个独立的编码路径。

## 修复和边界

- CLI `main()` 在初始化时对可配置的标准输出/错误流设置 `errors=backslashreplace`，保留原有编码与日志脱敏，覆盖 `pacing.py` 等共用模块的直接 `print`。
- 添加 CP1252 下限流退避 stdout、stderr 及 pytest 捕获流测试；保留 v1.5.1 的 CLI 文件日志句柄释放与 JSON 输出兼容性。
- 没有改动 Cookie 权限、下载策略、输出 CSV、并发默认值和底层 yt-dlp。
- 网络风控是外部验收限制，不通过增加重试绕过它，也不能将其描述成业务验收通过。

## 发布门槛（不得以规划代替证据）

| 步骤 | 当前状态 | 需要的证据 |
| --- | --- | --- |
| VERSION / pyproject / uv.lock = 1.5.2 | 已在候选分支提交，待 CI | `scripts/release_checks.py version`、`uv sync --locked` |
| Windows/macOS 离线测试 | 待 CI | 相同 SHA 的双平台 Actions 检查 |
| v1.5.2 tag（不可覆写）与草稿 | 未执行 | tag SHA 与构建工作流 |
| Windows/macOS 实体安装包、内嵌工具、升级与回滚 | 未执行 | 两平台构建/原生检查通过 |
| 网络真实下载、媒体校验与续传 | 未通过 | 非机房环境的完整视频和验证日志 |
| 正式 Release | **禁止在上项通过之前发布** | 安装包 + 业务验收 + 人工审核 |

[构建与发布](../BUILD.md) · [开发与验收](development.md) · [v1.5.0 记录](release-v1.5.0.md) · [v1.5.1 记录](release-v1.5.1.md)。
