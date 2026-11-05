# v1.5.1 修复候选与发布验收

状态：**双平台构建与原生升级通过，Draft 已创建；真实 E2E 未通过，禁止正式发布**。2026-11-05，v1.5.0 与 v1.5.1 均保留原始标签及草稿，后续修复移至 v1.5.2。

## 为什么需要新的补丁版本

[v1.5.0 E2E 运行 #41982042086](https://github.com/Jqsfree/youtube_down/actions/runs/41982042086) 检出两个不同的失败：

- Windows：本机控制台编码 CP1252 无法表示中文 CLI 输出，直接抛 `UnicodeEncodeError`；临时日志文件仍打开，导致 `WinError 32` 清理失败。这是**可复现的程序缺陷**，已修复于 v1.5.1 候选。
- macOS：公开视频测试请求被 YouTube 触发 bot/rate 限制，未能下载验证；**属于受外部服务影响的验收阻塞**。不能据此推断所有用户环境都失败，也不能称为下载通过。

## 本候选变更

- CLI 所有常规中文消息按当前 stdout/stderr 编码进行安全回退，不影响原有凭据脱敏。
- `--dry-run` JSON 使用 ASCII-safe 序列化，确保在旧 Windows 控制台可解析。
- CLI 结束/异常时及时关闭日志文件句柄，防止 Windows 临时目录无法删除。
- 补充离线测试覆盖 cp1252、stdout JSON、正常/异常后的文件句柄释放。
- `VERSION`、`pyproject.toml`、`uv.lock` 锁文件根包版本同步为 1.5.1；不升级依赖。

## 已执行证据

- [v1.5.1 主分支 CI #41983008970](https://github.com/Jqsfree/youtube_down/actions/runs/41983008970)：Windows/macOS 全量离线测试通过。
- [v1.5.1 构建 #41983200216](https://github.com/Jqsfree/youtube_down/actions/runs/41983200216)：Windows/macOS 内嵌工具检查、真实安装升级/回滚通过，创建两个安装包及三个验证附件的 Draft。
- [v1.5.1 真实 E2E #41983592257](https://github.com/Jqsfree/youtube_down/actions/runs/41983592257)：Windows 已不再因 CLI 自身的 `_print` 或临时日志占用失败，但 `pacing.py` 的直接 `print` 又触发 CP1252 UnicodeEncodeError；macOS 仍受站点 bot/限流影响。两平台均未取得下载和续传成功证据。
- 因版本号对应的二进制不可覆盖，修复改为 v1.5.2 新候选。该文档保留历史证据。

## 发布门槛

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| v1.5.1 版本一致、依赖锁定 | 未执行 | 等待 PR CI |
| Windows/macOS 全量离线测试 | 未执行 | 等待 PR CI |
| v1.5.1 tag、构建和内嵌工具完整性 | 未执行 | 等待 Actions |
| Windows/macOS 原生安装、失败回滚与重启 | 未执行 | 等待 Actions |
| 真实下载/媒体校验/续传 | 未通过 | GitHub runner 受平台风控，需独立可信环境重新验证 |
| 正式公开发布 v1.5.1 | 未执行 | 仅在各项必要验收充分时才允许把 Draft 发布为 latest |

[构建步骤](../BUILD.md) · [发布历史](release-v1.5.0.md) · [开发验收](development.md)。

**禁止事项：** 不覆盖 v1.5.0 tag/Draft，不假定 Actions 机器可以通过 YouTube 风控，不仅凭离线 CI 把 v1.5.1 标记为正式可用。
