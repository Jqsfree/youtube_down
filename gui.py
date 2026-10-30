"""主窗口 —— 多平台下载 UI（YouTube / Bilibili / TikTok）。

不直接调用 yt-dlp。所有操作通过 DownloaderFacade(YoutubeDownloader) + BatchDownloadWorker 完成。
布局：左栏平台 Cookie，右栏队列与进度。主题由 theme.py 提供。
"""

from __future__ import annotations

import os
import platform as py_platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from batch_io import (
    PASTE_SOURCE,
    InputFile,
    OverviewStore,
    discover_inputs,
    output_dir_for,
    parse_pasted_text,
)
from downloader import (
    YoutubeDownloader,
    build_extra_options,
    check_environment,
    looks_like_playlist,
    proxy_reachable,
)
from logger_utils import AppLogger, redact
from platforms.base import Platform
from theme import (
    PRESETS,
    app_icon,
    apply_theme,
    load_palette,
    preset_names,
    save_palette,
    settings,
    theme_preview,
    with_accent,
)
from worker import (
    AUTO_FORMAT_ID,
    AppUpdateWorker,
    BatchDownloadWorker,
    PlaylistExpandWorker,
    ValidateCookieWorker,
    YtdlpUpdateWorker,
)
from yt_dlp_updater import load_state, overlay_enabled, should_auto_check

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
    _SUBS_LANG_SETS: tuple[list[str], ...] = (
        ["zh-Hans", "zh", "en"],
        ["zh-Hans", "zh"],
        ["en"],
    )

    def __init__(self) -> None:
        super().__init__()

        self._downloader = YoutubeDownloader()
        self._log_path = Path.home() / "Downloads" / "youtube_downloader.log"
        AppLogger.attach_file(self._log_path)
        self._worker: BatchDownloadWorker | None = None
        self._validate_cookie_worker: ValidateCookieWorker | None = None
        self._ytdlp_update_worker: YtdlpUpdateWorker | None = None
        self._ytdlp_check_interactive: bool = False
        self._app_update_worker: AppUpdateWorker | None = None
        self._app_check_interactive: bool = False
        self._release: Any = None
        self._output_dir: Path = Path.home() / "Downloads"
        self._csv_ids: list[str] = []
        # (来源相对路径, 来源列表, CSV 指定的输出目录, {来源: 原表行号})
        self._csv_queue: list[tuple[str, list[str], Path | None, dict[str, int]]] = []
        self._csv_cookie_overrides: dict[str, dict[str, str]] = {}
        self._overview: OverviewStore | None = None
        self._cookie_persisted: bool = bool(self._downloader._platform_cookiefiles)  # noqa: SLF001
        self._batch_done: int = 0
        self._batch_errors: list[tuple[int, str, str]] = []
        self._last_fmt_id: str = ""
        self._last_min_height: int = 720
        self._last_strict_quality: bool = True
        self._last_brutal_mode: bool = False
        self._batch_total: int = 0
        self._batch_video_ids: list[str] = []
        self._current_video_id: str = ""
        self._queue_results: list[tuple[int, int, int, str]] = []
        self._cancelled: bool = False
        self._downloading: bool = False
        self._paused: bool = False
        self._env_ok: bool = True
        self._source_dirs: dict[str, Path] = {}
        self._last_audio_only: bool = False
        self._last_audio_format: str = "m4a"
        self._expand_pending: list[str] = []
        self._expand_collected: list[str] = []
        self._expand_workers: list[PlaylistExpandWorker] = []
        self._selected_platform = Platform.YOUTUBE
        self._palette = load_palette()

        ver = Path(__file__).parent / "VERSION"
        self._app_version = ver.read_text().strip() if ver.exists() else "dev"
        self._base_title = f"Multi-Platform Downloader v{self._app_version}"
        self.setWindowTitle(self._base_title)
        self._icon = app_icon()
        if not self._icon.isNull():
            self.setWindowIcon(self._icon)
        self.resize(980, 640)
        self.setMinimumSize(720, 480)
        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()
        self._restore_settings()
        self._refresh_cookie_ui()
        AppLogger.attach_gui(self._append_log_line)

        all_ok, env_items = check_environment()
        self._env_ok = all_ok
        errors = [i for i in env_items if i.status == "error"]
        warnings = [i for i in env_items if i.status == "warning"]
        if errors or warnings:
            self._show_env_wizard(errors, warnings)

        versions = "  ".join(
            f"{i.name}={i.version}" if i.version else i.name for i in env_items
        )
        print(f"[env] {versions}")
        AppLogger.start_session("GUI")
        for i in env_items:
            icon = "✗" if i.status == "error" else ("⚠" if i.status == "warning" else "✓")
            extra = f" {i.message}" if i.message else ""
            self._log(f"  {icon} {i.name} {i.version}{extra}")
        self.statusBar().showMessage(
            f"{py_platform.system()} · 主题 {self._palette.name} · 请导入 CSV 或粘贴链接"
        )
        if overlay_enabled():
            QTimer.singleShot(1500, self._maybe_auto_check_ytdlp)
        QTimer.singleShot(2500, self._maybe_auto_check_app_update)

    def closeEvent(self, event: Any) -> None:
        self._save_settings()
        self._stop_background_threads()
        AppLogger.get_logger().info("应用退出")
        event.accept()

    def _stop_background_threads(self, timeout: int = 3000) -> None:
        """停掉所有后台线程；退出与安装更新共用。

        ``QThread.quit()`` 对没有事件循环的 ``run()`` 无效，必须
        ``close_queue()`` + ``downloader.cancel()`` 让 run() 自己返回。
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.close_queue()
            self._downloader.cancel()
            if not self._worker.wait(timeout):
                AppLogger.get_logger().warning("下载线程未在 %s ms 内退出", timeout)
                self._worker.wait()
        for worker in (
            self._app_update_worker,
            self._ytdlp_update_worker,
            self._validate_cookie_worker,
        ):
            if worker is not None and worker.isRunning():
                worker.wait(timeout)
        self._expand_pending.clear()
        self._expand_collected.clear()
        for expand in list(self._expand_workers):
            expand.wait(timeout)

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:
        # 下载中不接受拖入：会覆盖待执行队列并允许启动第二个 worker
        if self._download_running():
            event.ignore()
            return
        filepaths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.toLocalFile().lower().endswith((".csv", ".tsv", ".txt"))
        ]
        if not filepaths:
            # 拖入网页链接（浏览器拖拽带 url 但非本地文件）→ 走粘贴框同一条路径
            text = event.mimeData().text().strip()
            if "http://" in text or "https://" in text:
                self._url_edit.setPlainText(text)
                self._on_add_url()
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

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_main())
        self._pages.addWidget(self._build_cookie_page())
        self._pages.addWidget(self._build_settings_page())
        outer.addWidget(self._build_nav())
        outer.addWidget(self._pages, stretch=1)
        self.setStatusBar(QStatusBar())

    def _build_nav(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setFixedWidth(176)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(4)

        app_title = QLabel("视频批量下载")
        app_title.setObjectName("appTitle")
        layout.addWidget(app_title)
        subtitle = QLabel("YouTube · Bilibili · TikTok")
        subtitle.setObjectName("mutedLabel")
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        self._nav_buttons: list[QPushButton] = []
        for index, text in enumerate(("下载任务", "平台 Cookie", "设置")):
            btn = QPushButton(text)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.clicked.connect(lambda _=False, i=index: self._pages.setCurrentIndex(i))
            layout.addWidget(btn)
            self._nav_buttons.append(btn)
        self._nav_buttons[0].setChecked(True)

        layout.addStretch(1)
        ver = QLabel(f"v{self._app_version}")
        ver.setObjectName("mutedLabel")
        layout.addWidget(ver)
        return frame

    @staticmethod
    def _page(title: str, hint: str = "") -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(12)
        header = QLabel(title)
        header.setObjectName("titleLabel")
        layout.addWidget(header)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("mutedLabel")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)
        return page, layout

    def _build_cookie_page(self) -> QWidget:
        page, layout = self._page(
            "平台 Cookie",
            "公开视频无需 Cookie；遇到登录、年龄或机器人验证时才会使用。"
            "浏览器 Cookie 仅在点击重新读取后启用；也可导入 Netscape 格式的 cookies.txt。"
            "Cookie 等同账号凭据，请勿分享。"
            "Windows 请手动导入 Cookie 文件。",
        )
        self._platform_buttons: dict[str, QPushButton] = {}
        self._cookie_labels: dict[str, QLabel] = {}

        card, card_layout = self._card(spacing=6)
        for plat in _PLATFORM_KEYS:
            row = QFrame()
            row.setObjectName("platformCard")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            btn = QPushButton(plat.label)
            btn.setObjectName("platformBtn")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setMinimumWidth(150)
            btn.clicked.connect(lambda checked=False, p=plat: self._on_select_platform(p))
            self._platform_buttons[plat.value] = btn
            status = QLabel()
            status.setObjectName("cookieMissing")
            self._cookie_labels[plat.value] = status
            row_layout.addWidget(btn)
            row_layout.addWidget(status, stretch=1)
            card_layout.addWidget(row)
        self._platform_buttons[Platform.YOUTUBE.value].setChecked(True)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._import_cookie_btn = QPushButton("导入 Cookie")
        self._import_cookie_btn.setObjectName("primaryBtn")
        self._clear_cookie_btn = QPushButton("清除")
        self._clear_cookie_btn.setObjectName("dangerBtn")
        self._reload_browser_cookie_btn = QPushButton("重新读取浏览器")
        self._reload_browser_cookie_btn.setToolTip(
            "重新检测浏览器并导出 Cookie（建议先关闭 Chrome/Edge）"
        )
        self._remember_cookie_checkbox = QCheckBox("记住为默认")
        self._remember_cookie_checkbox.setToolTip("勾选后保存为该平台默认 Cookie")
        action_row.addWidget(self._import_cookie_btn)
        action_row.addWidget(self._clear_cookie_btn)
        action_row.addWidget(self._reload_browser_cookie_btn)
        action_row.addSpacing(8)
        action_row.addWidget(self._remember_cookie_checkbox)
        action_row.addStretch(1)
        card_layout.addSpacing(6)
        card_layout.addLayout(action_row)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_settings_page(self) -> QWidget:
        page, layout = self._page("设置")

        look = QLabel("外观")
        look.setObjectName("sectionLabel")
        layout.addWidget(look)
        theme_card, theme_layout = self._card(spacing=10)
        grid = QGridLayout()
        grid.setSpacing(10)
        self._theme_buttons: dict[str, QToolButton] = {}
        for index, name in enumerate(preset_names()):
            btn = QToolButton()
            btn.setObjectName("themeSwatch")
            btn.setText(name)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setIcon(QIcon(theme_preview(PRESETS[name])))
            btn.setIconSize(QSize(132, 76))
            btn.clicked.connect(lambda _=False, n=name: self._on_theme_changed(n))
            grid.addWidget(btn, index // 4, index % 4)
            self._theme_buttons[name] = btn
        if self._palette.name in self._theme_buttons:
            self._theme_buttons[self._palette.name].setChecked(True)
        theme_layout.addLayout(grid)
        accent_row = QHBoxLayout()
        self._accent_btn = QPushButton("自定义强调色…")
        accent_row.addWidget(self._accent_btn)
        accent_row.addStretch(1)
        theme_layout.addLayout(accent_row)
        layout.addWidget(theme_card)

        about = QLabel("下载引擎")
        about.setObjectName("sectionLabel")
        layout.addWidget(about)
        engine_card, engine_layout = self._card()
        engine_row = QHBoxLayout()
        engine_label = QLabel(f"应用版本 v{self._app_version} · yt-dlp 可在线热更新（仅发布版 EXE）")
        engine_label.setObjectName("mutedLabel")
        self._app_update_btn = QPushButton("检查更新")
        self._app_update_btn.setToolTip("从 GitHub Releases 检查并安装新版本")
        self._ytdlp_update_btn = QPushButton("更新 yt-dlp")
        if overlay_enabled():
            self._ytdlp_update_btn.setToolTip("从 PyPI 更新 yt-dlp（需重启后生效）")
        else:
            self._ytdlp_update_btn.setEnabled(False)
            self._ytdlp_update_btn.setToolTip("仅发布版 EXE 支持热更新")
        engine_row.addWidget(engine_label, stretch=1)
        engine_row.addWidget(self._app_update_btn)
        engine_row.addWidget(self._ytdlp_update_btn)
        engine_layout.addLayout(engine_row)
        layout.addWidget(engine_card)
        layout.addStretch(1)
        return page

    @staticmethod
    def _card(spacing: int = 8) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(spacing)
        return card, card_layout

    def _build_main(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header = QLabel("下载任务")
        header.setObjectName("titleLabel")
        self._csv_label = QLabel("")
        self._csv_label.setObjectName("mutedLabel")
        header_row.addWidget(header)
        header_row.addStretch(1)
        header_row.addWidget(self._csv_label)
        layout.addLayout(header_row)

        # 输入卡片：粘贴框 + 导入按钮 + 下载选项
        input_card, input_layout = self._card(spacing=10)
        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        self._url_edit = QPlainTextEdit()
        self._url_edit.setObjectName("urlEdit")
        self._url_edit.setPlaceholderText(
            "粘贴 YouTube / Bilibili / TikTok 链接或 ID，可一次多条（换行、逗号、空格分隔）…"
        )
        self._add_url_btn = QPushButton("加入队列")
        self._add_url_btn.setObjectName("primaryBtn")
        self._load_csv_btn = QPushButton("加载 CSV")
        self._load_folder_btn = QPushButton("加载文件夹")
        self._load_folder_btn.setToolTip("递归读取文件夹内所有 CSV/TSV/TXT，输出目录保持相同层级")
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        btn_col.addWidget(self._add_url_btn)
        btn_col.addWidget(self._load_csv_btn)
        btn_col.addWidget(self._load_folder_btn)
        url_row.addWidget(self._url_edit, stretch=1)
        self._paste_btn = QPushButton("粘贴")
        self._paste_btn.setToolTip("把剪贴板内容填入左侧输入框")
        url_row.addWidget(self._paste_btn)
        url_row.addLayout(btn_col)
        input_layout.addLayout(url_row)
        self._url_edit.setFixedHeight(btn_col.sizeHint().height())

        opts = QHBoxLayout()
        opts.setSpacing(8)
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
        opts.addSpacing(8)

        self._audio_checkbox = QCheckBox("仅音频")
        self._audio_checkbox.setToolTip("下载最佳音轨并转换为 m4a / mp3")
        self._audio_format_combo = QComboBox()
        self._audio_format_combo.addItems(["m4a", "mp3", "opus"])
        self._audio_format_combo.setVisible(False)
        opts.addWidget(self._audio_checkbox)
        opts.addWidget(self._audio_format_combo)
        opts.addSpacing(8)

        opts.addWidget(QLabel("输出"))
        self._output_input = QLineEdit(str(self._output_dir))
        self._browse_btn = QPushButton("浏览…")
        opts.addWidget(self._output_input, stretch=1)
        opts.addWidget(self._browse_btn)
        opts.addSpacing(8)
        self._brutal_checkbox = QCheckBox("暴力下载（8 线程）")
        self._brutal_checkbox.setToolTip(
            "更高重试、更短间隔、最多 8 条并行；可能加剧限流，建议配合 Cookie 文件"
        )
        opts.addWidget(self._brutal_checkbox)
        input_layout.addLayout(opts)

        extras = QHBoxLayout()
        extras.setSpacing(8)
        self._subs_checkbox = QCheckBox("字幕")
        self._subs_checkbox.setToolTip("下载字幕 SRT 并嵌入视频（仅视频模式）")
        self._subs_lang_combo = QComboBox()
        self._subs_lang_combo.addItems(["中英", "中文", "英文"])
        self._subs_lang_combo.setToolTip("嵌入的字幕语言")
        self._meta_checkbox = QCheckBox("元数据")
        self._meta_checkbox.setToolTip("把标题、上传者等写入文件")
        self._thumb_checkbox = QCheckBox("封面")
        self._thumb_checkbox.setToolTip("下载并嵌入视频封面")
        self._proxy_input = QLineEdit()
        self._proxy_input.setPlaceholderText("代理 http://127.0.0.1:7897（留空不走代理）")
        self._rate_combo = QComboBox()
        self._rate_combo.addItems(["不限速", "1M", "5M", "10M"])
        self._rate_combo.setToolTip("单条视频下载限速")
        self._impersonate_checkbox = QCheckBox("浏览器指纹（实验）")
        self._impersonate_checkbox.setToolTip(
            "仅取信息阶段用 curl-cffi 模拟 Chrome；需安装 curl-cffi"
        )
        extras.addWidget(self._subs_checkbox)
        extras.addWidget(self._subs_lang_combo)
        extras.addWidget(self._meta_checkbox)
        extras.addWidget(self._thumb_checkbox)
        extras.addSpacing(8)
        extras.addWidget(QLabel("代理"))
        extras.addWidget(self._proxy_input, stretch=1)
        extras.addWidget(QLabel("限速"))
        extras.addWidget(self._rate_combo)
        extras.addWidget(self._impersonate_checkbox)
        input_layout.addLayout(extras)
        layout.addWidget(input_card)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["平台", "ID/URL", "标题", "状态", "进度", "错误"]
        )
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_view.setHighlightSections(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(32)
        self._table.setShowGrid(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_menu)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._table, stretch=2)

        # 进度卡片：当前视频 + 总进度 + 操作按钮
        progress_card, progress_layout = self._card(spacing=8)
        status_row = QHBoxLayout()
        self._status_label = QLabel("请加载 CSV 或粘贴链接")
        self._status_label.setObjectName("statusLabel")
        self._progress_label = QLabel("0%")
        self._speed_label = QLabel("—")
        self._eta_label = QLabel("ETA: —")
        self._size_label = QLabel("— / —")
        status_row.addWidget(self._status_label, stretch=1)
        for stat in (self._size_label, self._speed_label, self._eta_label, self._progress_label):
            stat.setObjectName("statLabel")
            status_row.addSpacing(10)
            status_row.addWidget(stat)
        progress_layout.addLayout(status_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(False)
        progress_layout.addWidget(self._progress_bar)

        self._batch_progress_bar = QProgressBar()
        self._batch_progress_bar.setObjectName("batchProgress")
        self._batch_progress_bar.setRange(0, 100)
        self._batch_progress_bar.setFormat("总进度 %p%")
        progress_layout.addWidget(self._batch_progress_bar)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._download_btn = QPushButton("开始下载")
        self._download_btn.setObjectName("primaryBtn")
        self._download_btn.setEnabled(False)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("dangerBtn")
        self._cancel_btn.setEnabled(False)
        self._pause_btn = QPushButton("暂停")
        self._pause_btn.setEnabled(False)
        self._pause_btn.setToolTip("在当前视频结束后暂停，继续时从下一个视频开始")
        self._history_btn = QPushButton("历史")
        self._history_btn.setToolTip("查看当前输出目录的下载总览")
        self._open_dir_btn = QPushButton("打开目录")
        self._open_log_dir_btn = QPushButton("打开日志目录")
        self._open_log_dir_btn.clicked.connect(self._on_open_log_dir)
        self._log_toggle_btn = QPushButton("收起日志")
        action_row.addWidget(self._download_btn)
        action_row.addWidget(self._pause_btn)
        action_row.addWidget(self._cancel_btn)
        action_row.addWidget(self._history_btn)
        action_row.addWidget(self._open_dir_btn)
        action_row.addStretch(1)
        action_row.addWidget(self._open_log_dir_btn)
        action_row.addWidget(self._log_toggle_btn)
        progress_layout.addLayout(action_row)
        layout.addWidget(progress_card)

        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("logPanel")
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setMinimumHeight(100)
        self._log_view.setMaximumHeight(170)
        layout.addWidget(self._log_view, stretch=1)
        return panel

    def _connect_signals(self) -> None:
        self._import_cookie_btn.clicked.connect(self._on_import_cookie)
        self._clear_cookie_btn.clicked.connect(self._on_clear_cookie)
        self._reload_browser_cookie_btn.clicked.connect(self._on_reload_browser_cookie)
        self._load_csv_btn.clicked.connect(self._on_load_csv)
        self._load_folder_btn.clicked.connect(self._on_load_folder)
        self._add_url_btn.clicked.connect(self._on_add_url)
        self._paste_btn.clicked.connect(self._on_paste_clipboard)
        self._pause_btn.clicked.connect(self._on_pause)
        self._history_btn.clicked.connect(self._show_history)
        self._browse_btn.clicked.connect(self._on_browse)
        self._download_btn.clicked.connect(self._on_download)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._open_dir_btn.clicked.connect(self._on_open_dir)
        self._log_toggle_btn.clicked.connect(self._on_toggle_log)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self._audio_checkbox.toggled.connect(self._on_audio_toggled)
        self._accent_btn.clicked.connect(self._on_pick_accent)
        self._ytdlp_update_btn.clicked.connect(self._on_check_ytdlp_update)
        self._app_update_btn.clicked.connect(self._on_check_app_update)
        self._shortcut_download = QShortcut("Ctrl+D", self)
        self._shortcut_download.activated.connect(self._on_download)
        self._shortcut_cancel = QShortcut("Escape", self)
        self._shortcut_cancel.activated.connect(self._on_cancel)

    def _save_settings(self) -> None:
        s = settings()
        s.setValue("download/output_dir", self._output_input.text())
        s.setValue("download/quality_index", self._quality_combo.currentIndex())
        s.setValue("download/brutal", self._brutal_checkbox.isChecked())
        s.setValue("download/audio_only", self._audio_checkbox.isChecked())
        s.setValue("download/audio_format", self._audio_format_combo.currentText())
        s.setValue("download/subs", self._subs_checkbox.isChecked())
        s.setValue("download/subs_langs", self._subs_lang_combo.currentIndex())
        s.setValue("download/metadata", self._meta_checkbox.isChecked())
        s.setValue("download/thumbnail", self._thumb_checkbox.isChecked())
        s.setValue("download/proxy", self._proxy_input.text())
        s.setValue("download/rate", self._rate_combo.currentText())
        s.setValue("download/impersonate", self._impersonate_checkbox.isChecked())

    def _restore_settings(self) -> None:
        s = settings()
        output_dir = str(s.value("download/output_dir", "") or "")
        if output_dir:
            self._output_dir = Path(output_dir)
            self._output_input.setText(output_dir)
        index = int(s.value("download/quality_index", 0) or 0)
        self._quality_combo.setCurrentIndex(
            min(max(index, 0), self._quality_combo.count() - 1)
        )
        self._brutal_checkbox.setChecked(bool(s.value("download/brutal", False, type=bool)))
        self._meta_checkbox.setChecked(bool(s.value("download/metadata", False, type=bool)))
        self._thumb_checkbox.setChecked(bool(s.value("download/thumbnail", False, type=bool)))
        self._impersonate_checkbox.setChecked(
            bool(s.value("download/impersonate", False, type=bool))
        )
        self._proxy_input.setText(str(s.value("download/proxy", "") or ""))
        for combo, key in ((self._rate_combo, "download/rate"),
                           (self._audio_format_combo, "download/audio_format")):
            value = str(s.value(key, "") or "")
            if value and combo.findText(value) >= 0:
                combo.setCurrentText(value)
        self._audio_checkbox.setChecked(
            bool(s.value("download/audio_only", False, type=bool))
        )
        self._subs_checkbox.setChecked(bool(s.value("download/subs", False, type=bool)))
        lang_index = int(s.value("download/subs_langs", 0) or 0)
        self._subs_lang_combo.setCurrentIndex(
            min(max(lang_index, 0), self._subs_lang_combo.count() - 1)
        )

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

    def _batch_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # ------------------------------------------------------------------
    # 应用自更新（GitHub Releases）
    # ------------------------------------------------------------------

    def _app_worker_running(self) -> bool:
        return self._app_update_worker is not None and self._app_update_worker.isRunning()

    def _maybe_auto_check_app_update(self) -> None:
        """启动后静默检查（24h 一次），只在发现新版时提示。"""
        import app_updater

        s = settings()
        try:
            last = float(s.value("update/last_check", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if not app_updater.should_auto_check(last):
            return
        s.setValue("update/last_check", time.time())
        self._start_app_check(interactive=False)

    def _on_check_app_update(self) -> None:
        if self._app_worker_running():
            QMessageBox.information(self, "检查更新", "正在检查或下载更新，请稍候")
            return
        self._start_app_check(interactive=True)

    def _start_app_check(self, interactive: bool) -> None:
        self._app_check_interactive = interactive
        self._app_update_btn.setEnabled(False)
        worker = AppUpdateWorker("check")
        worker.check_done.connect(self._on_app_check_done)
        worker.finished.connect(worker.deleteLater)
        self._app_update_worker = worker
        worker.start()
        if interactive:
            self._log("正在检查应用更新…")

    def _on_app_check_done(
        self, available: bool, current: str, latest: str, notes: str, error: str,
    ) -> None:
        worker = self._app_update_worker
        self._release = getattr(worker, "release", None) if worker else getattr(
            self, "_release", None,
        )
        self._app_update_worker = None
        self._app_update_btn.setEnabled(True)
        if error:
            if self._app_check_interactive:
                QMessageBox.warning(self, "检查更新", f"检查失败：{error}")
            else:
                self._log(f"检查应用更新失败: {error}")
            return
        if not available:
            msg = f"已是最新版本（v{current or self._app_version}）"
            self._log(msg)
            if self._app_check_interactive:
                QMessageBox.information(self, "检查更新", msg)
            return
        self._app_update_btn.setText(f"安装更新 (v{latest})")
        self._log(f"发现新版本 v{latest}（当前 v{current or self._app_version}）")
        if not self._app_check_interactive:
            self._status_label.setText(f"发现新版本 v{latest}，可在「设置 → 检查更新」升级")
            return
        summary = "\n".join(notes.strip().splitlines()[:12]) if notes else "（无更新说明）"
        reply = QMessageBox.question(
            self,
            "发现新版本",
            f"当前版本: v{current or self._app_version}\n最新版本: v{latest}\n\n"
            f"{summary}\n\n现在下载并安装吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_app_download()

    def _start_app_download(self) -> None:
        import app_updater

        release = getattr(self, "_release", None)
        if release is None:
            QMessageBox.warning(self, "更新失败", "没有可用的发布信息，请重新检查更新")
            return
        if not app_updater.is_frozen():
            QDesktopServices.openUrl(QUrl(app_updater.RELEASES_PAGE))
            self._log("源码运行，已打开 Releases 页面手动下载")
            return
        if not self._app_worker_running():
            self._app_update_btn.setEnabled(False)
            worker = AppUpdateWorker("download", release=release)
            worker.progress.connect(self._on_app_download_progress)
            worker.download_done.connect(self._on_app_download_done)
            worker.finished.connect(worker.deleteLater)
            self._app_update_worker = worker
            worker.start()
            self._status_label.setText("正在下载新版本…")
            self._log(f"开始下载 v{release.version} 安装包")

    def _on_app_download_progress(self, pct: int) -> None:
        self._status_label.setText(f"正在下载新版本… {pct}%")

    def _on_app_download_done(self, ok: bool, message: str) -> None:
        import app_updater

        self._app_update_worker = None
        self._app_update_btn.setEnabled(True)
        if not ok:
            self._log(f"下载更新失败: {message}")
            QMessageBox.warning(self, "更新失败", message)
            return
        self._log(f"更新包已就绪: {message}")
        if self._batch_running():
            reply = QMessageBox.question(
                self,
                "安装更新",
                "更新包已下载完成。安装会退出应用并重启，当前下载任务会被中断。\n\n"
                "现在安装吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
        else:
            reply = QMessageBox.question(
                self,
                "安装更新",
                "更新包已下载完成，现在退出并安装新版本吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
        if reply != QMessageBox.StandardButton.Yes:
            self._status_label.setText("更新包已下载，可稍后重新检查更新安装")
            return
        # 必须先把后台线程停干净，再让脚本替换文件：
        # QApplication.quit() 不会触发 closeEvent，运行中的 QThread 被析构会 abort。
        self._shutdown_for_update()
        info = app_updater.install_and_restart(Path(message))
        self._log(info)
        if app_updater.is_frozen():
            QApplication.quit()
        else:
            QMessageBox.information(self, "更新", info)

    def _shutdown_for_update(self) -> None:
        """安装更新前停掉所有后台线程，避免文件仍被占用 / QThread 中途析构。"""
        for vid in self._batch_video_ids:
            row = self._row_for_video(vid)
            if row is None:
                continue
            item = self._table.item(row, 3)
            if item is not None and item.text() in ("排队中", "等待中"):
                self._set_row_status(vid, "已取消")
        self._stop_background_threads(timeout=10000)
        self._save_settings()
        self._log("已停止后台任务，准备安装更新")

    def _ytdlp_worker_running(self) -> bool:
        return self._ytdlp_update_worker is not None and self._ytdlp_update_worker.isRunning()

    def _maybe_auto_check_ytdlp(self) -> None:
        if not overlay_enabled() or self._batch_running():
            return
        if not should_auto_check(load_state()):
            return
        self._start_ytdlp_check(interactive=False)

    def _on_check_ytdlp_update(self) -> None:
        if not overlay_enabled():
            return
        if self._batch_running() or self._ytdlp_worker_running():
            QMessageBox.information(self, "yt-dlp 更新", "请等待当前下载或更新完成后再试")
            return
        self._start_ytdlp_check(interactive=True)

    def _start_ytdlp_check(self, interactive: bool) -> None:
        self._ytdlp_check_interactive = interactive
        self._ytdlp_update_btn.setEnabled(False)
        worker = YtdlpUpdateWorker("check")
        worker.check_done.connect(self._on_ytdlp_check_done)
        worker.finished.connect(worker.deleteLater)
        self._ytdlp_update_worker = worker
        worker.start()
        self._log("正在检查 yt-dlp 更新…")

    def _on_ytdlp_check_done(self, available: bool, current: str, latest: str, error: str) -> None:
        self._ytdlp_update_worker = None
        if overlay_enabled() and not self._batch_running():
            self._ytdlp_update_btn.setEnabled(True)
        if error:
            if self._ytdlp_check_interactive:
                QMessageBox.warning(self, "yt-dlp 更新", error)
            else:
                self._log(f"yt-dlp 检查失败: {error}")
            return
        if not available:
            msg = f"已是最新（{current or '?'}）"
            self._log(msg)
            if self._ytdlp_check_interactive:
                QMessageBox.information(self, "yt-dlp 更新", msg)
            return
        if self._batch_running():
            self._log(f"发现 yt-dlp {latest}，下载任务结束后可手动更新")
            return
        reply = QMessageBox.question(
            self,
            "yt-dlp 更新",
            f"发现新版本\n当前: {current or '?'}\n最新: {latest}\n\n下载并在下次启动启用？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_ytdlp_apply()

    def _start_ytdlp_apply(self) -> None:
        if self._batch_running() or self._ytdlp_worker_running():
            QMessageBox.information(self, "yt-dlp 更新", "请等待当前下载或更新完成后再试")
            return
        self._ytdlp_update_btn.setEnabled(False)
        worker = YtdlpUpdateWorker("apply")
        worker.apply_done.connect(self._on_ytdlp_apply_done)
        worker.finished.connect(worker.deleteLater)
        self._ytdlp_update_worker = worker
        worker.start()
        self._log("正在下载 yt-dlp…")

    def _on_ytdlp_apply_done(self, ok: bool, message: str) -> None:
        self._ytdlp_update_worker = None
        if overlay_enabled() and not self._batch_running():
            self._ytdlp_update_btn.setEnabled(True)
        self._log(message)
        if ok:
            QMessageBox.information(self, "yt-dlp 更新", message)
        else:
            QMessageBox.warning(self, "yt-dlp 更新失败", message)

    # ------------------------------------------------------------------
    # Cookie / platform
    # ------------------------------------------------------------------

    def _on_select_platform(self, plat: Platform) -> None:
        self._selected_platform = plat
        self._log(f"当前平台: {plat.label}")

    def _refresh_cookie_ui(self) -> None:
        files = getattr(self._downloader, "_platform_cookiefiles", {}) or {}
        browser_src = getattr(self._downloader, "_cookies_spec", None)
        cookiefile = getattr(self._downloader, "_cookiefile_path", None)
        for plat in _PLATFORM_KEYS:
            label = self._cookie_labels[plat.value]
            path = files.get(plat.value)
            if path:
                label.setText(f"Cookie：已配置 ({Path(path).name})")
                label.setObjectName("cookieOk")
            elif plat.value == "youtube" and cookiefile:
                label.setText(f"Cookie：已配置 ({Path(cookiefile).name})")
                label.setObjectName("cookieOk")
            elif plat.value == "youtube" and browser_src:
                label.setText(f"Cookie：浏览器 {browser_src}")
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
        self._log(f"已导入 Cookie ({self._selected_platform.label})")
        self._status_label.setText("Cookie 已加载，正在后台验证...")
        self._import_cookie_btn.setEnabled(False)
        self._clear_cookie_btn.setEnabled(False)
        self._reload_browser_cookie_btn.setEnabled(False)
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
        self._reload_browser_cookie_btn.setEnabled(True)
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

    def _on_reload_browser_cookie(self) -> None:
        if self._is_busy():
            QMessageBox.warning(self, "请稍候", "当前正在下载或验证 Cookie。")
            return
        # 清掉当前文件，强制重新检测 / 导出
        self._downloader._cookiefile_path = None  # noqa: SLF001
        self._downloader._cookiefile_platform = None  # noqa: SLF001
        self._downloader._browser_cookie_broken = False  # noqa: SLF001
        ok = self._downloader.redetect_browser()
        self._refresh_cookie_ui()
        src = self._downloader.cookie_source()
        if ok or src:
            self._log(f"已重新读取浏览器 Cookie: {src or 'browser'}")
            self._status_label.setText(f"Cookie 已更新: {src}")
            if self._downloader._cookiefile_path:  # noqa: SLF001
                self._start_cookie_validation()
        else:
            self._log("未能从浏览器读取 Cookie（请关闭浏览器后重试，或手动导入）")
            self._status_label.setText("未检测到浏览器 Cookie")
            QMessageBox.warning(
                self,
                "重新读取失败",
                "未能从浏览器读取 Cookie。\n请关闭 Chrome/Edge 后重试，或使用「导入 Cookie」。",
            )

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

    def _on_load_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择包含 CSV/TSV/TXT 的文件夹")
        if not directory:
            return
        inputs, errors = discover_inputs(Path(directory))
        self._load_inputs(inputs, errors)

    def _on_paste_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self.statusBar().showMessage("剪贴板为空", 3000)
            return
        self._url_edit.setPlainText(text)

    def _on_add_url(self) -> None:
        text = self._url_edit.toPlainText().strip()
        if not text:
            return
        valid, invalid = parse_pasted_text(text)
        if invalid:
            preview = "\n".join(invalid[:10])
            more = f"\n… 共 {len(invalid)} 条" if len(invalid) > 10 else ""
            QMessageBox.warning(self, "部分输入无效", f"以下内容无法识别，已忽略：\n{preview}{more}")
        if not valid:
            return
        playlist_urls = [s for s in valid if looks_like_playlist(s)]
        if playlist_urls:
            self._expand_pending = playlist_urls
            self._expand_collected = [s for s in valid if s not in playlist_urls]
            self._add_url_btn.setEnabled(False)
            self._status_label.setText(f"正在展开 {len(playlist_urls)} 个播放列表…")
            self._expand_next()
            return
        self._add_sources(valid)

    def _expand_next(self) -> None:
        if not self._expand_pending:
            self._add_url_btn.setEnabled(True)
            self._add_sources(self._expand_collected)
            return
        url = self._expand_pending.pop(0)
        worker = PlaylistExpandWorker(self._downloader, url, limit=500)
        worker.expanded.connect(self._on_playlist_expanded)
        worker.failed.connect(self._on_playlist_failed)
        worker.finished.connect(
            lambda w=worker: self._expand_workers.remove(w)
            if w in self._expand_workers else None
        )
        self._expand_workers.append(worker)
        worker.start()

    def _on_playlist_expanded(self, urls: list[str]) -> None:
        self._log(f"播放列表展开: {len(urls)} 条")
        self._expand_collected.extend(urls)
        self._expand_next()

    def _on_playlist_failed(self, message: str) -> None:
        self._log(f"播放列表展开失败: {message}")
        self._expand_next()

    def _reset_batch_state(self) -> None:
        """开始新一轮导入：清掉上一批的队列、结果表与统计，避免串批。"""
        self._csv_ids = []
        self._csv_queue = []
        self._source_dirs.clear()
        self._csv_cookie_overrides.clear()
        self._batch_video_ids = []
        self._batch_errors.clear()
        self._queue_results.clear()
        self._batch_done = 0
        self._batch_total = 0
        self._current_video_id = ""
        self._table.setRowCount(0)
        self._csv_label.setText("")
        self._reset_progress_display()

    def _reset_progress_display(self) -> None:
        self._progress_bar.setValue(0)
        self._batch_progress_bar.setValue(0)
        self._progress_label.setText("0%")
        self._speed_label.setText("—")
        self._eta_label.setText("ETA: —")
        self._size_label.setText("— / —")

    def _add_sources(self, sources: list[str]) -> None:
        # 上一批已结束（队列被消费完）时，粘贴/展开的新链接开启新一轮
        if not self._download_running() and not self._csv_queue:
            self._reset_batch_state()
        if not sources:
            self._url_edit.clear()
            self._status_label.setText("播放列表为空或展开失败")
            self._log("未加入任何视频")
            return
        added_ids: list[str] = []
        for source in sources:
            if source in self._csv_ids:
                continue
            self._csv_ids.append(source)
            added_ids.append(source)
            try:
                label = YoutubeDownloader.parse_input(source).label
            except ValueError:
                label = "?"
            self._append_queue_row(label, source, "等待中", 0)
        live = self._enqueue_live(added_ids)
        if added_ids and not live:
            # 未在下载中（或 worker 已收尾）：进入下一轮队列
            # 保留已导入的 CSV 分组，只把新链接并入/新增 paste 组
            paste_index = next(
                (
                    index
                    for index, (name, _ids, explicit, _rows) in enumerate(self._csv_queue)
                    if name == PASTE_SOURCE and explicit is None
                ),
                None,
            )
            if paste_index is None:
                self._csv_queue.append(
                    (PASTE_SOURCE, added_ids, None,
                     {source: index for index, source in enumerate(added_ids, start=1)})
                )
            else:
                name, ids, explicit, _rows = self._csv_queue[paste_index]
                ids = ids + added_ids
                self._csv_queue[paste_index] = (
                    name, ids, explicit,
                    {source: index for index, source in enumerate(ids, start=1)},
                )
        self._url_edit.clear()
        self._download_btn.setEnabled(self._env_ok and not self._download_running())
        if live:
            total = self._batch_total
            self._status_label.setText(f"已加入活动队列 {len(added_ids)} 个（当前共 {total} 个）")
        else:
            total = sum(len(ids) for _, ids, _, _ in self._csv_queue)
            if len(self._csv_queue) == 1 and self._csv_queue[0][0] == PASTE_SOURCE:
                self._csv_label.setText(f"队列 {total} 个视频")
            else:
                self._csv_label.setText(f"队列 {len(self._csv_queue)} 组 / 共 {total} 个视频")
            self._status_label.setText(
                f"已加入 {len(added_ids)} 个 — {self._quality_label(*self._get_quality_settings())}"
            )
        self._log(f"加入队列: {len(added_ids)} 个（共 {total} 个）")

    def _enqueue_live(self, video_ids: list[str]) -> bool:
        """把新任务塞进正在运行的 worker；成功返回 True。"""
        worker = self._worker
        if not video_ids or worker is None or not worker.isRunning():
            return False
        # 与 worker._seen_ids 保持一致，避免索引错位
        new_ids = [vid for vid in video_ids if vid not in self._batch_video_ids]
        if not new_ids:
            return True
        if not worker.enqueue(new_ids):
            return False
        self._batch_video_ids.extend(new_ids)
        self._batch_total = len(self._batch_video_ids)
        for vid in new_ids:
            self._set_row_status(vid, "排队中", 0)
        self._csv_label.setText(f"活动队列 {self._batch_total} 个视频")
        return True

    def _status_item(self, status: str) -> QTableWidgetItem:
        item = QTableWidgetItem(status)
        color = {
            "完成": self._palette.success,
            "失败": self._palette.danger,
            "下载中": self._palette.accent,
        }.get(status)
        if color:
            item.setForeground(QColor(color))
        return item

    def _append_queue_row(self, platform_name: str, vid: str, status: str, progress: int) -> None:
        # Update existing row if same ID
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item and item.text() == vid:
                self._table.setItem(row, 3, self._status_item(status))
                self._table.setItem(row, 4, QTableWidgetItem(f"{progress}%"))
                return
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(platform_name))
        self._table.setItem(row, 1, QTableWidgetItem(vid))
        self._table.setItem(row, 2, QTableWidgetItem(""))
        self._table.setItem(row, 3, self._status_item(status))
        self._table.setItem(row, 4, QTableWidgetItem(f"{progress}%"))
        self._table.setItem(row, 5, QTableWidgetItem(""))

    def _row_for_video(self, video_id: str) -> int | None:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item and item.text() == video_id:
                return row
        return None

    def _set_row_status(
        self,
        video_id: str,
        status: str,
        progress: int | None = None,
        title: str | None = None,
        error: str | None = None,
        path: str | None = None,
    ) -> None:
        row = self._row_for_video(video_id)
        if row is None:
            return
        self._table.setItem(row, 3, self._status_item(status))
        if progress is not None:
            self._table.setItem(row, 4, QTableWidgetItem(f"{progress}%"))
        if title:
            self._table.setItem(row, 2, QTableWidgetItem(title))
        if error is not None:
            self._table.setItem(row, 5, QTableWidgetItem(error))
        if path:
            item = self._table.item(row, 1)
            if item:
                item.setData(Qt.ItemDataRole.UserRole, path)

    def _process_imported_files(self, filepaths: list[str]) -> None:
        inputs: list[InputFile] = []
        errors: list[str] = []
        for fp in filepaths:
            found, errs = discover_inputs(Path(fp))
            inputs.extend(found)
            errors.extend(errs)
        self._load_inputs(inputs, errors)

    def _load_inputs(self, inputs: list[InputFile], errors: list[str]) -> None:
        if not inputs:
            QMessageBox.warning(self, "导入失败", "\n".join(errors) or "未能识别视频列表。")
            return
        if self._csv_queue and self._table.rowCount():
            reply = QMessageBox.question(
                self, "替换队列？",
                "当前有任务队列，导入新文件将清空。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        all_ids: list[str] = []
        detected_columns: list[str] = []
        self._reset_batch_state()
        seen_names: set[str] = set()

        for inp in inputs:
            path, rows = inp.path, inp.rows
            used_column = rows[0].get("_import_column", "")
            if used_column:
                detected_columns.append(f"{inp.rel}→{used_column}")
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
            name = self._unique_queue_rel(inp.rel, seen_names)
            seen_names.add(name)
            output_dir = self._resolve_import_output_dir(rows)
            if output_dir:
                self._source_dirs[name] = output_dir
            # 保留首次出现的行号；同源同 ID 合并（总览键限制）
            unique_ids = list(dict.fromkeys(ids))
            if len(unique_ids) < len(ids):
                self._log(
                    f"同源重复 ID 已合并: {inp.rel} "
                    f"{len(ids) - len(unique_ids)} 行（只保留首次）"
                )
            # 只记首次出现的行号（总览键为来源+id，无法保留重复行）
            row_numbers: dict[str, int] = {}
            for index, row in enumerate(rows, start=1):
                vid = row["video_id"]
                if vid not in row_numbers:
                    row_numbers[vid] = int(row.get("_row") or index)
            self._csv_queue.append((name, unique_ids, output_dir, row_numbers))

        if errors:
            QMessageBox.critical(self, "文件错误", "\n".join(errors))
        if not self._csv_queue:
            QMessageBox.warning(self, "导入失败", "未能识别视频列表。")
            return

        self._csv_ids = list(dict.fromkeys(all_ids))
        total = sum(len(ids) for _, ids, _, _ in self._csv_queue)
        col_hint = f" | 列: {', '.join(detected_columns)}" if detected_columns else ""
        self._csv_label.setText(f"队列 {len(self._csv_queue)} 组 / 共 {total} 个视频{col_hint}")
        self._download_btn.setEnabled(self._env_ok and not self._download_running())
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
        self._cancelled = False
        if not proxy_reachable(self._proxy_input.text()):
            QMessageBox.warning(
                self, "代理不可用",
                f"无法连接代理 {self._proxy_input.text().strip()}\n"
                "请启动代理，或清空代理后直连。",
            )
            return
        min_height, strict_quality = self._get_quality_settings()
        output_dir = Path(self._output_input.text()).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "目录错误", f"无法创建输出目录: {exc}")
            return
        self._last_fmt_id = AUTO_FORMAT_ID
        self._last_min_height = min_height
        self._last_strict_quality = strict_quality
        self._last_audio_only = self._audio_checkbox.isChecked()
        self._last_audio_format = self._audio_format_combo.currentText()
        self._downloader.set_ydl_opt_overrides(self._collect_ydl_overrides())
        self._downloader.set_impersonate(self._impersonate_checkbox.isChecked())
        self._output_dir = output_dir
        brutal = self._brutal_checkbox.isChecked()
        self._last_brutal_mode = brutal
        self._log(f"暴力下载={'开' if brutal else '关'}")
        if not self._csv_queue and self._csv_ids:
            self._csv_queue = [("batch", list(self._csv_ids), None, {})]
        self._overview = OverviewStore(output_dir)
        self._start_queue(AUTO_FORMAT_ID, output_dir, min_height, strict_quality, brutal)

    def _start_queue(
        self, format_id: str, base_dir: Path, min_height: int, strict_quality: bool,
        brutal_mode: bool = False,
    ) -> None:
        if not self._csv_queue:
            return
        name, ids, explicit_dir, row_numbers = self._csv_queue.pop(0)
        output_dir = explicit_dir or self._build_queue_output_dir(base_dir, name)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._log(f"队列开始: {name} ({len(ids)} 个) → {output_dir}")
        self._start_batch_download(
            format_id, output_dir, ids, min_height, strict_quality,
            results_dir=base_dir, brutal_mode=brutal_mode,
            source_rel=name, row_numbers=row_numbers,
        )

    def _start_batch_download(
        self,
        format_id: str,
        output_dir: Path,
        video_ids: list[str],
        min_height: int,
        strict_quality: bool,
        results_dir: Path | None = None,
        brutal_mode: bool = False,
        source_rel: str = "",
        row_numbers: dict[str, int] | None = None,
    ) -> None:
        self._batch_errors.clear()
        self._batch_done = 0
        self._batch_total = len(video_ids)
        self._batch_video_ids = list(video_ids)
        self._current_video_id = ""
        self._reset_progress_display()
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
            brutal_mode=brutal_mode,
            overview=self._overview,
            source_rel=source_rel,
            row_numbers=row_numbers,
            audio_only=self._last_audio_only,
            audio_format=self._last_audio_format,
        )
        self._worker.enable_live_queue()
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
        w.total_changed.connect(self._on_batch_total_changed)
        w.all_finished.connect(self._on_batch_all_finished)
        self._batch_progress_bar.setValue(0)
        self._paused = False
        self._pause_btn.setText("暂停")
        self._set_downloading_ui(True)
        self._worker.start()

    def _download_running(self) -> bool:
        return self._downloading

    def _on_cancel(self) -> None:
        if not self._download_running():
            return
        # 取消 = 整批停止；worker 仍在跑，收尾交给 all_finished
        self._cancelled = True
        self._csv_queue.clear()
        if self._worker is not None:
            self._worker.close_queue()
        self._downloader.cancel()
        # 活动队列里还没开始的项不会再被消费，行状态要复位
        for vid in self._batch_video_ids:
            row = self._row_for_video(vid)
            if row is None:
                continue
            item = self._table.item(row, 3)
            if item is not None and item.text() in ("排队中", "等待中"):
                self._set_row_status(vid, "已取消")
        self._cancel_btn.setEnabled(False)
        self._pause_btn.setEnabled(False)
        self._progress_bar.setValue(0)
        self._status_label.setText("正在取消…")
        self._log("用户取消下载")

    def _on_pause(self) -> None:
        if not self._download_running() or self._worker is None:
            return
        self._paused = not self._paused
        if self._paused:
            self._worker.pause()
            self._pause_btn.setText("继续")
            self._status_label.setText("已暂停（当前视频完成后生效）")
        else:
            self._worker.resume()
            self._pause_btn.setText("暂停")
            self._status_label.setText("继续下载…")

    def _set_downloading_ui(self, downloading: bool) -> None:
        self._downloading = downloading
        self._load_csv_btn.setEnabled(not downloading)
        self._load_folder_btn.setEnabled(not downloading)
        # 下载中仍可继续追加任务（活动队列）
        self._add_url_btn.setEnabled(True)
        self._paste_btn.setEnabled(True)
        self._download_btn.setEnabled(
            not downloading and self._env_ok and bool(self._csv_queue or self._csv_ids)
        )
        self._cancel_btn.setEnabled(downloading)
        self._pause_btn.setEnabled(downloading)
        if not downloading:
            self._paused = False
            self._pause_btn.setText("暂停")
        self._quality_combo.setEnabled(not downloading and not self._audio_checkbox.isChecked())
        self._min_height_input.setEnabled(not downloading)
        self._output_input.setEnabled(not downloading)
        self._browse_btn.setEnabled(not downloading)
        self._import_cookie_btn.setEnabled(not downloading)
        self._clear_cookie_btn.setEnabled(not downloading)
        self._reload_browser_cookie_btn.setEnabled(not downloading)
        self._brutal_checkbox.setEnabled(not downloading)
        self._audio_checkbox.setEnabled(not downloading)
        self._audio_format_combo.setEnabled(not downloading)
        self._subs_checkbox.setEnabled(not downloading and not self._audio_checkbox.isChecked())
        self._subs_lang_combo.setEnabled(not downloading and self._subs_checkbox.isChecked())
        self._meta_checkbox.setEnabled(not downloading)
        self._thumb_checkbox.setEnabled(not downloading)
        self._proxy_input.setEnabled(not downloading)
        self._rate_combo.setEnabled(not downloading)
        self._impersonate_checkbox.setEnabled(not downloading)
        if overlay_enabled():
            updating = (
                self._ytdlp_update_worker is not None
                and self._ytdlp_update_worker.isRunning()
            )
            self._ytdlp_update_btn.setEnabled(not downloading and not updating)
        if not downloading and self._worker is not None and not self._worker.isRunning():
            self._worker = None

    def _on_quality_changed(self, index: int) -> None:
        _, preset = self._QUALITY_PRESETS[index]
        self._min_height_input.setVisible(preset is None)
        if preset is not None:
            self._min_height_input.setText(str(preset))

    def _on_audio_toggled(self, checked: bool) -> None:
        self._audio_format_combo.setVisible(checked)
        self._quality_combo.setEnabled(not checked)
        self._min_height_input.setEnabled(not checked)
        self._subs_checkbox.setEnabled(not checked)
        self._subs_lang_combo.setEnabled(not checked)

    def _collect_ydl_overrides(self) -> dict[str, object]:
        """把界面开关翻译成 yt-dlp 选项（每次开始下载前调用）。"""
        return build_extra_options(
            subtitles=(
                self._subs_checkbox.isChecked()
                and not self._audio_checkbox.isChecked()
            ),
            metadata=self._meta_checkbox.isChecked(),
            thumbnail=self._thumb_checkbox.isChecked(),
            subtitle_langs=self._SUBS_LANG_SETS[self._subs_lang_combo.currentIndex()],
            proxy=self._proxy_input.text(),
            rate=self._rate_combo.currentText(),
        )

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
    def _unique_queue_rel(rel: str, seen_names: set[str]) -> str:
        candidate = rel
        suffix = 2
        while candidate in seen_names:
            candidate = f"{rel}_{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _build_queue_output_dir(base_dir: Path, queue_name: str) -> Path:
        return output_dir_for(base_dir, queue_name)

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

    def _on_batch_total_changed(self, total: int) -> None:
        self._batch_total = total
        self._csv_label.setText(f"活动队列 {total} 个视频")

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
        self._set_row_status(video_id, "下载中", 0, error="")
        self._log(f"[{index + 1}/{total}] 开始: {video_id}{tag}")

    def _on_batch_video_finished(
        self, index: int, path: str, cookie_used: bool, title: str
    ) -> None:
        self._batch_done = index + 1
        vid = self._batch_video_ids[index] if index < len(self._batch_video_ids) else "?"
        self._set_row_status(vid, "完成", 100, title=title, error="", path=path)
        tag = " [🍪]" if cookie_used else ""
        self._log(f"[{index + 1}/{self._batch_total}] 完成{tag}: {path}")

    def _on_batch_video_error(self, index: int, msg: str, cookie_used: bool) -> None:
        vid = self._batch_video_ids[index] if index < len(self._batch_video_ids) else "?"
        self._batch_errors = [(i, v, m) for i, v, m in self._batch_errors if i != index]
        self._batch_errors.append((index, vid, msg))
        status = "跳过" if "跳过" in msg or "低于" in msg else "失败"
        self._set_row_status(vid, status, 0, error=msg)
        if not self._log_view.isVisible():
            self._log_view.setVisible(True)
            self._log_toggle_btn.setText("收起日志")
        self._log(f"[{index + 1}/{self._batch_total}] {status}: {vid}")
        self._log(f"  ↳ {msg}")

    def _on_batch_all_finished(
        self, success: int, fail: int, skipped: int, overview_path: str
    ) -> None:
        self._batch_progress_bar.setValue(100)
        self._batch_errors.clear()

        if self._cancelled:
            self._queue_results.clear()
            self._csv_ids = []
            self._csv_label.setText("")
            self._status_label.setText(f"已取消（成功 {success}，失败 {fail}）")
            self._log(f"已取消: 成功 {success}, 失败 {fail}, 总览: {overview_path}")
            self._set_downloading_ui(False)
            return

        self._status_label.setText(f"批量完成：成功 {success}，失败 {fail}")

        if self._csv_queue:
            self._queue_results.append((success, fail, skipped, overview_path))
            fmt_id = self._worker._format_id if self._worker else self._last_fmt_id  # noqa: SLF001
            self._start_queue(
                fmt_id, self._output_dir, self._last_min_height, self._last_strict_quality,
                self._last_brutal_mode,
            )
            return

        if self._queue_results:
            self._queue_results.append((success, fail, skipped, overview_path))
            total_ok = sum(s for s, _, _, _ in self._queue_results)
            total_fail = sum(f for _, f, _, _ in self._queue_results)
            total_skip = sum(k for _, _, k, _ in self._queue_results)
            parts = [f"总数: {total_ok + total_fail + total_skip}", f"成功: {total_ok}"]
            if total_skip:
                parts.append(f"跳过: {total_skip}")
            parts.append(f"失败: {total_fail}")
            parts.append(f"\n下载总览:\n{overview_path}")
            QMessageBox.information(self, "队列下载完成", "\n".join(parts))
            self._log(f"队列全部完成: 成功 {total_ok}, 失败 {total_fail}, 总览: {overview_path}")
            self._queue_results.clear()
        else:
            parts = [f"总数: {success + fail + skipped}", f"成功: {success}"]
            if skipped:
                parts.append(f"跳过: {skipped}")
            parts.append(f"失败: {fail}")
            parts.append(f"\n下载总览:\n{overview_path}")
            QMessageBox.information(self, "批量下载完成", "批量下载完成\n\n" + "\n".join(parts))
            self._log(f"批量完成: 成功 {success}, 失败 {fail}, 总览: {overview_path}")

        # 本轮队列已结束；结果表保留供查看/重试，下一轮导入时再清空
        self._csv_ids = []
        self._csv_label.setText("")
        self._set_downloading_ui(False)

    def _on_open_log_dir(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._log_path.parent))):
            QMessageBox.warning(self, "无法打开", "日志目录暂时不可用。")

    def _on_open_dir(self) -> None:
        path = Path(self._output_input.text()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _show_history(self) -> None:
        """只读展示当前输出目录的 下载总览.csv。"""
        rows = sorted(
            OverviewStore(Path(self._output_input.text()).expanduser()).rows(),
            key=lambda r: (
                r["来源文件"], int(r["行号"]) if r["行号"].isdigit() else 0,
            ),
        )
        if not rows:
            QMessageBox.information(self, "下载历史", "当前输出目录还没有下载记录。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("下载历史")
        dialog.resize(960, 540)
        layout = QVBoxLayout(dialog)
        filter_combo = QComboBox()
        filter_combo.addItems(["全部", "成功", "失败", "跳过"])
        layout.addWidget(filter_combo)
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(
            ["来源文件", "行号", "标题", "状态", "错误类型", "错误信息", "输出文件"]
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(table)

        def fill() -> None:
            wanted = filter_combo.currentText()
            shown = [r for r in rows if wanted == "全部" or r["状态"] == wanted]
            table.setRowCount(len(shown))
            for row_index, row in enumerate(shown):
                for col, key in enumerate(
                    ["来源文件", "行号", "标题", "状态", "错误类型", "错误信息", "输出文件"]
                ):
                    table.setItem(row_index, col, QTableWidgetItem(row.get(key, "")))

        filter_combo.currentTextChanged.connect(fill)
        fill()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    @staticmethod
    def _open_path(path: Path) -> None:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _on_table_menu(self, pos: Any) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        id_item = self._table.item(row, 1)
        vid = id_item.text() if id_item else ""
        path = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        menu = QMenu(self)
        copy_action = menu.addAction("复制链接")
        file_action = menu.addAction("打开文件")
        file_action.setEnabled(bool(path))
        folder_action = menu.addAction("打开所在目录")
        retry_action = menu.addAction("重试失败项")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is copy_action:
            link = vid
            if "://" not in vid:
                try:
                    link = YoutubeDownloader.parse_input(vid).url
                except ValueError:
                    pass
            QApplication.clipboard().setText(link)
        elif chosen is file_action and path:
            self._open_path(Path(path))
        elif chosen is folder_action:
            if path:
                self._open_path(Path(path).parent)
            else:
                self._on_open_dir()
        elif chosen is retry_action:
            self._retry_failed()

    def _retry_failed(self) -> None:
        if self._download_running():
            return
        failed = []
        for row in range(self._table.rowCount()):
            status = self._table.item(row, 3)
            vid = self._table.item(row, 1)
            if status and vid and status.text() == "失败":
                failed.append(vid.text())
        if not failed:
            QMessageBox.information(self, "重试", "没有失败项")
            return
        # 沿用总览里原来的来源文件/行号，重试成功后不会留下同一条旧的失败行
        origin: dict[str, tuple[str, int]] = {}
        for row in self._overview.rows() if self._overview else []:
            vid = row.get("video_id", "")
            if vid in failed and vid not in origin:
                row_no = row.get("行号", "")
                origin[vid] = (
                    row.get("来源文件", "") or PASTE_SOURCE,
                    int(row_no) if row_no.isdigit() else 0,
                )
        groups: dict[str, list[str]] = {}
        numbers: dict[str, dict[str, int]] = {}
        for vid in failed:
            source, row_no = origin.get(vid, (PASTE_SOURCE, 0))
            groups.setdefault(source, []).append(vid)
            if row_no:
                numbers.setdefault(source, {})[vid] = row_no
        self._csv_ids = failed
        self._csv_queue = [
            (source, ids, self._source_dirs.get(source), numbers.get(source, {}))
            for source, ids in groups.items()
        ]
        self._log(f"重试失败项: {len(failed)} 个")
        self._on_download()

    def _on_toggle_log(self) -> None:
        visible = self._log_view.isVisible()
        self._log_view.setVisible(not visible)
        self._log_toggle_btn.setText("展开日志" if visible else "收起日志")

    def _append_log_line(self, text: str) -> None:
        if not hasattr(self, "_log_view"):
            return
        self._log_view.appendPlainText(redact(text))
        self._log_view.ensureCursorVisible()

    def _log(self, text: str) -> None:
        AppLogger.get_logger().info("%s", text)

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
