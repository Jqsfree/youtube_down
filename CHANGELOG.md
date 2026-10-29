# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.3.1] - 2026-10-29

- **修复更新检查的报错难以理解**：未认证访问 `api.github.com` 是 60 次/小时/IP，
  公司 NAT 或反复点「检查更新」会撞上 403，旧实现直接弹
  `HTTP Error 403: rate limit exceeded`。现在区分限流 / 404 / 其他 HTTP /
  连接失败 / 超时，给出「稍后再试」或「去 Releases 手动下载」的提示。

## [1.3.0] - 2026-10-29

应用内更新 + 打包图标 + 活动下载队列。**里程碑：从这一版起可以在软件里一键升级。**

> 1.2.0 及更早版本没有更新入口，需手动下载一次 1.3.0。

### 新增

- **应用内更新**（`app_updater.py` + 设置页「检查更新」）
  - 读取 GitHub Releases latest 与本地 `VERSION` 比较，跳过预发布版本。
  - 流式下载对应平台资产，按 API 上报的字节数校验完整性。
  - 若 release 附带 `SHA256SUMS.txt`，强制校验 sha256；不符即删包并报错。
  - Windows 写 `.bat`（`ping` 延迟 + 30 次重命名重试），macOS 写 `.sh`
    （`ditto -x -k` 解压 + `xattr -dr` 去隔离），分离进程执行后退出重启。
  - 替换失败会回滚：把 `.old` 搬回原位并重新拉起，避免旧版新版全丢。
  - 启动后 24 小时内静默检查一次（`QSettings` 键 `update/last_check`）。
  - 源码运行时不自我替换，改为打开 Releases 页面。

- **打包与窗口图标**
  - `assets/icon.svg` 为唯一源文件，`scripts/make_icon.py` 生成
    `icon.png`(1024) / `icon.ico`(7 尺寸，纯 Python PNG-in-ICO) / `icon.icns`(iconutil)。
  - `theme.asset_path()` 兼容 `sys._MEIPASS`，`theme.app_icon()` 在 `main.py` 与主窗口生效。

- **活动下载队列（持续下载）**
  - `BatchDownloadWorker` 从固定列表改为活动队列：`enable_live_queue()` 后
    `run()` 排空会等待新任务（`enqueue()`），空闲 3 秒才退出。
  - 运行中「加入队列」/「粘贴」保持可用，新 ID 直接进当前批次；
    worker 已收尾时返回 `False`，GUI 回退到下一组 `_csv_queue`。
  - `total_changed(int)` 上报新分母，进度条随追加增长而不是跳变。

### 修复

- **进度条到不了 100%**：旧实现分母用「本轮待下载条数」、分子只数实跑条数，
  续传跳过会让进度停在 N-M/N。改为「已完成（含续传跳过）/ 期望总数」，
  并让当前视频的内部进度参与折算。
- **安装更新时线程未停**：`QApplication.quit()` 不触发 `closeEvent`，运行中的
  `QThread` 被析构会 abort，Windows 上还会因 exe 被占用导致脚本替换失败。
  新增 `_stop_background_threads()`，退出与安装更新共用。
- **`QThread.quit()` 对无事件循环的 `run()` 无效**：退出改为
  `close_queue()` + `downloader.cancel()` 让 `run()` 自行返回。
- **更新脚本回滚缺失**：Windows `copy` 失败时 `TARGET` 已被 `move` 走，
  旧逻辑会 `start` 一个不存在的路径；macOS 覆盖失败同样两头落空。两者均补回滚。
- **重定向落点未校验**：`urllib` 会自动跟随 302，现在对 `geturl()` 也做白名单
  校验，并放行 `.githubusercontent.com` 后缀（真实落点是
  `release-assets.githubusercontent.com`）。
- **预发布判定误伤**：`is_prerelease()` 旧实现用子串匹配，`1.2.0` 这类正常版本
  也可能命中；改为只看 `-` / `+` 之后的后缀段。
- **安装前权限检查**：目标目录不可写时提前提示，不让脚本静默失败。
- **取消后行状态卡住**：取消时把仍在「排队中/等待中」的行复位为「已取消」。
- **`_cookie_overrides` 跨线程遍历**：运行中追加改为整体替换而非原地 `update()`。
- **更新包缓存清理**：启动时清理超过 3 天的 `ytdown-update-*` 临时目录。

### 工程

- CI 为每个产物生成 `SHA256SUMS.txt`，在 release job 合并后随 Release 发布。
- macOS 构建从 `--onefile --windowed` 改为 `--onedir --windowed`：
  该组合已被 PyInstaller 6.22 标记弃用（v7 报错），且启动要解包上百 MB；
  实测 zip 体积 113 MB vs 116 MB，基本一致。同时补 `--osx-bundle-identifier`。
- 新增测试：`test_app_updater.py`、`test_progress_accounting.py`、
  `test_gui_live_queue.py`、`test_live_queue.py`、`test_icon_assets.py`。
- `CLAUDE.md` 收敛为指向 `AGENTS.md`，不再重复维护两份 agent 指南。

### 已知限制

- macOS 包未做代码签名 / 公证，手动下载首次打开需「右键 → 打开」；
  应用内更新不受影响（urllib 下载不带 quarantine 标记）。
- Windows 的 `.bat` 替换流程无法在 macOS 上实机验证，仅有脚本内容单测覆盖。

## [1.2.0] - 2026-10-24

- 批量功能完善：文件夹/CSV 导入、输出镜像、`下载总览.csv`、失败重试。
- 通用直链支持、代理预检、文件名净化。
- Windows / macOS 单文件打包与 GitHub Release 自动化。

## [1.1.6] - 2026-10-20

- 取消/续传/Cookie/清晰度校验加固。

[1.3.1]: https://github.com/Jqsfree/youtube_down/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/Jqsfree/youtube_down/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/Jqsfree/youtube_down/compare/v1.1.6...v1.2.0
[1.1.6]: https://github.com/Jqsfree/youtube_down/releases/tag/v1.1.6
