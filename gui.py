"""主窗口 —— 纯 UI 层。

不直接调用 yt-dlp。所有操作通过 YoutubeDownloader + DownloadWorker 完成。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QListWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from downloader import YoutubeDownloader, check_environment
from logger_utils import AppLogger
from worker import BatchDownloadWorker, DownloadWorker, FetchInfoWorker, FormatAnalyzer


class MainWindow(QMainWindow):
    """YouTube Downloader 主窗口。"""

    def __init__(self) -> None:
        super().__init__()

        # ── 环境检测 ──
        all_ok, env_items = check_environment()
        errors = [i for i in env_items if i.status == "error"]
        if errors:
            lines = ["以下依赖缺失，程序可能无法正常工作：\n"]
            for i in errors:
                lines.append(f"  ✗ {i.name}: {i.message}")
            QMessageBox.critical(self, "环境检测", "\n".join(lines))

        # 控制台打印版本
        versions = "  ".join(
            f"{i.name}={i.version}" if i.version else i.name
            for i in env_items
        )
        print(f"[env] {versions}")
        # 日志在 _build_ui 之后添加，所以先存起来
        self._env_report = env_items

        self._downloader = YoutubeDownloader()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = Path.home() / "Downloads" / f"youtube_downloader_{ts}.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        AppLogger.attach_file(self._log_path)
        self._worker: DownloadWorker | BatchDownloadWorker | None = None
        self._video_id: str = ""
        self._info: dict[str, Any] | None = None
        self._output_dir: Path = Path.home() / "Downloads"
        self._csv_ids: list[str] = []
        self._csv_queue: list[tuple[str, list[str], Path | None]] = []
        self._batch_done: int = 0
        self._batch_errors: list[tuple[int, str]] = []
        self._last_fmt_id: str = ""
        self._last_min_height: int = 720
        self._batch_total: int = 0
        self._batch_video_ids: list[str] = []
        self._queue_results: list[tuple[int, int, str]] = []  # 队列累计结果  # 当前批次视频总数，避免用 _csv_ids 长度

        spec = self._downloader._cookies_spec  # noqa: SLF001
        title = "YouTube Downloader"
        if spec:
            title += f"  |  🍪 {spec}（已检测到）"
        self.setWindowTitle(title)
        self.resize(900, 720)
        self.setAcceptDrops(True)
        self._build_ui()
        self._connect_signals()

        AppLogger.attach_gui(self._append_log_line)
        AppLogger.get_logger().info("应用启动")

        # 环境日志
        for i in env_items:
            icon = "✗" if i.status == "error" else ("⚠" if i.status == "warning" else "✓")
            self._log(f"  {icon} {i.name} {i.version}")

    # ------------------------------------------------------------------
    # 拖拽导入
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:
        filepaths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith((".csv", ".tsv", ".txt"))
        ]
        if filepaths:
            self._process_imported_files(filepaths)

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------

    def _append_log_line(self, text: str) -> None:
        if not hasattr(self, "_log_view"):
            return
        self._log_view.appendPlainText(text)
        self._log_view.ensureCursorVisible()

    def _log(self, text: str) -> None:
        """追加一行带时间戳的日志。"""
        ts = datetime.now().strftime("%H:%M:%S")
        self._append_log_line(f"[{ts}] {text}")

    def _on_toggle_error_logs(self, checked: bool) -> None:
        AppLogger.set_gui_show_errors(checked)
        if checked:
            self._log("已显示错误日志")
        else:
            self._log("已隐藏错误日志")

    def _on_copy_log(self) -> None:
        text = self._log_view.toPlainText()
        if not text:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self._log("日志已复制到剪贴板")

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """构建完整 UI 布局。"""
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- Video ID 输入区 ----
        id_layout = QHBoxLayout()
        id_layout.addWidget(QLabel("Video ID:"))
        self._id_input = QLineEdit()
        self._id_input.setPlaceholderText("输入 YouTube Video ID，例如 dQw4w9WgXcQ")
        id_layout.addWidget(self._id_input)
        self._fetch_btn = QPushButton("获取信息")
        id_layout.addWidget(self._fetch_btn)
        self._refresh_btn = QPushButton("刷新页面")
        self._refresh_btn.setToolTip("重新获取当前输入的视频信息和格式")
        id_layout.addWidget(self._refresh_btn)
        root.addLayout(id_layout)

        # ---- CSV 批量导入 ----
        csv_layout = QHBoxLayout()
        self._load_csv_btn = QPushButton("Load CSV")
        self._load_csv_btn.setToolTip("支持 CSV/TSV/TXT，并可按自定义字段名导入")
        csv_layout.addWidget(self._load_csv_btn)
        csv_layout.addWidget(QLabel("字段:"))
        self._csv_column_input = QLineEdit()
        self._csv_column_input.setPlaceholderText("如 video_id / id / url")
        self._csv_column_input.setText("video_id")
        self._csv_column_input.setMaximumWidth(180)
        csv_layout.addWidget(self._csv_column_input)
        csv_layout.addWidget(QLabel("最小分辨率:"))
        self._min_height_input = QLineEdit()
        self._min_height_input.setPlaceholderText("如 720 / 1080")
        self._min_height_input.setText("720")
        self._min_height_input.setMaximumWidth(90)
        csv_layout.addWidget(self._min_height_input)
        self._csv_label = QLabel("")
        csv_layout.addWidget(self._csv_label)
        csv_layout.addStretch()
        root.addLayout(csv_layout)

        self._imported_files_group = QGroupBox("已导入文件")
        imported_files_layout = QVBoxLayout(self._imported_files_group)
        self._imported_files_list = QListWidget()
        self._imported_files_list.setMaximumHeight(140)
        imported_files_layout.addWidget(self._imported_files_list)
        root.addWidget(self._imported_files_group)

        # ---- 视频信息区 ----
        info_group = QGroupBox("视频信息")
        info_layout = QVBoxLayout(info_group)
        self._title_label = QLabel("Title: —")
        self._title_label.setWordWrap(True)
        info_layout.addWidget(self._title_label)
        self._uploader_label = QLabel("Uploader: —")
        info_layout.addWidget(self._uploader_label)
        self._duration_label = QLabel("Duration: —")
        info_layout.addWidget(self._duration_label)
        root.addWidget(info_group)

        # ---- 格式列表 ----
        fmt_group = QGroupBox("可用格式")
        fmt_layout = QVBoxLayout(fmt_group)
        self._format_tree = QTreeWidget()
        self._format_tree.setHeaderLabels([
            "Resolution", "Codec", "Container", "FPS", "Size", "Type", "Note",
        ])
        self._format_tree.setRootIsDecorated(False)
        self._format_tree.setSelectionMode(
            QTreeWidget.SelectionMode.SingleSelection
        )
        fmt_layout.addWidget(self._format_tree)
        root.addWidget(fmt_group)

        # ---- 输出目录 ----
        out_layout = QHBoxLayout()
        out_layout.addWidget(QLabel("Save To:"))
        self._output_input = QLineEdit()
        self._output_input.setText(str(self._output_dir))
        out_layout.addWidget(self._output_input)
        self._browse_btn = QPushButton("浏览...")
        out_layout.addWidget(self._browse_btn)
        root.addLayout(out_layout)

        # ---- 进度区 ----
        prog_group = QGroupBox("Progress")
        prog_layout = QVBoxLayout(prog_group)
        # 整体批量进度（仅批量模式可见）
        self._batch_progress_bar = QProgressBar()
        self._batch_progress_bar.setRange(0, 100)
        self._batch_progress_bar.setValue(0)
        self._batch_progress_bar.setVisible(False)
        prog_layout.addWidget(self._batch_progress_bar)
        self._batch_progress_label = QLabel("")
        self._batch_progress_label.setVisible(False)
        prog_layout.addWidget(self._batch_progress_label)
        # 当前视频进度
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        prog_layout.addWidget(self._progress_bar)
        self._progress_label = QLabel("0%")
        self._size_label = QLabel("— / —")
        self._speed_label = QLabel("—")
        self._eta_label = QLabel("ETA: —")
        stats_layout = QHBoxLayout()
        stats_layout.addWidget(self._progress_label)
        stats_layout.addWidget(self._size_label)
        stats_layout.addWidget(self._speed_label)
        stats_layout.addWidget(self._eta_label)
        stats_layout.addStretch()
        prog_layout.addLayout(stats_layout)
        self._status_label = QLabel("就绪")
        prog_layout.addWidget(self._status_label)
        root.addWidget(prog_group)

        # ---- 日志区（默认隐藏） ----
        self._log_group = QGroupBox("日志")
        self._log_group.setVisible(False)
        log_layout = QVBoxLayout(self._log_group)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._log_view.setPlaceholderText("下载日志将显示在此处...")
        self._log_view.setMinimumHeight(80)
        self._log_view.setPlaceholderText("下载日志将显示在此处...")
        log_layout.addWidget(self._log_view)
        root.addWidget(self._log_group)

        # ---- 操作按钮 ----
        btn_layout = QHBoxLayout()
        self._download_btn = QPushButton("下载")
        self._download_btn.setEnabled(False)
        btn_layout.addWidget(self._download_btn)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setEnabled(False)
        btn_layout.addWidget(self._cancel_btn)
        self._open_dir_btn = QPushButton("打开目录")
        btn_layout.addWidget(self._open_dir_btn)
        self._log_toggle_btn = QPushButton("日志")
        self._log_toggle_btn.setCheckable(True)
        btn_layout.addWidget(self._log_toggle_btn)
        self._show_errors_btn = QPushButton("显示错误")
        self._show_errors_btn.setCheckable(True)
        self._show_errors_btn.toggled.connect(self._on_toggle_error_logs)
        btn_layout.addWidget(self._show_errors_btn)
        self._copy_log_btn = QPushButton("复制日志")
        self._copy_log_btn.clicked.connect(self._on_copy_log)
        btn_layout.addWidget(self._copy_log_btn)
        btn_layout.addStretch()
        root.addLayout(btn_layout)

    def _connect_signals(self) -> None:
        """连接控件信号到处理槽。"""
        self._fetch_btn.clicked.connect(self._on_fetch)
        self._refresh_btn.clicked.connect(self._on_refresh)
        self._load_csv_btn.clicked.connect(self._on_load_csv)
        self._browse_btn.clicked.connect(self._on_browse)
        self._download_btn.clicked.connect(self._on_download)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._open_dir_btn.clicked.connect(self._on_open_dir)
        self._log_toggle_btn.toggled.connect(self._log_group.setVisible)

    # ------------------------------------------------------------------
    # 槽：加载 CSV
    # ------------------------------------------------------------------

    def _on_load_csv(self) -> None:
        """文件选择对话框加载 CSV。"""
        filepaths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择导入文件（可多选）",
            "",
            "支持文件 (*.csv *.tsv *.txt);;所有文件 (*)",
        )
        if filepaths:
            self._process_imported_files(filepaths)

    def _process_imported_files(self, filepaths: list[str]) -> None:
        """处理导入文件列表（文件对话框或拖拽共用）。"""
        import_column = self._csv_column_input.text().strip() or "video_id"
        all_ids: list[str] = []
        errors: list[str] = []
        self._csv_queue = []
        seen_names: set[str] = set()

        for fp in filepaths:
            path = Path(fp)
            try:
                rows = YoutubeDownloader.load_csv_rows(path, column=import_column)
            except FileNotFoundError as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            except ValueError as exc:
                errors.append(f"{path.name}: {exc}")
                continue

            if not rows:
                continue

            ids = [row["video_id"] for row in rows]
            all_ids.extend(ids)
            name = self._build_queue_name(path.stem, seen_names)
            seen_names.add(name)
            output_dir = self._resolve_import_output_dir(rows)
            self._csv_queue.append((name, list(dict.fromkeys(ids)), output_dir))

        if errors:
            QMessageBox.critical(self, "文件错误", "\n".join(errors))

        if not self._csv_queue:
            QMessageBox.warning(self, "提示", "CSV 中未找到有效的 video_id")
            return

        self._csv_ids = list(dict.fromkeys(all_ids))
        total = sum(len(ids) for _, ids, _ in self._csv_queue)
        parts = " → ".join(f"{name}({len(ids)})" for name, ids, _ in self._csv_queue)
        self._csv_label.setText(
            f"队列 {len(self._csv_queue)} 组 / 共 {total} 个 Video ID（依据列: {import_column}）"
        )
        self._refresh_imported_files_list(self._csv_queue)
        self._log(f"加载队列: {parts}")

        self._id_input.setText(self._csv_ids[0])
        self._download_btn.setEnabled(False)
        # 自动后台分析格式（不卡 GUI）
        self._fetch_btn.setEnabled(False)
        self._fetch_btn.setText("分析中...")
        self._status_label.setText("正在分析共有格式...")
        self._format_analyzer = FormatAnalyzer(
            downloader=self._downloader,
            video_ids=self._csv_ids,
            sample_size=5,
        )
        self._format_analyzer.progress.connect(self._status_label.setText)
        self._format_analyzer.finished.connect(self._on_formats_ready)
        self._format_analyzer.error.connect(self._on_formats_error)
        self._format_analyzer.start()

    def _refresh_imported_files_list(self, queue: list[tuple[str, list[str], Path | None]]) -> None:
        """刷新已导入文件列表，显示每个源文件名、视频数和目标目录。"""
        self._imported_files_list.clear()
        for name, ids, output_dir in queue:
            target = str(output_dir) if output_dir else "默认目录"
            self._imported_files_list.addItem(f"{name} — {len(ids)} 个视频 → {target}")
        if not queue:
            self._imported_files_list.addItem("暂无导入文件")

    def _on_formats_ready(self, first_info: dict, common_formats: list) -> None:
        """后台分析完成，填充格式列表。"""
        self._info = first_info
        self._show_info(first_info)
        self._show_common_formats(common_formats)
        self._download_btn.setEnabled(True)
        self._fetch_btn.setEnabled(True)
        self._fetch_btn.setText("获取信息")

    def _on_formats_error(self, msg: str) -> None:
        QMessageBox.critical(self, "错误", msg)
        self._fetch_btn.setEnabled(True)
        self._fetch_btn.setText("获取信息")
        self._status_label.setText("获取失败")

    def _show_common_formats(self, formats: list[dict[str, Any]]) -> None:
        """填充格式列表（仅共有格式）。"""
        self._format_tree.clear()
        for fmt in formats:
            item = QTreeWidgetItem([
                fmt["resolution"],
                fmt["codec"],
                fmt["container"],
                str(fmt["fps"]) if fmt["fps"] else "—",
                fmt["filesize_str"],
                fmt["type"],
                fmt.get("note", ""),
            ])
            self._format_tree.addTopLevelItem(item)
        for col in range(self._format_tree.columnCount()):
            self._format_tree.resizeColumnToContents(col)

    # ------------------------------------------------------------------
    # 槽：获取视频信息
    # ------------------------------------------------------------------

    def _on_refresh(self) -> None:
        """刷新当前输入的视频信息。"""
        if self._id_input.text().strip():
            self._on_fetch()
        else:
            QMessageBox.information(self, "提示", "先输入 Video ID 再刷新")

    def _on_fetch(self) -> None:
        """后台获取视频信息（不卡 GUI）。"""
        video_id = self._id_input.text().strip()
        if not video_id:
            QMessageBox.warning(self, "提示", "请输入 Video ID")
            return

        self._video_id = video_id
        self._download_btn.setEnabled(False)
        self._fetch_btn.setEnabled(False)
        self._fetch_btn.setText("获取中...")
        self._status_label.setText("正在获取视频信息...")

        self._fetch_worker = FetchInfoWorker(self._downloader, video_id)
        self._fetch_worker.finished.connect(self._on_fetch_ready)
        self._fetch_worker.error.connect(self._on_fetch_error)
        self._fetch_worker.start()

    def _on_fetch_ready(self, info: dict) -> None:
        self._info = info
        self._show_info(info)
        self._show_formats(self._video_id, info)
        self._download_btn.setEnabled(True)
        self._fetch_btn.setEnabled(True)
        self._fetch_btn.setText("获取信息")
        self._status_label.setText("就绪 — 请选择格式后下载")

    def _on_fetch_error(self, msg: str) -> None:
        QMessageBox.critical(self, "获取失败", msg)
        self._fetch_btn.setEnabled(True)
        self._fetch_btn.setText("获取信息")
        self._status_label.setText("获取失败")

    def _show_info(self, info: dict[str, Any]) -> None:
        """从 info dict 更新视频信息显示。"""
        title = info.get("title") or "—"
        uploader = info.get("uploader") or info.get("channel") or "—"
        duration = info.get("duration") or 0
        duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "—"

        self._title_label.setText(f"Title: {title}")
        self._uploader_label.setText(f"Uploader: {uploader}")
        self._duration_label.setText(f"Duration: {duration_str}")

    def _show_formats(self, video_id: str, info: dict[str, Any]) -> None:
        """填充格式列表 QTreeWidget。"""
        self._format_tree.clear()
        try:
            min_height = self._parse_min_height(self._min_height_input.text())
            formats = self._downloader.list_formats(video_id=video_id, info=info, min_height=max(720, min_height))
        except Exception:
            QMessageBox.warning(self, "错误", "获取格式列表失败")
            return

        for fmt in formats:
            item = QTreeWidgetItem([
                fmt["resolution"],
                fmt["codec"],
                fmt["container"],
                str(fmt["fps"]) if fmt["fps"] else "—",
                fmt["filesize_str"],
                fmt["type"],
                fmt.get("note", ""),
            ])
            self._format_tree.addTopLevelItem(item)

        # 自动调整列宽
        for col in range(self._format_tree.columnCount()):
            self._format_tree.resizeColumnToContents(col)

    # ------------------------------------------------------------------
    # 槽：浏览目录
    # ------------------------------------------------------------------

    def _on_browse(self) -> None:
        """浏览按钮：选择保存目录。"""
        directory = QFileDialog.getExistingDirectory(
            self, "选择保存目录", self._output_input.text()
        )
        if directory:
            self._output_dir = Path(directory)
            self._output_input.setText(directory)

    # ------------------------------------------------------------------
    # 槽：下载 / 取消
    # ------------------------------------------------------------------

    def _on_download(self) -> None:
        """下载按钮：启动下载（单视频或批量）。"""
        # 获取选中的格式
        selected = self._format_tree.selectedItems()
        if not selected:
            QMessageBox.warning(self, "提示", "请先选择一个格式")
            return

        row = self._format_tree.indexOfTopLevelItem(selected[0])
        try:
            min_height = self._parse_min_height(self._min_height_input.text())
            formats = self._downloader.list_formats(info=self._info, min_height=max(720, min_height))
            chosen = formats[row]
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"获取格式信息失败: {exc}")
            return

        min_height = self._parse_min_height(self._min_height_input.text())
        output_dir = Path(self._output_input.text())
        if not output_dir.exists():
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                QMessageBox.critical(self, "目录错误", f"无法创建输出目录: {exc}")
                return

        # 判断模式：CSV 队列 vs 单视频
        self._last_fmt_id = chosen["format_id"]
        self._last_min_height = min_height
        if self._csv_queue:
            self._start_queue(chosen["format_id"], output_dir, min_height)
        elif self._csv_ids:
            self._start_batch_download(chosen["format_id"], output_dir, self._csv_ids, min_height)
        else:
            self._start_single_download(chosen["format_id"], output_dir, min_height)

    def _start_single_download(self, format_id: str, output_dir: Path, min_height: int) -> None:
        """启动单个视频下载。"""
        self._worker = DownloadWorker(
            downloader=self._downloader,
            video_id=self._video_id,
            format_id=format_id,
            output_dir=output_dir,
            min_height=min_height,
        )
        self._worker.progress_changed.connect(self._on_progress)
        self._worker.status_changed.connect(self._on_status)
        self._worker.speed_changed.connect(self._on_speed)
        self._worker.eta_changed.connect(self._on_eta)
        self._worker.size_changed.connect(self._on_size)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self._set_downloading_ui(True)
        self._worker.start()

    def _start_queue(self, format_id: str, base_dir: Path, min_height: int) -> None:
        """启动队列下载：逐个 CSV 依次处理。"""
        if not self._csv_queue:
            return
        name, ids, explicit_dir = self._csv_queue.pop(0)
        output_dir = explicit_dir or self._build_queue_output_dir(base_dir, name)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_input.setText(str(output_dir))
        self._log(f"队列开始: {name} ({len(ids)} 个)")
        self._start_batch_download(format_id, output_dir, ids, min_height)

    def _start_batch_download(
        self,
        format_id: str,
        output_dir: Path,
        video_ids: list[str],
        min_height: int,
    ) -> None:
        """启动单组批量下载。"""
        self._batch_errors.clear()
        self._batch_done = 0
        self._batch_total = len(video_ids)
        self._batch_video_ids = list(video_ids)
        self._worker = BatchDownloadWorker(
            downloader=self._downloader,
            video_ids=video_ids,
            format_id=format_id,
            output_dir=output_dir,
            min_height=min_height,
        )
        w: BatchDownloadWorker = self._worker  # type narrowing
        w.all_progress_changed.connect(self._on_batch_progress)
        w.video_started.connect(self._on_batch_video_started)
        w.video_finished.connect(self._on_batch_video_finished)
        w.video_error.connect(self._on_batch_video_error)
        w.progress_changed.connect(self._on_progress)
        w.speed_changed.connect(self._on_speed)
        w.eta_changed.connect(self._on_eta)
        w.size_changed.connect(self._on_size)
        w.status_changed.connect(self._on_status)
        w.all_finished.connect(self._on_batch_all_finished)

        self._batch_progress_bar.setVisible(True)
        self._batch_progress_bar.setValue(0)
        self._batch_progress_label.setVisible(True)
        self._batch_progress_label.setText(f"0 / {self._batch_total}")

        self._set_downloading_ui(True)
        self._worker.start()

    @staticmethod
    def _parse_min_height(value: str) -> int:
        """解析最小分辨率阈值，非法时回退到 720。"""
        text = (value or "").strip().lower().replace("p", "")
        try:
            return max(0, int(text)) if text else 720
        except ValueError:
            return 720

    @staticmethod
    def _resolve_import_output_dir(rows: list[dict[str, str]]) -> Path | None:
        """从导入记录里解析 output_dir，若存在则优先使用。"""
        for row in rows:
            candidate = row.get("output_dir") or row.get("output_folder") or row.get("folder")
            if candidate:
                return Path(candidate)
        return None

    @staticmethod
    def _build_queue_name(stem: str, seen_names: set[str]) -> str:
        """生成队列名称，避免重复且保持可读。"""
        safe_name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5._ -]+", "_", stem).strip(" .")
        if not safe_name:
            safe_name = "source"
        candidate = safe_name
        suffix = 2
        while candidate in seen_names:
            candidate = f"{safe_name}_{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _build_queue_output_dir(base_dir: Path, queue_name: str) -> Path:
        """根据源文件名构造输出目录。"""
        return base_dir / queue_name

    def _on_cancel(self) -> None:
        """取消按钮：停止当前下载。"""
        if self._worker is not None and self._worker.isRunning():
            self._downloader.cancel()
            self._worker.wait(10000)
        self._set_downloading_ui(False)
        self._progress_bar.setValue(0)
        self._batch_progress_bar.setVisible(False)
        self._batch_progress_label.setVisible(False)
        self._status_label.setText("已取消")
        self._log("用户取消下载")

    def _set_downloading_ui(self, downloading: bool) -> None:
        """切换 UI 状态：闲置 / 下载中。"""
        self._fetch_btn.setEnabled(not downloading)
        self._load_csv_btn.setEnabled(not downloading)
        self._download_btn.setEnabled(not downloading)
        self._cancel_btn.setEnabled(downloading)
        self._id_input.setEnabled(not downloading)
        self._format_tree.setEnabled(not downloading)
        self._output_input.setEnabled(not downloading)
        self._browse_btn.setEnabled(not downloading)
        if not downloading:
            self._worker = None

    # ------------------------------------------------------------------
    # 槽：Worker 回调
    # ------------------------------------------------------------------

    def _on_progress(self, pct: int) -> None:
        self._progress_bar.setValue(pct)
        self._progress_label.setText(f"{pct}%")

    def _on_status(self, text: str) -> None:
        self._status_label.setText(text)

    def _on_speed(self, text: str) -> None:
        self._speed_label.setText(text)

    def _on_eta(self, text: str) -> None:
        self._eta_label.setText(f"ETA: {text}")

    def _on_size(self, text: str) -> None:
        self._size_label.setText(text)

    def _on_finished(self, path: str) -> None:
        self._set_downloading_ui(False)
        self._progress_bar.setValue(100)
        self._progress_label.setText("100%")
        self._status_label.setText(f"下载完成 — {path}")
        AppLogger.get_logger().info("下载完成: %s", path)
        QMessageBox.information(self, "下载完成", f"文件已保存至:\n{path}")

    def _on_error(self, msg: str) -> None:
        self._set_downloading_ui(False)
        AppLogger.log_exception(Exception(msg), "下载失败")
        QMessageBox.critical(self, "下载失败", msg)
        self._status_label.setText("下载失败")

    # ------------------------------------------------------------------
    # 槽：批量 Worker 回调
    # ------------------------------------------------------------------

    def _on_batch_progress(self, pct: int) -> None:
        self._batch_progress_bar.setValue(pct)
        self._batch_progress_label.setText(
            f"总进度 {pct}% — {self._batch_done} / {self._batch_total}"
        )

    def _on_batch_video_started(
        self, index: int, total: int, video_id: str, cookie: bool
    ) -> None:
        self._progress_bar.setValue(0)
        self._progress_label.setText("0%")
        self._speed_label.setText("—")
        self._eta_label.setText("ETA: —")
        self._size_label.setText("— / —")
        tag = " 🍪" if cookie else ""
        self._status_label.setText(f"[{index + 1}/{total}] 下载: {video_id}{tag}")
        self._log(f"[{index + 1}/{total}] 开始: {video_id}{tag}")

    def _on_batch_video_finished(
        self, index: int, path: str, cookie_used: bool
    ) -> None:
        self._batch_done = index + 1
        self._batch_progress_label.setText(
            f"总进度 — {self._batch_done} / {self._batch_total}"
        )
        tag = " [🍪]" if cookie_used else ""
        self._log(f"[{index + 1}/{self._batch_total}] 完成{tag}: {path}")

    def _on_batch_video_error(
        self, index: int, msg: str, cookie_used: bool
    ) -> None:
        vid = self._batch_video_ids[index] if index < len(self._batch_video_ids) else "?"
        # 覆盖同 index 的旧记录（Stage 1 → Stage 2 重试时只保留最终错误）
        self._batch_errors = [
            (i, v, m) for i, v, m in self._batch_errors if i != index
        ]
        self._batch_errors.append((index, vid, msg))
        tag = " [🍪]" if cookie_used else ""
        self._batch_progress_label.setText(
            f"总进度 — {self._batch_done + 1} / {self._batch_total}  ⚠{tag}"
        )
        self._status_label.setText(
            f"[{index + 1}/{self._batch_total}] 失败: {vid}"
        )
        self._log(f"[{index + 1}/{self._batch_total}] 失败: {vid} — {msg[:120]}")

    def _on_batch_all_finished(
        self, success: int, fail: int, csv_path: str
    ) -> None:
        self._batch_progress_bar.setValue(100)
        self._batch_progress_label.setText(f"完成 — 成功 {success}，失败 {fail}")
        self._status_label.setText(f"批量下载完成：成功 {success}，失败 {fail}")

        self._batch_errors.clear()

        # 队列模式：累积结果，最后一个弹窗
        if self._csv_queue:
            self._queue_results.append((success, fail, csv_path))
            fmt_id = self._worker._format_id if self._worker else self._last_fmt_id  # noqa: SLF001
            min_height = self._last_min_height
            self._start_queue(fmt_id, self._output_dir, min_height)
            return

        # 队列全部完成（或单批次）→ 汇总弹窗
        if self._queue_results:
            self._queue_results.append((success, fail, csv_path))
            total_ok = sum(s for s, _, _ in self._queue_results)
            total_fail = sum(f for _, f, _ in self._queue_results)
            csv_list = "\n".join(f"  {p}" for _, _, p in self._queue_results)
            msg = (
                f"队列全部完成\n\n"
                f"总数: {total_ok + total_fail}\n"
                f"成功: {total_ok}\n"
                f"失败: {total_fail}\n\n"
                f"结果 CSV:\n{csv_list}"
            )
            QMessageBox.information(self, "队列下载完成", msg)
            self._log(f"队列全部完成: 成功 {total_ok}, 失败 {total_fail}")
            self._queue_results.clear()
        else:
            total = success + fail
            extra = []
            if self._worker is not None:
                skipped_path = getattr(self._worker, "_last_skipped_csv", "")
                failed_path = getattr(self._worker, "_last_failed_csv", "")
                if skipped_path:
                    extra.append(f"跳过列表 CSV:\n{skipped_path}")
                if failed_path:
                    extra.append(f"失败重试 CSV:\n{failed_path}")
            msg = (
                f"批量下载完成\n\n"
                f"总数: {total}\n"
                f"成功: {success}\n"
                f"失败: {fail}\n\n"
                f"详细结果 CSV:\n{csv_path}"
            )
            if extra:
                msg = f"{msg}\n\n" + "\n\n".join(extra)
            QMessageBox.information(self, "批量下载完成", msg)
            self._log(f"批量完成: 成功 {success}, 失败 {fail}, CSV: {csv_path}")
            AppLogger.get_logger().info("批量完成: 成功 %s, 失败 %s, CSV: %s", success, fail, csv_path)

        self._set_downloading_ui(False)

    # ------------------------------------------------------------------
    # 槽：打开目录
    # ------------------------------------------------------------------

    def _on_open_dir(self) -> None:
        """打开目录按钮：用系统文件管理器打开输出目录。"""
        output_dir = self._output_input.text()
        path = Path(output_dir)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
