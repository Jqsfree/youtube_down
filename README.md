# YouTube Downloader

基于 yt-dlp + PySide6 的 YouTube 视频下载桌面工具。

## 系统要求

- Python 3.11+
- ffmpeg（放入 PATH 或 `resources/ffmpeg/` 目录）

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## Windows 本地测试

在项目根目录打开 **PowerShell** 或 **命令提示符**，执行：

```powershell
# 一键测试：安装依赖 + 单元测试 + 冒烟测试 + GUI 初始化
powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1

# 测试通过后直接启动 GUI
powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1 -LaunchGui

# 离线环境（跳过需要网络的 get_info 测试）
powershell -ExecutionPolicy Bypass -File scripts\win_test.ps1 -SkipNetwork
```

也可以双击 `scripts\win_test.bat`。

> 若 PowerShell 报中文乱码或解析错误，请确保使用最新版 `scripts/win_test.ps1`（脚本输出为英文，避免 Windows 编码问题）。

### 前置条件

| 组件 | 说明 |
|------|------|
| Python 3.11+ | [python.org](https://www.python.org/downloads/) 安装时勾选 "Add to PATH" |
| ffmpeg | 加入 PATH，或解压到 `resources\ffmpeg\` |
| 网络 | 冒烟测试会请求 YouTube 获取视频信息 |

可选（Cookie 重试模式）：

- **deno**：Cookie 模式下 YouTube JS 挑战需要
- **Chrome/Edge**：用于读取浏览器 Cookie

### 手动分步测试

```powershell
python -m pip install -r requirements.txt pytest
python -m pytest tests/ -v
python smoke_test.py
python main.py
```


1. 输入 YouTube Video ID（例如 `dQw4w9WgXcQ`）
2. 点击 **获取信息** 查看视频详情和可用格式
3. 选择目标格式
4. 选择保存目录（默认为 Downloads）
5. 点击 **下载**
6. 下载完成后点击 **打开目录** 查看文件

## 项目结构

```
youtube_downloader/
├── main.py          # 程序入口
├── gui.py           # 主窗口（纯 UI）
├── downloader.py    # yt-dlp 封装
├── worker.py        # 下载线程
├── requirements.txt
└── README.md
```

## 解耦设计

- `gui.py` 不直接调用 yt-dlp
- 所有下载逻辑封装在 `downloader.py` 的 `YoutubeDownloader` 类中
- `worker.py` 负责后台线程，通过 Qt Signals 更新 GUI
