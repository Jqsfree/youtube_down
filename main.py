"""Multi-Platform Downloader — 程序入口。

用法::

    python main.py
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from gui import MainWindow
from theme import apply_theme


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    apply_theme(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
