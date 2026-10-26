from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

from logger_utils import AppLogger


class GuiLogHandler(logging.Handler, QObject):
    """将日志转发到 GUI，通过 Qt Signal 确保线程安全。"""

    _log_message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self, parent)

    def connect_to(self, slot: Any) -> None:
        """连接 Signal 到 GUI 线程的 slot（自动排队）。"""
        self._log_message.connect(slot, Qt.ConnectionType.QueuedConnection)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno >= logging.ERROR and not AppLogger.gui_show_errors:
                return
            msg = self.format(record)
            self._log_message.emit(msg)
        except Exception:
            pass
