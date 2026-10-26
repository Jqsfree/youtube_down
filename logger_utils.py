from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gui_log import GuiLogHandler


class AppLogger:
    """统一日志器，兼容 GUI 显示和文件输出。"""

    _instance: logging.Logger | None = None
    gui_show_errors = False

    @classmethod
    def get_logger(cls) -> logging.Logger:
        if cls._instance is None:
            logger = logging.getLogger("youtube_downloader")
            logger.setLevel(logging.DEBUG)
            logger.propagate = False

            if not logger.handlers:
                formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%H:%M:%S")
                stream_handler = logging.StreamHandler()
                stream_handler.setFormatter(formatter)
                logger.addHandler(stream_handler)

            cls._instance = logger
        return cls._instance

    @classmethod
    def attach_gui(cls, slot: Any) -> GuiLogHandler:
        from gui_log import GuiLogHandler

        logger = cls.get_logger()
        for h in logger.handlers:
            if isinstance(h, GuiLogHandler):
                return h
        handler = GuiLogHandler()
        handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%H:%M:%S"))
        handler.connect_to(slot)
        logger.addHandler(handler)
        return handler

    @classmethod
    def set_gui_show_errors(cls, enabled: bool) -> None:
        cls.gui_show_errors = enabled

    @classmethod
    def attach_file(cls, path: str | Path) -> None:
        logger = cls.get_logger()
        if any(getattr(h, "baseFilename", None) == str(path) for h in logger.handlers):
            return
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%H:%M:%S"))
        logger.addHandler(file_handler)

    @classmethod
    def log_exception(cls, exc: BaseException, context: str = "") -> None:
        logger = cls.get_logger()
        if context:
            logger.error("%s: %s", context, exc)
        else:
            logger.error("%s", exc)
        logger.debug("%s", traceback.format_exc())
        cls.gui_show_errors = True

    @classmethod
    def clear_handlers(cls) -> None:
        logger = cls.get_logger()
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        cls._instance = None
