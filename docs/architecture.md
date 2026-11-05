# 当前架构与设计取舍

状态：`main` 源码现状，截至 2026-11-05。此文档描述实现边界，不是待实施重构提案。历史动机和五批迁移记录见 [architecture-plan.md](architecture-plan.md)。

## 结论

沿用 **Python + PySide6 + yt-dlp + Qt-free 批量执行核心**。当前复杂性主要位于任务状态、结果持久化、平台外部依赖和发布验收；单纯更换 UI 框架或全面迁移 async 不能直接解决这些风险。只有经过可复现的性能/可靠性基准证明瓶颈后才评估替换。

## 核心数据流

~~~text
GUI (main.py / gui.py / theme.py)    CLI (download_cli.py)
              |                              |
         worker.py                           |
       Qt QThread/Signals                    |
              |                              |
              +------------+-----------------+
                           |
                   batch_runner.py
              task scheduling / policies
              completion pool / live queue
                     /            \
           downloader.py        batch_io.py
               |                 overview CSV
       platforms/* / cookies.py
               |
             yt-dlp
               |
           ffmpeg/ffprobe
~~~

上图为逻辑调用关系，**不表示** CLI 通过 QThread：CLI 仅复用 Qt-free 的公共下载入口及线程池工具。

## 模块职责

| 模块 | 负责 | 不应负责 |
| --- | --- | --- |
| `gui.py` / `theme.py` | 输入与状态展示、设置与 UI | 直接调用 yt-dlp 下载协议 |
| `worker.py` | Qt 线程生命周期、信号适配 | 复制一份独立批量调度状态机 |
| `batch_runner.py` | Qt-free 队列、并发完成、熔断、GUI 批量策略及 CLI direct-item 公共入口 | 导入 PySide6 |
| `download_cli.py` | argparse、输入处理、CLI 策略、命令行报告、特定代理/节点处理 | GUI 逻辑 |
| `downloader.py` / `platforms/*` | 统一下载入口、格式与平台封装，委托 yt-dlp | 窗口组件 |
| `cookies.py` / `private_files.py` | Cookie 读取与临时副本、权限边界 | 静默上传账户数据 |
| `media_probe.py` / `download_errors.py` | 媒体与工具验证、错误类别 | UI 呈现 |
| `batch_io.py` | 输入发现、输出布局、总览 CSV 原子写入与读取 | 下载协议 |

`YoutubeDownloader` 与其原模块方法继续作为兼容接口。严格约束：`gui.py` 不导入 `yt_dlp`；纯逻辑不反向依赖 GUI/Qt。

## GUI 与 CLI 策略差异

| 维度 | GUI 批量 | CLI |
| --- | --- | --- |
| Cookie | 默认无 Cookie，特定错误触发二阶段重试；浏览器读取须授权 | 默认无浏览器读取，显式指定 Cookie 后按 direct-item 入口使用 |
| 格式选择 | 视频信息→格式档位/回退；支持最低清晰度 | `best` + 最低高度与相关校验 |
| 限流重试 | 批量处理实现了 4、8 秒退避 | direct-item 10 秒限流重试 |
| 并发 | Qt worker 适配 Qt-free 执行核心 | ThreadPoolExecutor/CompletionPool |
| 可观测 | Qt Signals、GUI 日志、下载总览 | 控制台、CLI 日志、下载总览 |

`GUI_POLICY`、`CLI_POLICY` 描述这些有意保留的差异；目前**并非**所有策略都采用一个完全同构的执行器。不要把「共享核心」写作「行为完全相同」。

## 结果持久化与错误边界

`下载总览.csv` 以 `(来源文件, video_id)` 为键，读写由 `OverviewStore` 负责。并发写入使用锁和临时文件替换；旧文件若存在但无法读取、列结构明显异常时拒绝继续覆盖。它是**结果快照**而非数据库或完整事件日志；不要推断能精准重建所有中断前未执行的瞬间。

成功记录与媒体文件有效性的判断仍需按调用场景核对；对于磁盘损坏或进程异常，不能假设任何 CSV 状态绝对可靠。

## 运行、测试与交付边界

- GUI UI 信号路径、Qt-free BatchRunner、CLI 分别有离线回归。
- 日常 CI 不做真正媒体下载；手动 E2E 才包括下载、ffprobe 和续传。
- Windows/macOS 构建沿用 PyInstaller；原生安装检查和源代码测试是两个不同门槛。
- GitHub Release v1.4.0 与 main 新修复之间存在版本差，不能混称已经交付。

完整验收标准见 [development.md](development.md)，历史 P0–P2 记录见 [p0-p2-acceptance.md](p0-p2-acceptance.md)。