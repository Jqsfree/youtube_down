"""Multi-Platform Downloader — 程序入口。

用法::

    python main.py
"""

from __future__ import annotations

import sys

from yt_dlp_updater import install_overlay_finder

install_overlay_finder()

from PySide6.QtWidgets import QApplication

import app_updater
from gui import MainWindow
from theme import app_icon, apply_theme


def main() -> None:
    # 清掉上次自动更新残留的 *.old
    app_updater.cleanup_old()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    apply_theme(app)
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
