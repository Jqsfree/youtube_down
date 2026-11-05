# P0–P2 可靠性与验收记录

日期：2026-11-05。基线：正式发布 v1.4.0；main 已合并 [PR #5](https://github.com/Jqsfree/youtube_down/pull/5)、[PR #6](https://github.com/Jqsfree/youtube_down/pull/6)、[PR #7](https://github.com/Jqsfree/youtube_down/pull/7)，main SHA `37c16e87212a6d319898f9e981c778f4f139ab06`。这些代码**尚未进入 v1.4.0 已发布安装包**。

## 已核实的状态

| 事项 | 状态 | 证据与限制 |
| --- | --- | --- |
| Windows/macOS 离线回归 | 已通过 | [main Actions #41970320392](https://github.com/Jqsfree/youtube_down/actions/runs/41970320392)：macOS 359 passed；Windows 357 passed、2 skipped |
| 历史 CSV 保护与 CLI 风控结果 | 已合并；离线通过 | PR #6/#7；未声称覆盖每一种文件系统损坏 |
| Windows 更新重启/隐藏子进程窗口 | 已合并；离线通过 | PR #5；**2026-11-05 新修复的原生安装验证未执行** |
| 实际下载视频、校验与续传 | 验收入口已实现；未形成运行证据 | `.github/workflows/real-download.yml` 需手动提供公开短视频 URL 运行 |


## 架构决定（ADR）

保持 Python / PySide6 / yt-dlp 与现有 Qt-free `BatchRunnerCore`。不更换 GUI 框架、不引入 async 全量迁移、不重写下载协议。

理由：主要风险是文件结果一致性、异步任务终态以及真实下载/安装的验收缺口；重写框架不解决这些问题，反而改变 Cookie、打包和升级路径。后续若性能或重入瓶颈能用实测复现，再针对具体模块评估重构。

## P0：历史总览与发布安全

- PR #6：已有 `下载总览.csv` 损坏/乱码/列结构异常时停止写入；保留老版必要列兼容。
- GUI 在下载和历史查看时提示用户，不改动损坏的原文件；CLI 返回错误码。
- PR #5：Windows 更新重启/黑窗修复已合并 main；新修复仍需原生安装演练；CI 通过不等于 Windows 用户机器验收。
- 验收：双平台离线测试通过，Windows 安装替换、失败回滚、占用重试及真实更新启动验证。

## P1：任务终态与端到端下载

- CLI 在触发风控熔断后，已经运行的任务等待实际结果落盘，未启动的任务记录跳过，不允许未记录的丢失任务。
- 真实 `QThread` 信号路径独立测试；以 Mock 验证 CLI 并行熔断的确定性顺序。
- 新增手动 `Real download acceptance` 工作流。输入短、公开且允许测试的视频 URL；Windows/macOS 分别下载、校验媒体时长、核对总览、重跑确认续传。使用 `uv.lock`。
- 常规 PR CI 仍只运行离线测试。受 YouTube 风控/网络状态影响，手动联网测试失败不能简单归为代码错误。
- 验收证据必须有 Actions 运行号、两个平台的结论、日志中的下载与续传结果；未实际触发前不得声称完成真实下载验收。

## P2：执行策略与模块边界

- 在 Qt-free batch 核心定义不可变 `GUI_POLICY` / `CLI_POLICY`，显式描述 Cookie、格式与限流退避差异。
- 将 bot/429 判断抽为两端共用函数。保留 GUI 两阶段 Cookie + 分辨率档位与 CLI 显式 Cookie + best 格式；策略统一是显式管理差异，不是强制同一下载路径。
- 保持 `download_cli.py`、`worker.py`、`YoutubeDownloader` 现有对外入口、CSV 列和下载文件名不变。

## 限制与发布门槛

- 当前没有 Linux GUI 的正式发布资产，不应把源码可运行当成已发行。
- 真实下载场景中的平台限流、登录校验、内容不可用需要分类记录，不应通过扩大并发无条件重试。
- 更新安全校验 SHA256 解决损坏，不等于发行者签名。升级健康回滚仍为独立工作。
- main 发布前必须审查 PR 的完整 diff、依赖、CI；涉及 Windows 更新的功能还需原生安装验证。
