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

## 使用

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
