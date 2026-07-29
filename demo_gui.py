"""Multi-platform downloader UI DEMO (frontend only).

This is a visual/interaction demo — no yt-dlp downloads.
正式入口请使用 ``python main.py``。

Run::

    python demo_gui.py
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
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

# Portable UI stylesheet for Fusion on Win / Linux / macOS.
# Uses Qt default font stack — no OS-specific font families.
_DEMO_QSS = """
QMainWindow {
    background: #f5f5f5;
}
QFrame#sidebar {
    background: #ebebeb;
    border-right: 1px solid #c8c8c8;
}
QLabel#titleLabel {
    font-size: 15px;
    font-weight: 600;
    color: #222;
}
QLabel#sectionLabel {
    font-weight: 600;
    color: #333;
    margin-top: 4px;
}
QPushButton {
    padding: 5px 12px;
    min-height: 22px;
}
QPushButton#primaryBtn {
    font-weight: 600;
}
QPushButton:disabled {
    color: #888;
}
QTableWidget {
    background: #fff;
    gridline-color: #ddd;
    border: 1px solid #c8c8c8;
}
QPlainTextEdit#logPanel {
    background: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #c8c8c8;
    font-family: monospace;
}
QProgressBar {
    border: 1px solid #c8c8c8;
    background: #fff;
    text-align: center;
    min-height: 18px;
}
QProgressBar::chunk {
    background: #3a7d44;
}
QLabel#cookieOk {
    color: #2e7d32;
}
QLabel#cookieMissing {
    color: #c62828;
}
QFrame#platformCard {
    background: #fafafa;
    border: 1px solid #d0d0d0;
    border-radius: 4px;
    padding: 4px;
}
"""

_PLATFORMS = ("YouTube", "Bilibili", "TikTok")

_SAMPLE_ROWS: tuple[tuple[str, str, str, int], ...] = (
    ("YouTube", "dQw4w9WgXcQ", "等待中", 0),
    ("Bilibili", "BV1xx411c7mD", "等待中", 0),
    ("TikTok", "7123456789012345678", "等待中", 0),
    ("YouTube", "jNQXAC9IVRw", "完成", 100),
    ("Bilibili", "BV1GJ411x7h7", "下载中", 42),
    ("TikTok", "https://www.tiktok.com/@demo/video/1", "失败", 0),
)


class DemoWindow(QMainWindow):
    """Recommended multi-platform downloader layout (demo only)."""

    def __init__(self) -> None:
        super().__init__()
        self._cookie_configured: dict[str, bool] = {p: False for p in _PLATFORMS}
        self._selected_platform = _PLATFORMS[0]
        self._output_dir = Path.home() / "Downloads"
        self._demo_running = False

        self.setWindowTitle("多平台下载器 UI Demo")
        self.resize(980, 640)
        self.setMinimumSize(720, 480)

        self._build_ui()
        self._populate_sample_queue()
        self._refresh_cookie_ui()
        self._log(
            f"demo: ready on {platform.system()} ({sys.platform}), "
            f"output default = {self._output_dir}"
        )

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = self._build_sidebar()
        main = self._build_main()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(sidebar)
        splitter.addWidget(main)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 740])
        outer.addWidget(splitter)

        status = QStatusBar()
        os_name = platform.system() or sys.platform
        status.showMessage(f"Demo 模式 · {os_name} · 无真实下载")
        self.setStatusBar(status)

    def _build_sidebar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setMinimumWidth(200)
        frame.setMaximumWidth(300)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("平台 / Cookie")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        hint = QLabel("选择平台后导入 Netscape cookie (.txt)")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        self._platform_buttons: dict[str, QPushButton] = {}
        self._cookie_labels: dict[str, QLabel] = {}
        self._platform_cards: dict[str, QFrame] = {}

        for name in _PLATFORMS:
            card = QFrame()
            card.setObjectName("platformCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 8, 8, 8)
            card_layout.setSpacing(4)

            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.clicked.connect(lambda checked=False, n=name: self._on_select_platform(n))
            self._platform_buttons[name] = btn
            card_layout.addWidget(btn)

            status = QLabel()
            status.setObjectName("cookieMissing")
            self._cookie_labels[name] = status
            card_layout.addWidget(status)

            self._platform_cards[name] = card
            layout.addWidget(card)

        self._platform_buttons[_PLATFORMS[0]].setChecked(True)

        btn_row = QHBoxLayout()
        self._import_cookie_btn = QPushButton("导入 Cookie")
        self._import_cookie_btn.clicked.connect(self._on_import_cookie)
        self._clear_cookie_btn = QPushButton("清除")
        self._clear_cookie_btn.clicked.connect(self._on_clear_cookie)
        btn_row.addWidget(self._import_cookie_btn)
        btn_row.addWidget(self._clear_cookie_btn)
        layout.addLayout(btn_row)

        layout.addStretch(1)

        note = QLabel("仅演示 UI，不调用 yt-dlp")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(note)
        return frame

    def _build_main(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QLabel("多平台下载器 — 前端 Demo")
        header.setObjectName("titleLabel")
        layout.addWidget(header)

        # URL / CSV
        url_row = QHBoxLayout()
        url_label = QLabel("链接 / CSV")
        url_label.setObjectName("sectionLabel")
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("粘贴视频 URL，或加载 CSV 批量列表…")
        self._load_csv_btn = QPushButton("加载 CSV")
        self._load_csv_btn.clicked.connect(self._on_load_csv)
        self._add_url_btn = QPushButton("加入队列")
        self._add_url_btn.clicked.connect(self._on_add_url)
        url_row.addWidget(url_label)
        url_row.addWidget(self._url_edit, stretch=1)
        url_row.addWidget(self._add_url_btn)
        url_row.addWidget(self._load_csv_btn)
        layout.addLayout(url_row)

        # Quality + output
        opts = QHBoxLayout()
        q_label = QLabel("画质")
        self._quality = QComboBox()
        self._quality.addItems(["720p", "1080p", "1440p", "2160p", "自定义"])
        self._quality.setCurrentText("1080p")
        self._quality.currentTextChanged.connect(
            lambda t: self._log(f"demo: quality → {t}")
        )
        opts.addWidget(q_label)
        opts.addWidget(self._quality)

        out_label = QLabel("输出目录")
        self._output_edit = QLineEdit(str(self._output_dir))
        self._output_edit.setReadOnly(True)
        self._browse_btn = QPushButton("浏览…")
        self._browse_btn.clicked.connect(self._on_browse_output)
        opts.addWidget(out_label)
        opts.addWidget(self._output_edit, stretch=1)
        opts.addWidget(self._browse_btn)
        layout.addLayout(opts)

        # Queue
        queue_label = QLabel("下载队列")
        queue_label.setObjectName("sectionLabel")
        layout.addWidget(queue_label)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["平台", "ID/URL", "状态", "进度"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._table, stretch=2)

        # Actions + overall progress
        action_row = QHBoxLayout()
        self._start_btn = QPushButton("开始")
        self._start_btn.setObjectName("primaryBtn")
        self._start_btn.clicked.connect(self._on_start)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        action_row.addWidget(self._start_btn)
        action_row.addWidget(self._cancel_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self._overall = QProgressBar()
        self._overall.setRange(0, 100)
        self._overall.setValue(0)
        self._overall.setFormat("总进度 %p%")
        layout.addWidget(self._overall)

        # Log (collapsible via toggle)
        log_header = QHBoxLayout()
        log_title = QLabel("日志")
        log_title.setObjectName("sectionLabel")
        self._toggle_log_btn = QPushButton("收起")
        self._toggle_log_btn.setFixedWidth(64)
        self._toggle_log_btn.clicked.connect(self._on_toggle_log)
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(self._toggle_log_btn)
        layout.addLayout(log_header)

        self._log_panel = QPlainTextEdit()
        self._log_panel.setObjectName("logPanel")
        self._log_panel.setReadOnly(True)
        self._log_panel.setMaximumBlockCount(500)
        self._log_panel.setMinimumHeight(100)
        self._log_panel.setMaximumHeight(180)
        layout.addWidget(self._log_panel, stretch=1)

        return panel

    # ── Sample data ──────────────────────────────────────────────────

    def _populate_sample_queue(self) -> None:
        self._table.setRowCount(0)
        for platform_name, vid, status, progress in _SAMPLE_ROWS:
            self._append_row(platform_name, vid, status, progress)
        done = sum(1 for *_, s, p in _SAMPLE_ROWS if s == "完成" or p >= 100)
        total = len(_SAMPLE_ROWS)
        self._overall.setValue(int(100 * done / total) if total else 0)

    def _append_row(self, platform_name: str, vid: str, status: str, progress: int) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(platform_name))
        self._table.setItem(row, 1, QTableWidgetItem(vid))
        self._table.setItem(row, 2, QTableWidgetItem(status))
        self._table.setItem(row, 3, QTableWidgetItem(f"{progress}%"))

    # ── Cookie UI state ──────────────────────────────────────────────

    def _refresh_cookie_ui(self) -> None:
        for name in _PLATFORMS:
            ok = self._cookie_configured[name]
            label = self._cookie_labels[name]
            if ok:
                label.setText("Cookie：已配置")
                label.setObjectName("cookieOk")
            else:
                label.setText("Cookie：未配置")
                label.setObjectName("cookieMissing")
            # Force stylesheet re-apply after objectName change
            label.style().unpolish(label)
            label.style().polish(label)

    def _on_select_platform(self, name: str) -> None:
        self._selected_platform = name
        self._log(f"demo: selected platform → {name}")

    def _on_import_cookie(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"导入 {self._selected_platform} Cookie",
            str(Path.home()),
            "Cookie 文件 (*.txt);;所有文件 (*)",
        )
        if not path:
            self._log("demo: import cookie cancelled")
            return
        self._cookie_configured[self._selected_platform] = True
        self._refresh_cookie_ui()
        self._log(
            f"demo: import cookie clicked — {self._selected_platform} "
            f"← {Path(path).name} (UI state only)"
        )

    def _on_clear_cookie(self) -> None:
        self._cookie_configured[self._selected_platform] = False
        self._refresh_cookie_ui()
        self._log(f"demo: clear cookie clicked — {self._selected_platform}")

    # ── Main actions ─────────────────────────────────────────────────

    def _on_browse_output(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录",
            str(self._output_dir),
        )
        if not chosen:
            self._log("demo: browse output cancelled")
            return
        self._output_dir = Path(chosen)
        self._output_edit.setText(str(self._output_dir))
        self._log(f"demo: output path → {self._output_dir}")

    def _on_load_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "加载 CSV",
            str(Path.home()),
            "CSV 文件 (*.csv);;所有文件 (*)",
        )
        if not path:
            self._log("demo: load CSV cancelled")
            return
        self._log(f"demo: load CSV clicked — {Path(path).name} (no parse)")
        QMessageBox.information(
            self,
            "Demo",
            "已选择 CSV（演示模式不会解析内容）。\n"
            f"{Path(path).name}",
        )

    def _on_add_url(self) -> None:
        text = self._url_edit.text().strip()
        if not text:
            self._log("demo: add URL — empty input")
            return
        self._append_row(self._selected_platform, text, "等待中", 0)
        self._url_edit.clear()
        self._log(f"demo: added to queue — {self._selected_platform} / {text}")

    def _on_start(self) -> None:
        self._demo_running = True
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._overall.setValue(15)
        self._log("demo: start clicked — no real download")
        self.statusBar().showMessage(
            f"Demo 运行中 · {platform.system()} · 无真实下载"
        )

    def _on_cancel(self) -> None:
        self._demo_running = False
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._log("demo: cancel clicked")
        self.statusBar().showMessage(
            f"Demo 模式 · {platform.system()} · 无真实下载"
        )

    def _on_toggle_log(self) -> None:
        visible = self._log_panel.isVisible()
        self._log_panel.setVisible(not visible)
        self._toggle_log_btn.setText("展开" if visible else "收起")
        self._log(f"demo: log panel {'hidden' if visible else 'shown'}")

    def _log(self, message: str) -> None:
        self._log_panel.appendPlainText(message)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(_DEMO_QSS)
    window = DemoWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
