# v1.5.0 发布放行清单

状态：**已生成 Draft，但真实下载验收未通过；禁止公开发布**。2026-11-05，v1.5.0 标签为 `26c6e7a4dfeccab7599dc3ac0892c63d232ad24c`；后续修复转入 v1.5.1，不覆盖原有 tag/资产。

## 范围与版本一致性

- 基线：v1.4.0 正式 Release；v1.5.0 收录 PR #5–#9 在 main 的改进。
- 版本来源：`VERSION`、`pyproject.toml`、`uv.lock` 的项目版本均为 1.5.0；`CHANGELOG.md` 存在 `## [1.5.0]`。
- 依赖及工具版本保持锁定，未因本次改版升级外部 yt-dlp、PyInstaller 或 ffmpeg。
- 只有新的 `v1.5.0` tag 指向完成审核的提交后，tag 构建工作流才会生成双平台资产与 **Draft Release**；草稿不应被描述为已经发布。

## 已执行证据（2026-11-05）

- [主分支 CI](https://github.com/Jqsfree/youtube_down/actions/runs/41981395497)：Windows/macOS 离线测试通过。
- [v1.5.0 构建工作流](https://github.com/Jqsfree/youtube_down/actions/runs/41981645668)：Windows/macOS 包、工具验证、原生安装替换/回滚通过；创建 5 个资产的 Draft Release。
- [实际 YouTube E2E](https://github.com/Jqsfree/youtube_down/actions/runs/41982042086)：**Windows 未通过**，非 UTF-8 控制台中文输出触发 UnicodeEncodeError，并导致临时日志句柄占用；**macOS 未通过**，公网 runner 遭遇 bot/限流，未成功写入媒体。两项均不得标记通过。
- 该 Draft 不作为最新正式安装包，正式发布仍为 v1.4.0。按项目策略，修复转入 v1.5.1。

## 放行检查

| 检查 | 触发条件 | 当前结果 |
| --- | --- | --- |
| 版本一致性与依赖锁定 | PR / main CI | 待 PR CI 结果 |
| Windows、macOS 离线回归 | PR / main CI | 待 PR CI 结果 |
| 打包产物与哈希、build-info | `v1.5.0` tag 构建 | 未执行 |
| 升级替换、失败回滚、Windows 占用重试 | tag 构建自动运行 `scripts/check_install.py` | 未执行 |
| YouTube 实际下载、ffprobe、重跑续传 | `Real download acceptance` 手动工作流 | 未执行 |
| Release 草稿资产对齐、人工审查并发布 | tag 构建完成后 | 未执行 |

## 严格发布顺序

1. 合并版本 PR，确认 main CI 通过，检查没有同时发生的其他修改。
2. 在主分支提交上创建唯一 tag `v1.5.0`（**不可覆盖已有 tag**）。
3. tag 自动触发 `.github/workflows/build.yml`，执行 Windows/macOS 测试、原生构建、内嵌工具校验和旧包升级/回滚，最终只能生成 Draft Release。
4. 下载并检查 Draft 的 `YouTubeDownloader-Windows.exe`、`YouTubeDownloader-macOS.zip`、`SHA256SUMS.txt`、两份 `build-info`；核对 SHA256 和源提交。
5. 在 Windows/macOS 验证新安装包的真实启动、下载与更新，保存 Actions/实际操作证据。手动 Real download workflow 须提供有权测试的公开视频 URL，不存储 Cookie。
6. 检查更新机制能找到 v1.5.0，用户体验和已知限制准确。全部通过后才从 Draft 发布正式版。

## 操作和异常边界

- tag 构建不会覆盖已有正式 Release 或草稿；失败后不改写原 tag/资产。若必须重新发布，按仓库规则增加补丁版本并重新验收。
- **CI 通过 != 真实下载成功**；联网 `get_info` 冒烟 != 实际视频文件写入成功。
- Windows/macOS 包不含 Developer ID 公证保证；哈希证明下载完整性，不证明发布者身份。
- 没有完成上述验收时保持 Draft，不发布为 latest，不修改 README 的已发布下载链接。
- 本文的「未执行」状态只能在取得可核查 run ID 或安装结果后更新，禁止凭推断改成「通过」。

参考：[构建说明](../BUILD.md)、[开发验收](development.md)、[历史审查](p0-p2-acceptance.md)。
