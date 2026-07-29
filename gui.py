"""主窗口 —— 多平台下载 UI（YouTube / Bilibili / TikTok）。

不直接调用 yt-dlp。所有操作通过 DownloaderFacade(YoutubeDownloader) + BatchDownloadWorker 完成。
布局：左栏平台 Cookie，右栏队列与进度。主题由 theme.py 提供。
"""

from __future__ import annotations

import os
import platform as py_platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from downloader import YoutubeDownloader, check_environment
from logger_utils import AppLogger
from platforms.base import Platform
from theme import PRESETS, apply_theme, load_palette, preset_names, save_palette, with_accent
from worker import AUTO_FORMAT_ID, BatchDownloadWorker, ValidateCookieWorker

_PLATFORM_KEYS = (Platform.YOUTUBE, Platform.BILIBILI, Platform.TIKTOK)


class MainWindow(QMainWindow):
    """多平台下载器主窗口。"""

    _QUALITY_PRESETS: tuple[tuple[str, int | None], ...] = (
        ("720p", 720),
        ("1080p", 1080),
        ("1440p", 1440),
        ("2160p (4K)", 2160),
        ("自定义", None),
    )

    def __init__(self) -> None:
        super().__init__()

        self._downloader = YoutubeDownloader()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = Path.home() / "Downloads" / f"youtube_downloader_{ts}.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        AppLogger.attach_file(self._log_path)
        self._worker: BatchDownloadWorker | None = None
        self._validate_cookie_worker: ValidateCookieWorker | None = None
        self._output_dir: Path = Path.home() / "Downloads"
        self._csv_ids: list[str] = []
        self._csv_queue: list[tuple[str, list[str], Path | None]] = []
        self._csv_cookie_overrides: dict[str, dict[str, str]] = {}
        self._cookie_persisted: bool = bool(self._downloader._platform_cookiefiles)  # noqa: SLF001
        self._batch_done: int = 0
        self._batch_errors: list[tuple[int, str, str]] = []
        self._last_fmt_id: str = ""
        self._last_min_height: int = 720
        self._last_strict_quality: bool = True
        self._batch_total: int = 0
        self._batch_video_ids: list[str] = []
        self._current_video_id: str = ""
        self._queue_results: list[tuple[int, int, int, str]] = []
        self._selected_platform = Platform.YOUTUBE
        self._palette = load_palette()

        ver = Path(__file__).parent / "VERSION"
        self._app_version = ver.read_text().strip() if ver.exists() else "dev"
        self._base_title = f"Multi-Platform Downloader v{self._app_version}"
        self.setWindowTitle(self._base_title)
        self.resize(980, 640)
        self.setMinimumSize(720, 480)
        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()
        self._refresh_cookie_ui()
        AppLogger.attach_gui(self._append_log_line)

        all_ok, env_items = check_environment()
        errors = [i for i in env_items if i.status == "error"]
        warnings = [i for i in env_items if i.status == "warning"]
        if errors or warnings:
            self._show_env_wizard(errors, warnings)

        versions = "  ".join(
            f"{i.name}={i.version}" if i.version else i.name for i in env_items
        )
        print(f"[env] {versions}")
        AppLogger.get_logger().info("应用启动")
        for i in env_items:
            icon = "✗" if i.status == "error" else ("⚠" if i.status == "warning" else "✓")
            self._log(f"  {icon} {i.name} {i.version}")
        self.statusBar().showMessage(
            f"{py_platform.system()} · 主题 {self._palette.name} · 请导入 CSV 或粘贴链接"
        )

    def closeEvent(self, event: Any) -> None:
        if self._validate_cookie_worker is not None and self._validate_cookie_worker.isRunning():
            self._validate_cookie_worker.wait(3000)
        if self._worker is not None and self._worker.isRunning():
            self._downloader.cancel()
            self._worker.quit()
            self._worker.wait(3000)
        AppLogger.get_logger().info("应用退出")
        event.accept()

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:
        filepaths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith((".csv", ".tsv", ".txt"))
        ]
        if not filepaths:
            return
        if len(filepaths) == 1 and YoutubeDownloader.is_netscape_cookie_file(filepaths[0]):
            self._apply_cookie_file(filepaths[0])
            return
        self._process_imported_files(filepaths)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_main())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 740])
        outer.addWidget(splitter)
        self.setStatusBar(QStatusBar())

    def _build_sidebar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setMinimumWidth(200)
        frame.setMaximumWidth(320)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("平台 / Cookie")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        hint = QLabel("选择平台后导入 Netscape cookies.txt")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._platform_buttons: dict[str, QPushButton] = {}
        self._cookie_labels: dict[str, QLabel] = {}

        for plat in _PLATFORM_KEYS:
            card = QFrame()
            card.setObjectName("platformCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 8, 8, 8)
            card_layout.setSpacing(4)

            btn = QPushButton(plat.label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.clicked.connect(lambda checked=False, p=plat: self._on_select_platform(p))
            self._platform_buttons[plat.value] = btn
            card_layout.addWidget(btn)

            status = QLabel()
            status.setObjectName("cookieMissing")
            self._cookie_labels[plat.value] = status
            card_layout.addWidget(status)
            layout.addWidget(card)

        self._platform_buttons[Platform.YOUTUBE.value].setChecked(True)

        remember_row = QHBoxLayout()
        self._remember_cookie_checkbox = QCheckBox("记住为默认")
        self._remember_cookie_checkbox.setToolTip("勾选后保存为该平台默认 Cookie")
        remember_row.addWidget(self._remember_cookie_checkbox)
        layout.addLayout(remember_row)

        btn_row = QHBoxLayout()
        self._import_cookie_btn = QPushButton("导入 Cookie")
        self._clear_cookie_btn = QPushButton("清除")
        btn_row.addWidget(self._import_cookie_btn)
        btn_row.addWidget(self._clear_cookie_btn)
        layout.addLayout(btn_row)

        theme_label = QLabel("主题")
        theme_label.setObjectName("sectionLabel")
        layout.addWidget(theme_label)

        theme_row = QHBoxLayout()
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(preset_names())
        idx = self._theme_combo.findText(self._palette.name)
        self._theme_combo.setCurrentIndex(max(0, idx))
        self._accent_btn = QPushButton("强调色")
        theme_row.addWidget(self._theme_combo, stretch=1)
        theme_row.addWidget(self._accent_btn)
        layout.addLayout(theme_row)

        layout.addStretch(1)
        ver = QLabel(f"v{self._app_version}")
        ver.setObjectName("mutedLabel")
        layout.addWidget(ver)
        return frame

    def _build_main(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QLabel("任务队列")
        header.setObjectName("titleLabel")
        layout.addWidget(header)

        url_row = QHBoxLayout()
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("粘贴 YouTube / Bilibili / TikTok 链接或 ID…")
        self._add_url_btn = QPushButton("加入队列")
        self._load_csv_btn = QPushButton("加载 CSV")
        url_row.addWidget(self._url_edit, stretch=1)
        url_row.addWidget(self._add_url_btn)
        url_row.addWidget(self._load_csv_btn)
        layout.addLayout(url_row)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("清晰度"))
        self._quality_combo = QComboBox()
        for label, _ in self._QUALITY_PRESETS:
            self._quality_combo.addItem(label)
        self._quality_combo.setToolTip(
            "严格下载所选清晰度；仅 1080p 缺失时向下兼容 720p，其他档位不兼容"
        )
        self._min_height_input = QLineEdit()
        self._min_height_input.setPlaceholderText("如 720")
        self._min_height_input.setText("720")
        self._min_height_input.setMaximumWidth(80)
        self._min_height_input.setVisible(False)
        opts.addWidget(self._quality_combo)
        opts.addWidget(self._min_height_input)

        opts.addWidget(QLabel("输出"))
        self._output_input = QLineEdit(str(self._output_dir))
        self._browse_btn = QPushButton("浏览…")
        opts.addWidget(self._output_input, stretch=1)
        opts.addWidget(self._browse_btn)
        layout.addLayout(opts)

        self._csv_label = QLabel("")
        self._csv_label.setObjectName("mutedLabel")
        layout.addWidget(self._csv_label)

        queue_label = QLabel("下载队列")
        queue_label.setObjectName("sectionLabel")
        layout.addWidget(queue_label)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["平台", "ID/URL", "状态", "进度"])
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._table, stretch=2)

        # Per-video progress (compact)
        stats = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_label = QLabel("0%")
        self._speed_label = QLabel("—")
        self._eta_label = QLabel("ETA: —")
        self._size_label = QLabel("— / —")
        stats.addWidget(self._progress_bar, stretch=1)
        stats.addWidget(self._progress_label)
        stats.addWidget(self._size_label)
        stats.addWidget(self._speed_label)
        stats.addWidget(self._eta_label)
        layout.addLayout(stats)

        self._status_label = QLabel("请加载 CSV 或粘贴链接")
        layout.addWidget(self._status_label)

        action_row = QHBoxLayout()
        self._download_btn = QPushButton("开始")
        self._download_btn.setObjectName("primaryBtn")
        self._download_btn.setEnabled(False)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setEnabled(False)
        self._open_dir_btn = QPushButton("打开目录")
        self._log_toggle_btn = QPushButton("收起日志")
        action_row.addWidget(self._download_btn)
        action_row.addWidget(self._cancel_btn)
        action_row.addWidget(self._open_dir_btn)
        action_row.addStretch(1)
        action_row.addWidget(self._log_toggle_btn)
        layout.addLayout(action_row)

        self._batch_progress_bar = QProgressBar()
        self._batch_progress_bar.setRange(0, 100)
        self._batch_progress_bar.setFormat("总进度 %p%")
        layout.addWidget(self._batch_progress_bar)

        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("logPanel")
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setMinimumHeight(100)
        self._log_view.setMaximumHeight(180)
        layout.addWidget(self._log_view, stretch=1)
        return panel

    def _connect_signals(self) -> None:
        self._import_cookie_btn.clicked.connect(self._on_import_cookie)
        self._clear_cookie_btn.clicked.connect(self._on_clear_cookie)
        self._load_csv_btn.clicked.connect(self._on_load_csv)
        self._add_url_btn.clicked.connect(self._on_add_url)
        self._browse_btn.clicked.connect(self._on_browse)
        self._download_btn.clicked.connect(self._on_download)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._open_dir_btn.clicked.connect(self._on_open_dir)
        self._log_toggle_btn.clicked.connect(self._on_toggle_log)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self._theme_combo.currentTextChanged.connect(self._on_theme_changed)
        self._accent_btn.clicked.connect(self._on_pick_accent)
        self._shortcut_download = QShortcut("Ctrl+D", self)
        self._shortcut_download.activated.connect(self._on_download)
        self._shortcut_cancel = QShortcut("Escape", self)
        self._shortcut_cancel.activated.connect(self._on_cancel)

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _on_theme_changed(self, name: str) -> None:
        if name not in PRESETS:
            return
        self._palette = PRESETS[name]
        save_palette(self._palette)
        apply_theme(QApplication.instance(), self._palette)
        self._refresh_cookie_ui()
        self._log(f"主题切换: {name}")

    def _on_pick_accent(self) -> None:
        color = QColorDialog.getColor(QColor(self._palette.accent), self, "选择强调色")
        if not color.isValid():
            return
        self._palette = with_accent(self._palette, color.name())
        save_palette(self._palette, custom_accent=True)
        apply_theme(QApplication.instance(), self._palette)
        self._log(f"强调色: {color.name()}")

    # ------------------------------------------------------------------
    # Cookie / platform
    # ------------------------------------------------------------------

    def _on_select_platform(self, plat: Platform) -> None:
        self._selected_platform = plat
        self._log(f"当前平台: {plat.label}")

    def _refresh_cookie_ui(self) -> None:
        files = getattr(self._downloader, "_platform_cookiefiles", {}) or {}
        for plat in _PLATFORM_KEYS:
            label = self._cookie_labels[plat.value]
            path = files.get(plat.value)
            if path:
                label.setText(f"Cookie：已配置 ({Path(path).name})")
                label.setObjectName("cookieOk")
            else:
                label.setText("Cookie：未配置")
                label.setObjectName("cookieMissing")
            label.style().unpolish(label)
            label.style().polish(label)
        src = self._downloader.cookie_source()
        title = self._base_title
        if src:
            title += f"  |  {src}"
        self.setWindowTitle(title)

    def _is_busy(self) -> bool:
        if self._validate_cookie_worker is not None and self._validate_cookie_worker.isRunning():
            return True
        if self._worker is not None and self._worker.isRunning():
            return True
        return False

    def _apply_cookie_file(self, filepath: str) -> None:
        if self._is_busy():
            QMessageBox.warning(self, "请稍候", "当前正在下载或验证 Cookie，请稍后再导入。")
            return
        try:
            persist = self._remember_cookie_checkbox.isChecked()
            # Windows 路径规范化；按当前选中平台校验域名并入库
            self._downloader.set_cookiefile(
                filepath,
                persist=persist,
                preferred_platform=self._selected_platform.value,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._cookie_persisted = self._remember_cookie_checkbox.isChecked()
        self._refresh_cookie_ui()
        self._log(f"已导入 Cookie ({self._selected_platform.label}): {filepath}")
        self._status_label.setText("Cookie 已加载，正在后台验证...")
        self._import_cookie_btn.setEnabled(False)
        self._clear_cookie_btn.setEnabled(False)
        self._start_cookie_validation()

    def _start_cookie_validation(self) -> None:
        if self._validate_cookie_worker is not None and self._validate_cookie_worker.isRunning():
            return
        self._validate_cookie_worker = ValidateCookieWorker(self._downloader)
        self._validate_cookie_worker.finished.connect(self._on_cookie_validated)
        self._validate_cookie_worker.start()

    def _on_cookie_validated(self, ok: bool, msg: str) -> None:
        self._import_cookie_btn.setEnabled(True)
        self._clear_cookie_btn.setEnabled(True)
        self._refresh_cookie_ui()
        if ok:
            self._log(f"  {msg}")
            self._status_label.setText("Cookie 验证通过")
            QMessageBox.information(self, "Cookie 已导入", msg)
        else:
            self._log(f"  验证未通过: {msg}")
            self._status_label.setText("Cookie 已加载，验证未通过")
            QMessageBox.warning(self, "Cookie 验证", msg)

    def _on_import_cookie(self) -> None:
        if self._is_busy():
            QMessageBox.warning(self, "请稍候", "当前正在下载或验证 Cookie。")
            return
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            f"选择 {self._selected_platform.label} Cookie 文件",
            str(Path.home()),
            # 空格分隔扩展名；;; 分隔过滤器。末项用 * 兼容无扩展名文件（勿用 *.*）
            "Cookie 文件 (*.txt *.cookies);;所有文件 (*)",
        )
        if filepath:
            self._apply_cookie_file(filepath)


    def _on_clear_cookie(self) -> None:
        if self._validate_cookie_worker is not None and self._validate_cookie_worker.isRunning():
            QMessageBox.warning(self, "请稍候", "Cookie 验证进行中。")
            return
        plat = self._selected_platform.value
        self._downloader._platform_cookiefiles.pop(plat, None)  # noqa: SLF001
        YoutubeDownloader._persist_platform_cookiefile(plat, None)
        if self._downloader._cookiefile_platform == plat:  # noqa: SLF001
            self._downloader.clear_cookiefile(persist=True)
        self._refresh_cookie_ui()
        self._log(f"已清除 {self._selected_platform.label} Cookie")
        self._status_label.setText("Cookie 已清除")

    # ------------------------------------------------------------------
    # Import / queue
    # ------------------------------------------------------------------

    def _on_load_csv(self) -> None:
        filepaths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择导入文件（可多选）",
            "",
            "支持文件 (*.csv *.tsv *.txt);;所有文件 (*)",
        )
        if filepaths:
            self._process_imported_files(filepaths)

    def _on_add_url(self) -> None:
        text = self._url_edit.text().strip()
        if not text:
            return
        try:
            media = YoutubeDownloader.parse_input(text)
        except ValueError as exc:
            QMessageBox.warning(self, "无效输入", str(exc))
            return
        source = media.original if "://" in media.original else media.media_id
        if source not in self._csv_ids:
            self._csv_ids.append(source)
        self._csv_queue = [("paste", list(self._csv_ids), None)]
        self._append_queue_row(media.label, source, "等待中", 0)
        self._url_edit.clear()
        self._download_btn.setEnabled(True)
        self._csv_label.setText(f"队列 {len(self._csv_ids)} 个视频")
        self._status_label.setText(
            f"已加入 {media.label} — {self._quality_label(*self._get_quality_settings())}"
        )
        self._log(f"加入队列: {media.label} {source}")

    def _append_queue_row(self, platform_name: str, vid: str, status: str, progress: int) -> None:
        # Update existing row if same ID
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item and item.text() == vid:
                self._table.setItem(row, 2, QTableWidgetItem(status))
                self._table.setItem(row, 3, QTableWidgetItem(f"{progress}%"))
                return
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(platform_name))
        self._table.setItem(row, 1, QTableWidgetItem(vid))
        self._table.setItem(row, 2, QTableWidgetItem(status))
        self._table.setItem(row, 3, QTableWidgetItem(f"{progress}%"))

    def _set_row_status(self, video_id: str, status: str, progress: int | None = None) -> None:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item and item.text() == video_id:
                self._table.setItem(row, 2, QTableWidgetItem(status))
                if progress is not None:
                    self._table.setItem(row, 3, QTableWidgetItem(f"{progress}%"))
                return

    def _process_imported_files(self, filepaths: list[str]) -> None:
        if self._csv_queue and self._table.rowCount():
            reply = QMessageBox.question(
                self, "替换队列？",
                f"当前有任务队列，导入新文件将清空。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        all_ids: list[str] = []
        errors: list[str] = []
        detected_columns: list[str] = []
        self._csv_queue = []
        self._csv_cookie_overrides = {}
        self._table.setRowCount(0)
        seen_names: set[str] = set()

        for fp in filepaths:
            path = Path(fp)
            try:
                rows = YoutubeDownloader.load_csv_rows(path)
            except (FileNotFoundError, ValueError) as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            if not rows:
                errors.append(f"{path.name}: 未识别到有效视频 ID/链接")
                continue
            used_column = rows[0].get("_import_column", "")
            if used_column:
                detected_columns.append(f"{path.name}→{used_column}")
            ids = [row["video_id"] for row in rows]
            all_ids.extend(ids)
            for row in rows:
                override: dict[str, str] = {}
                cookiefile = (
                    row.get("cookiefile") or row.get("cookies_file") or row.get("cookie_file") or ""
                ).strip()
                if cookiefile:
                    cookie_path = Path(cookiefile).expanduser()
                    if not cookie_path.is_absolute():
                        cookie_path = (path.parent / cookie_path).resolve()
                    override["cookiefile"] = str(cookie_path)
                browser = (row.get("cookies_from_browser") or "").strip()
                if browser:
                    override["cookies_from_browser"] = browser
                if override:
                    self._csv_cookie_overrides[row["video_id"]] = override
                try:
                    media = YoutubeDownloader.parse_input(row["video_id"])
                    plat_label = media.label
                except ValueError:
                    plat_label = "?"
                self._append_queue_row(plat_label, row["video_id"], "等待中", 0)
            name = self._build_queue_name(path.stem, seen_names)
            seen_names.add(name)
            output_dir = self._resolve_import_output_dir(rows)
            self._csv_queue.append((name, list(dict.fromkeys(ids)), output_dir))

        if errors:
            QMessageBox.critical(self, "文件错误", "\n".join(errors))
        if not self._csv_queue:
            QMessageBox.warning(self, "导入失败", "未能识别视频列表。")
            return

        self._csv_ids = list(dict.fromkeys(all_ids))
        total = sum(len(ids) for _, ids, _ in self._csv_queue)
        col_hint = f" | 列: {', '.join(detected_columns)}" if detected_columns else ""
        self._csv_label.setText(f"队列 {len(self._csv_queue)} 组 / 共 {total} 个视频{col_hint}")
        self._download_btn.setEnabled(True)
        min_height, strict = self._get_quality_settings()
        self._status_label.setText(
            f"已加载 {total} 个视频 — {self._quality_label(min_height, strict)}"
        )
        self._log(f"加载队列: {total} 个视频")

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _on_browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择保存目录", self._output_input.text())
        if directory:
            self._output_dir = Path(directory)
            self._output_input.setText(directory)

    def _on_download(self) -> None:
        if not (self._csv_queue or self._csv_ids):
            QMessageBox.warning(self, "提示", "请先加载 CSV 或加入链接")
            return
        min_height, strict_quality = self._get_quality_settings()
        output_dir = Path(self._output_input.text())
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "目录错误", f"无法创建输出目录: {exc}")
            return
        self._last_fmt_id = AUTO_FORMAT_ID
        self._last_min_height = min_height
        self._last_strict_quality = strict_quality
        self._output_dir = output_dir
        if not self._csv_queue and self._csv_ids:
            self._csv_queue = [("batch", list(self._csv_ids), None)]
        self._start_queue(AUTO_FORMAT_ID, output_dir, min_height, strict_quality)

    def _start_queue(
        self, format_id: str, base_dir: Path, min_height: int, strict_quality: bool,
    ) -> None:
        if not self._csv_queue:
            return
        name, ids, explicit_dir = self._csv_queue.pop(0)
        output_dir = explicit_dir or self._build_queue_output_dir(base_dir, name)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_input.setText(str(output_dir))
        self._log(f"队列开始: {name} ({len(ids)} 个)")
        self._start_batch_download(
            format_id, output_dir, ids, min_height, strict_quality, results_dir=base_dir,
        )

    def _start_batch_download(
        self,
        format_id: str,
        output_dir: Path,
        video_ids: list[str],
        min_height: int,
        strict_quality: bool,
        results_dir: Path | None = None,
    ) -> None:
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
            strict_quality=strict_quality,
            results_dir=results_dir,
            cookie_overrides={
                source: self._csv_cookie_overrides[source]
                for source in video_ids
                if source in self._csv_cookie_overrides
            },
        )
        w = self._worker
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
        self._batch_progress_bar.setValue(0)
        self._set_downloading_ui(True)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._downloader.cancel()
            self._worker.wait(10000)
        self._set_downloading_ui(False)
        self._progress_bar.setValue(0)
        self._status_label.setText("已取消")
        self._log("用户取消下载")

    def _set_downloading_ui(self, downloading: bool) -> None:
        self._load_csv_btn.setEnabled(not downloading)
        self._add_url_btn.setEnabled(not downloading)
        self._download_btn.setEnabled(not downloading and bool(self._csv_queue or self._csv_ids))
        self._cancel_btn.setEnabled(downloading)
        self._quality_combo.setEnabled(not downloading)
        self._min_height_input.setEnabled(not downloading)
        self._output_input.setEnabled(not downloading)
        self._browse_btn.setEnabled(not downloading)
        self._import_cookie_btn.setEnabled(not downloading)
        if not downloading:
            self._worker = None

    def _on_quality_changed(self, index: int) -> None:
        _, preset = self._QUALITY_PRESETS[index]
        self._min_height_input.setVisible(preset is None)
        if preset is not None:
            self._min_height_input.setText(str(preset))

    def _get_quality_settings(self) -> tuple[int, bool]:
        index = self._quality_combo.currentIndex()
        _, preset = self._QUALITY_PRESETS[index]
        if preset is None:
            return self._parse_min_height(self._min_height_input.text()), True
        return preset, True

    @staticmethod
    def _quality_label(min_height: int, strict_quality: bool) -> str:
        if strict_quality:
            return f"严格 {min_height}p（仅 1080 缺失时兼容 720）"
        return f"严格 {min_height}p"

    @staticmethod
    def _parse_min_height(value: str) -> int:
        text = (value or "").strip().lower().replace("p", "")
        try:
            return max(720, int(text)) if text else 720
        except ValueError:
            return 720

    @staticmethod
    def _resolve_import_output_dir(rows: list[dict[str, str]]) -> Path | None:
        for row in rows:
            candidate = row.get("output_dir") or row.get("output_folder") or row.get("folder")
            if candidate:
                return Path(candidate)
        return None

    @staticmethod
    def _build_queue_name(stem: str, seen_names: set[str]) -> str:
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
        return base_dir / queue_name

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _on_progress(self, pct: int) -> None:
        self._progress_bar.setValue(pct)
        self._progress_label.setText(f"{pct}%")
        if self._current_video_id:
            self._set_row_status(self._current_video_id, "下载中", pct)

    def _on_status(self, text: str) -> None:
        self._status_label.setText(text)

    def _on_speed(self, text: str) -> None:
        self._speed_label.setText(text)

    def _on_eta(self, text: str) -> None:
        self._eta_label.setText(f"ETA: {text}")

    def _on_size(self, text: str) -> None:
        self._size_label.setText(text)

    def _on_batch_progress(self, pct: int) -> None:
        self._batch_progress_bar.setValue(pct)

    def _on_batch_video_started(
        self, index: int, total: int, video_id: str, cookie: bool
    ) -> None:
        self._current_video_id = video_id
        self._progress_bar.setValue(0)
        self._progress_label.setText("0%")
        self._speed_label.setText("—")
        self._eta_label.setText("ETA: —")
        self._size_label.setText("— / —")
        tag = " 🍪" if cookie else ""
        self._status_label.setText(f"[{index + 1}/{total}] 下载: {video_id}{tag}")
        self._set_row_status(video_id, "下载中", 0)
        self._log(f"[{index + 1}/{total}] 开始: {video_id}{tag}")

    def _on_batch_video_finished(self, index: int, path: str, cookie_used: bool) -> None:
        self._batch_done = index + 1
        vid = self._batch_video_ids[index] if index < len(self._batch_video_ids) else "?"
        self._set_row_status(vid, "完成", 100)
        tag = " [🍪]" if cookie_used else ""
        self._log(f"[{index + 1}/{self._batch_total}] 完成{tag}: {path}")

    def _on_batch_video_error(self, index: int, msg: str, cookie_used: bool) -> None:
        vid = self._batch_video_ids[index] if index < len(self._batch_video_ids) else "?"
        self._batch_errors = [(i, v, m) for i, v, m in self._batch_errors if i != index]
        self._batch_errors.append((index, vid, msg))
        status = "跳过" if "跳过" in msg or "低于" in msg else "失败"
        self._set_row_status(vid, status, 0)
        if not self._log_view.isVisible():
            self._log_view.setVisible(True)
            self._log_toggle_btn.setText("收起日志")
        self._log(f"[{index + 1}/{self._batch_total}] {status}: {vid}")
        self._log(f"  ↳ {msg}")

    def _on_batch_all_finished(
        self, success: int, fail: int, skipped: int, csv_path: str
    ) -> None:
        self._batch_progress_bar.setValue(100)
        self._status_label.setText(f"批量完成：成功 {success}，失败 {fail}")
        self._batch_errors.clear()

        if self._csv_queue:
            self._queue_results.append((success, fail, skipped, csv_path))
            fmt_id = self._worker._format_id if self._worker else self._last_fmt_id  # noqa: SLF001
            self._start_queue(fmt_id, self._output_dir, self._last_min_height, self._last_strict_quality)
            return

        if self._queue_results:
            self._queue_results.append((success, fail, skipped, csv_path))
            total_ok = sum(s for s, _, _, _ in self._queue_results)
            total_fail = sum(f for _, f, _, _ in self._queue_results)
            total_skip = sum(k for _, _, k, _ in self._queue_results)
            csv_list = "\n".join(f"  {p}" for _, _, _, p in self._queue_results)
            parts = [f"总数: {total_ok + total_fail + total_skip}", f"成功: {total_ok}"]
            if total_skip:
                parts.append(f"跳过: {total_skip}")
            parts.append(f"失败: {total_fail}")
            parts.append(f"\n结果 CSV:\n{csv_list}")
            QMessageBox.information(self, "队列下载完成", "\n".join(parts))
            self._log(f"队列全部完成: 成功 {total_ok}, 失败 {total_fail}")
            self._queue_results.clear()
        else:
            extra = []
            if self._worker is not None:
                skipped_path = getattr(self._worker, "_last_skipped_csv", "")
                failed_path = getattr(self._worker, "_last_failed_csv", "")
                if skipped_path:
                    extra.append(f"跳过列表 CSV:\n{skipped_path}")
                if failed_path:
                    extra.append(f"失败重试 CSV:\n{failed_path}")
            msg = (
                f"批量下载完成\n\n总数: {success + fail}\n成功: {success}\n失败: {fail}\n\n"
                f"详细结果 CSV:\n{csv_path}"
            )
            if extra:
                msg = f"{msg}\n\n" + "\n\n".join(extra)
            QMessageBox.information(self, "批量下载完成", msg)
            self._log(f"批量完成: 成功 {success}, 失败 {fail}, CSV: {csv_path}")

        self._set_downloading_ui(False)

    def _on_open_dir(self) -> None:
        path = Path(self._output_input.text())
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _on_toggle_log(self) -> None:
        visible = self._log_view.isVisible()
        self._log_view.setVisible(not visible)
        self._log_toggle_btn.setText("展开日志" if visible else "收起日志")

    def _append_log_line(self, text: str) -> None:
        if not hasattr(self, "_log_view"):
            return
        self._log_view.appendPlainText(text)
        self._log_view.ensureCursorVisible()

    def _log(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._append_log_line(f"[{ts}] {text}")

    # ------------------------------------------------------------------
    # Env wizard (kept from previous GUI)
    # ------------------------------------------------------------------

    def _show_env_wizard(self, errors: list, warnings: list | None = None) -> None:
        warnings = warnings or []
        pip_errors = [i for i in errors if i.install_kind == "pip"]
        binary_errors = [i for i in errors if i.install_kind == "binary"]
        other_errors = [i for i in errors if i.install_kind not in ("pip", "binary")]
        if pip_errors:
            lines = ["以下 Python 包缺失：\n"] + [f"  x {i.name}" for i in pip_errors]
            lines.append("\n是否自动 pip 安装？")
            reply = QMessageBox.question(
                self, "Python 依赖缺失", "\n".join(lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                for i in pip_errors:
                    if "pip install" in i.message:
                        pkg = i.message.split("pip install ")[-1].split("）")[0].rstrip(")")
                        try:
                            subprocess.run(
                                [sys.executable, "-m", "pip", "install"] + pkg.split(),
                                capture_output=True, timeout=120, check=False,
                            )
                        except Exception:
                            pass
        if binary_errors:
            lines = ["以下系统工具缺失：\n"] + [f"  x {i.name}\n    {i.message}" for i in binary_errors]
            QMessageBox.warning(self, "系统工具缺失", "\n".join(lines))
        if other_errors:
            QMessageBox.warning(
                self, "依赖缺失",
                "\n".join(f"  x {i.name}: {i.message}" for i in other_errors),
            )
        optional = [i for i in warnings if i.install_kind in ("pip", "binary")]
        if optional:
            lines = ["以下依赖缺失，部分功能不可用：\n"] + [
                f"  ! {i.name}: {i.message.split('（')[0] if '（' in i.message else i.message}"
                for i in optional
            ]
            QMessageBox.information(self, "可选依赖缺失", "\n".join(lines))
