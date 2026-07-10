"""主窗口 —— 纯 UI 层（CSV 批量下载）。

不直接调用 yt-dlp。所有操作通过 YoutubeDownloader + BatchDownloadWorker 完成。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
    QVBoxLayout,
    QWidget,
)

from downloader import YoutubeDownloader, check_environment
from logger_utils import AppLogger
from worker import AUTO_FORMAT_ID, BatchDownloadWorker, ValidateCookieWorker


class MainWindow(QMainWindow):
    """YouTube Downloader 主窗口。"""

    _QUALITY_PRESETS: tuple[tuple[str, int | None], ...] = (
        ("720p", 720),
        ("1080p", 1080),
        ("1440p", 1440),
        ("2160p (4K)", 2160),
        ("自动（尽量最高 ≥720p）", 720),
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
        self._cookie_persisted: bool = self._downloader._cookiefile_path is not None  # noqa: SLF001
        self._batch_done: int = 0
        self._batch_errors: list[tuple[int, str]] = []
        self._last_fmt_id: str = ""
        self._last_min_height: int = 720
        self._batch_total: int = 0
        self._batch_video_ids: list[str] = []
        self._queue_results: list[tuple[int, int, int, str]] = []  # (success, fail, skipped, csv)

        spec = self._downloader._cookies_spec  # noqa: SLF001
        ver = Path(__file__).parent / "VERSION"
        self._app_version = ver.read_text().strip() if ver.exists() else "dev"
        self._base_title = f"Multi-Platform Downloader v{self._app_version}"
        self._update_window_title()
        self.resize(900, 560)
        self.setAcceptDrops(True)
        self._build_ui()
        self._connect_signals()

        AppLogger.attach_gui(self._append_log_line)

        # ── 环境检测（UI 就绪后再弹向导）──
        all_ok, env_items = check_environment()
        errors = [i for i in env_items if i.status == "error"]
        warnings = [i for i in env_items if i.status == "warning"]
        if errors or warnings:
            self._show_env_wizard(errors, warnings)

        versions = "  ".join(
            f"{i.name}={i.version}" if i.version else i.name
            for i in env_items
        )
        print(f"[env] {versions}")
        self._env_report = env_items

        AppLogger.get_logger().info("应用启动")

        for i in env_items:
            icon = "✗" if i.status == "error" else ("⚠" if i.status == "warning" else "✓")
            self._log(f"  {icon} {i.name} {i.version}")

    # ------------------------------------------------------------------
    # 退出清理
    # ------------------------------------------------------------------

    def closeEvent(self, event: Any) -> None:
        """关闭窗口时确保 worker 线程终止。"""
        if self._validate_cookie_worker is not None and self._validate_cookie_worker.isRunning():
            self._validate_cookie_worker.wait(3000)
        if self._worker is not None and self._worker.isRunning():
            self._downloader.cancel()
            self._worker.quit()
            self._worker.wait(3000)
        AppLogger.get_logger().info("应用退出")
        event.accept()

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
        if not filepaths:
            return
        # 单个 cookies.txt 拖拽 → Cookie 导入，避免与 CSV 批量导入冲突
        if len(filepaths) == 1 and YoutubeDownloader.is_netscape_cookie_file(filepaths[0]):
            self._apply_cookie_file(filepaths[0])
            return
        self._process_imported_files(filepaths)

    # ------------------------------------------------------------------
    # 启动向导
    # ------------------------------------------------------------------

    def _show_env_wizard(
        self, errors: list, warnings: list | None = None,
    ) -> None:
        """启动时检测到缺失依赖：pip 包可自动安装，系统二进制仅提示安装方式。"""
        warnings = warnings or []
        pip_errors = [i for i in errors if i.install_kind == "pip"]
        binary_errors = [i for i in errors if i.install_kind == "binary"]
        other_errors = [i for i in errors if i.install_kind not in ("pip", "binary")]
        pip_warnings = [i for i in warnings if i.install_kind == "pip"]
        binary_warnings = [i for i in warnings if i.install_kind == "binary"]

        if pip_errors:
            lines = ["以下 Python 包缺失：\n"]
            cmds: list[str] = []
            for i in pip_errors:
                lines.append(f"  x {i.name}")
                if "pip install" in i.message:
                    cmds.append(
                        i.message.split("pip install ")[-1].split("）")[0].rstrip(")")
                    )
            lines.append("\n是否自动 pip 安装？")
            reply = QMessageBox.question(
                self, "Python 依赖缺失", "\n".join(lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes and cmds:
                self._status_label.setText("正在安装依赖...")
                for pkg in cmds:
                    self._log(f"pip install {pkg}")
                    try:
                        subprocess.run(
                            [sys.executable, "-m", "pip", "install"] + pkg.split(),
                            capture_output=True, timeout=120, check=False,
                        )
                        self._log(f"  ok {pkg}")
                    except Exception as exc:
                        self._log(f"  fail {pkg}: {exc}")
                self._status_label.setText("依赖安装完成，请重启应用")

        if binary_errors:
            lines = ["以下系统工具缺失，无法自动 pip 安装：\n"]
            for i in binary_errors:
                lines.append(f"  x {i.name}")
                if i.message:
                    lines.append(f"    {i.message.replace(chr(10), chr(10) + '    ')}")
            lines.append("\n请按上述说明安装后重启应用。")
            if os.name == "nt":
                lines.append("\n也可点击 Yes 尝试 winget install Gyan.FFmpeg（需管理员/网络）。")
                reply = QMessageBox.question(
                    self, "系统工具缺失", "\n".join(lines),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._status_label.setText("正在通过 winget 安装 ffmpeg...")
                    self._log("winget install Gyan.FFmpeg")
                    try:
                        subprocess.run(
                            ["winget", "install", "Gyan.FFmpeg",
                             "--accept-package-agreements", "--accept-source-agreements"],
                            capture_output=True, timeout=300, check=False,
                        )
                        self._status_label.setText("winget 安装完成，请重启应用并刷新 PATH")
                        self._log("  winget 安装命令已执行，请重启终端后重开应用")
                    except Exception as exc:
                        self._log(f"  winget 失败: {exc}")
            else:
                QMessageBox.warning(self, "系统工具缺失", "\n".join(lines))

        if other_errors:
            lines = ["以下依赖缺失：\n"] + [f"  x {i.name}: {i.message}" for i in other_errors]
            QMessageBox.warning(self, "依赖缺失", "\n".join(lines))

        optional = pip_warnings + binary_warnings
        if optional:
            lines = ["以下依赖缺失，部分功能不可用：\n"]
            cmds: list[str] = []
            for i in optional:
                msg = i.message.split("（")[0] if "（" in i.message else i.message
                lines.append(f"  ! {i.name}: {msg}")
                if i.install_kind == "pip" and "pip install" in i.message:
                    cmds.append(
                        i.message.split("pip install ")[-1].split("）")[0].rstrip(")")
                    )
            lines.append("\n安装以启用完整功能？（No 可跳过，不影响基本下载）")
            reply = QMessageBox.question(
                self, "可选依赖缺失", "\n".join(lines),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes and cmds:
                for pkg in cmds:
                    self._log(f"pip install {pkg}")
                    try:
                        subprocess.run(
                            [sys.executable, "-m", "pip", "install"] + pkg.split(),
                            capture_output=True, timeout=120, check=False,
                        )
                        self._log(f"  ok {pkg}")
                    except Exception as exc:
                        self._log(f"  fail {pkg}: {exc}")
                self._status_label.setText("可选依赖安装完成")

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

        # ---- Cookie 导入 ----
        cookie_layout = QHBoxLayout()
        cookie_layout.addWidget(QLabel("Cookie:"))
        self._cookie_label = QLabel("未配置")
        self._cookie_label.setWordWrap(True)
        cookie_layout.addWidget(self._cookie_label, stretch=1)
        self._import_cookie_btn = QPushButton("选择临时 Cookie")
        self._import_cookie_btn.setToolTip(
            "选择 Netscape 格式的 cookies.txt。\n"
            "支持 YouTube 或 Bilibili Cookie；Chrome/Edge 可用扩展「Get cookies.txt LOCALLY」导出。\n"
            "默认仅本次运行使用；勾选「记住为默认」才会保存。"
        )
        cookie_layout.addWidget(self._import_cookie_btn)
        self._remember_cookie_checkbox = QCheckBox("记住为默认")
        self._remember_cookie_checkbox.setToolTip(
            "不勾选时等同 yt-dlp --cookies /path/cookies.txt 的临时 Cookie；"
            "勾选后保存为当前平台默认 Cookie。"
        )
        cookie_layout.addWidget(self._remember_cookie_checkbox)
        self._clear_cookie_btn = QPushButton("清除")
        self._clear_cookie_btn.setEnabled(False)
        cookie_layout.addWidget(self._clear_cookie_btn)
        root.addLayout(cookie_layout)
        self._update_cookie_ui()

        # ---- CSV 批量导入 ----
        csv_layout = QHBoxLayout()
        self._load_csv_btn = QPushButton("Load CSV")
        self._load_csv_btn.setToolTip("导入 CSV/TSV/TXT，自动识别链接或 BV/ID 列")
        csv_layout.addWidget(self._load_csv_btn)
        self._version_label = QLabel(f"v{self._app_version}")
        self._version_label.setStyleSheet("color: gray;")
        csv_layout.addWidget(self._version_label)
        csv_layout.addWidget(QLabel("清晰度:"))
        self._quality_combo = QComboBox()
        for label, _ in self._QUALITY_PRESETS:
            self._quality_combo.addItem(label)
        self._quality_combo.setCurrentIndex(0)
        self._quality_combo.setToolTip(
            "批量下载时逐条自动匹配不低于所选清晰度的最佳格式"
        )
        self._quality_combo.setMinimumWidth(150)
        csv_layout.addWidget(self._quality_combo)
        self._min_height_input = QLineEdit()
        self._min_height_input.setPlaceholderText("如 720 / 1080")
        self._min_height_input.setText("720")
        self._min_height_input.setMaximumWidth(90)
        self._min_height_input.setVisible(False)
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
        self._status_label = QLabel("请先 Load CSV 导入视频列表")
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
        self._copy_log_btn = QPushButton("复制日志")
        self._copy_log_btn.clicked.connect(self._on_copy_log)
        btn_layout.addWidget(self._copy_log_btn)
        btn_layout.addStretch()
        root.addLayout(btn_layout)

    def _connect_signals(self) -> None:
        """连接控件信号到处理槽。"""
        self._import_cookie_btn.clicked.connect(self._on_import_cookie)
        self._clear_cookie_btn.clicked.connect(self._on_clear_cookie)
        self._load_csv_btn.clicked.connect(self._on_load_csv)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self._browse_btn.clicked.connect(self._on_browse)
        self._download_btn.clicked.connect(self._on_download)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._open_dir_btn.clicked.connect(self._on_open_dir)
        self._log_toggle_btn.toggled.connect(self._log_group.setVisible)
        self._shortcut_download = QShortcut("Ctrl+D", self)
        self._shortcut_download.activated.connect(self._on_download)
        self._shortcut_cancel = QShortcut("Escape", self)
        self._shortcut_cancel.activated.connect(self._on_cancel)

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
        if self._csv_queue:
            reply = QMessageBox.question(
                self, "替换队列？",
                f"当前有 {len(self._csv_queue)} 组任务，导入新文件将清空。继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        all_ids: list[str] = []
        errors: list[str] = []
        detected_columns: list[str] = []
        self._csv_queue = []
        self._csv_cookie_overrides = {}
        seen_names: set[str] = set()

        for fp in filepaths:
            path = Path(fp)
            try:
                rows = YoutubeDownloader.load_csv_rows(path)
            except FileNotFoundError as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            except ValueError as exc:
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
                    row.get("cookiefile")
                    or row.get("cookies_file")
                    or row.get("cookie_file")
                    or ""
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
            name = self._build_queue_name(path.stem, seen_names)
            seen_names.add(name)
            output_dir = self._resolve_import_output_dir(rows)
            self._csv_queue.append((name, list(dict.fromkeys(ids)), output_dir))

        if errors:
            QMessageBox.critical(self, "文件错误", "\n".join(errors))

        if not self._csv_queue:
            detail = "\n".join(errors) if errors else "请检查 CSV 是否包含 BV/URL/YouTube ID 列"
            QMessageBox.warning(self, "导入失败", f"未能从文件中识别视频列表。\n\n{detail}")
            return

        self._csv_ids = list(dict.fromkeys(all_ids))
        total = sum(len(ids) for _, ids, _ in self._csv_queue)
        parts = " → ".join(f"{name}({len(ids)})" for name, ids, _ in self._csv_queue)
        col_hint = f" | 列: {', '.join(detected_columns)}" if detected_columns else ""
        self._csv_label.setText(
            f"队列 {len(self._csv_queue)} 组 / 共 {total} 个视频{col_hint}"
        )
        self._refresh_imported_files_list(self._csv_queue)
        self._log(f"加载队列: {parts}")

        min_height = self._get_min_height()
        self._download_btn.setEnabled(True)
        self._status_label.setText(
            f"已加载 {total} 个视频 — 将按 {self._quality_label(min_height)} 逐条自动匹配格式"
        )

    def _refresh_imported_files_list(self, queue: list[tuple[str, list[str], Path | None]]) -> None:
        """刷新已导入文件列表，显示每个源文件名、视频数和目标目录。"""
        self._imported_files_list.clear()
        for name, ids, output_dir in queue:
            target = str(output_dir) if output_dir else "默认目录"
            self._imported_files_list.addItem(f"{name} — {len(ids)} 个视频 → {target}")
        if not queue:
            self._imported_files_list.addItem("暂无导入文件")

    # ------------------------------------------------------------------
    # Cookie / 窗口
    # ------------------------------------------------------------------

    def _update_window_title(self) -> None:
        title = self._base_title
        src = self._downloader.cookie_source()
        if src:
            title += f"  |  {src}"
        self.setWindowTitle(title)

    def _update_cookie_ui(self) -> None:
        src = self._downloader.cookie_source()
        if self._downloader._cookiefile_path:  # noqa: SLF001
            path = self._downloader._cookiefile_path
            platform = self._downloader._cookiefile_platform  # noqa: SLF001
            label = YoutubeDownloader._platform_label(platform) if platform else "自动"
            mode = "默认" if self._cookie_persisted else "临时"
            self._cookie_label.setText(f"{mode}文件 ({label}): {path}")
            self._clear_cookie_btn.setEnabled(True)
        elif src:
            self._cookie_label.setText(f"浏览器: {src.removeprefix('browser:')}")
            self._clear_cookie_btn.setEnabled(False)
        else:
            self._cookie_label.setText(
                "未配置（可临时选择 cookies.txt；Linux 可尝试浏览器 Cookie，Windows 推荐导出 cookies.txt）"
            )
            self._clear_cookie_btn.setEnabled(False)
        self._update_window_title()

    def _is_busy(self) -> bool:
        """是否有后台下载 / cookie 验证在进行。"""
        if self._validate_cookie_worker is not None and self._validate_cookie_worker.isRunning():
            return True
        if self._worker is not None and self._worker.isRunning():
            return True
        return False

    def _apply_cookie_file(self, filepath: str) -> None:
        """加载 cookie 文件并后台验证（供按钮与拖拽共用）。"""
        if self._is_busy():
            QMessageBox.warning(
                self, "请稍候",
                "当前正在下载或验证 Cookie，请完成或取消后再导入。",
            )
            return
        try:
            persist = self._remember_cookie_checkbox.isChecked()
            self._downloader.set_cookiefile(filepath, persist=persist)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._cookie_persisted = persist
        self._update_cookie_ui()
        mode = "默认" if persist else "临时"
        self._log(f"已选择{mode} Cookie: {filepath}")
        self._status_label.setText(f"{mode} Cookie 已加载，正在后台验证...")
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
        self._clear_cookie_btn.setEnabled(self._downloader._cookiefile_path is not None)  # noqa: SLF001
        if ok:
            self._log(f"  {msg}")
            self._status_label.setText("Cookie 验证通过，可直接 Load CSV 并下载")
            QMessageBox.information(
                self, "Cookie 已导入",
                f"{msg}\n\n可导入 CSV 后开始批量下载。",
            )
        else:
            self._log(f"  验证未通过: {msg}")
            self._status_label.setText("Cookie 已加载，验证未通过")
            QMessageBox.warning(
                self, "Cookie 验证",
                msg,
            )

    def _on_import_cookie(self) -> None:
        if self._is_busy():
            QMessageBox.warning(
                self, "请稍候",
                "当前正在下载或验证 Cookie，请完成或取消后再导入。",
            )
            return
        start_dir = str(Path.home())
        saved = self._downloader._cookiefile_path  # noqa: SLF001
        if saved:
            start_dir = str(saved.parent)
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Cookie 文件 (Netscape cookies.txt)",
            start_dir,
            "Cookie 文件 (*.txt *.cookies);;所有文件 (*.*)",
        )
        if filepath:
            self._apply_cookie_file(filepath)

    def _on_clear_cookie(self) -> None:
        if self._validate_cookie_worker is not None and self._validate_cookie_worker.isRunning():
            QMessageBox.warning(self, "请稍候", "Cookie 验证进行中，请稍后再清除。")
            return
        self._downloader.clear_cookiefile()
        self._cookie_persisted = False
        self._update_cookie_ui()
        self._log("已清除 Cookie 文件，恢复浏览器自动检测")
        self._status_label.setText("Cookie 已清除")

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
        """下载按钮：启动 CSV 批量下载。"""
        if not (self._csv_queue or self._csv_ids):
            QMessageBox.warning(self, "提示", "请先 Load CSV 导入视频列表")
            return

        min_height = self._get_min_height()
        output_dir = Path(self._output_input.text())
        if not output_dir.exists():
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                QMessageBox.critical(self, "目录错误", f"无法创建输出目录: {exc}")
                return

        self._last_fmt_id = AUTO_FORMAT_ID
        self._last_min_height = min_height
        if self._csv_queue:
            self._start_queue(AUTO_FORMAT_ID, output_dir, min_height)
        else:
            self._start_batch_download(AUTO_FORMAT_ID, output_dir, self._csv_ids, min_height)

    def _start_queue(self, format_id: str, base_dir: Path, min_height: int) -> None:
        """启动队列下载：逐个 CSV 依次处理。"""
        if not self._csv_queue:
            return
        name, ids, explicit_dir = self._csv_queue.pop(0)
        output_dir = explicit_dir or self._build_queue_output_dir(base_dir, name)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_input.setText(str(output_dir))
        self._log(f"队列开始: {name} ({len(ids)} 个)")
        self._start_batch_download(format_id, output_dir, ids, min_height, results_dir=base_dir)

    def _start_batch_download(
        self,
        format_id: str,
        output_dir: Path,
        video_ids: list[str],
        min_height: int,
        results_dir: Path | None = None,
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
            results_dir=results_dir,
            cookie_overrides={
                source: self._csv_cookie_overrides[source]
                for source in video_ids
                if source in self._csv_cookie_overrides
            },
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

    def _on_quality_changed(self, index: int) -> None:
        """切换清晰度预设时同步自定义输入框。"""
        _, preset = self._QUALITY_PRESETS[index]
        is_custom = preset is None
        self._min_height_input.setVisible(is_custom)
        if not is_custom and preset is not None:
            self._min_height_input.setText(str(preset))

    def _get_min_height(self) -> int:
        """从清晰度下拉或自定义输入解析目标分辨率。"""
        index = self._quality_combo.currentIndex()
        _, preset = self._QUALITY_PRESETS[index]
        if preset is not None:
            return preset
        return self._parse_min_height(self._min_height_input.text())

    @staticmethod
    def _quality_label(min_height: int) -> str:
        """生成状态栏用的清晰度描述。"""
        return f"不低于 {min_height}p 的最佳格式"

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
        self._load_csv_btn.setEnabled(not downloading)
        self._download_btn.setEnabled(not downloading and bool(self._csv_queue or self._csv_ids))
        self._cancel_btn.setEnabled(downloading)
        self._quality_combo.setEnabled(not downloading)
        self._min_height_input.setEnabled(not downloading)
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
        # 首次报错自动弹出日志面板
        if not self._log_group.isVisible():
            self._log_group.setVisible(True)
            self._log_toggle_btn.setChecked(True)
        self._log(f"[{index + 1}/{self._batch_total}] 失败: {vid}")
        self._log(f"  ↳ {msg}")

    def _on_batch_all_finished(
        self, success: int, fail: int, skipped: int, csv_path: str
    ) -> None:
        self._batch_progress_bar.setValue(100)
        self._batch_progress_label.setText(f"完成 — 成功 {success}，失败 {fail}")
        self._status_label.setText(f"批量下载完成：成功 {success}，失败 {fail}")

        self._batch_errors.clear()

        # 队列模式：累积结果，最后一个弹窗
        if self._csv_queue:
            self._queue_results.append((success, fail, skipped, csv_path))
            fmt_id = self._worker._format_id if self._worker else self._last_fmt_id  # noqa: SLF001
            min_height = self._last_min_height
            self._start_queue(fmt_id, self._output_dir, min_height)
            return

        # 队列全部完成（或单批次）→ 汇总弹窗
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
            self._log(f"队列全部完成: 成功 {total_ok}, 失败 {total_fail}" +
                      (f", 跳过 {total_skip}" if total_skip else ""))
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
