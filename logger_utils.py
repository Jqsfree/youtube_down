from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import itertools
import re
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gui_log import GuiLogHandler


MAX_LOG_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3


def redact(text: str) -> str:
    # Whole-message suppression handles multiline headers, quoted JSON and paths with spaces.
    sensitive = re.search(
        r"(?i)authorization|bearer|sessdata|bili_jct|password|passwd|secret|token|api[_-]?key|"
        r"cookie(?:s|file|_file)?[\"']?\s*[:=]|set-cookie|[/\\][^\r\n]*cookie|"
        r"cookie[^\r\n]*\.txt|http headers|<html|<!doctype|<body|^\s*\{|"
        r"(?:https?|socks[45]h?)://[^\r\n]*@", text,
    )
    if sensitive:
        return "[已隐藏凭据、Cookie 路径或响应内容]"
    text = re.sub(r"(?i)(?:https?|socks[45]h?)://\S+", "[URL]", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


class _SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Do not ask Formatter to render raw exception text, source lines or stack_info.
        clone = logging.makeLogRecord(record.__dict__.copy())
        clone.msg, clone.args = redact(record.getMessage()), ()
        limit = min(4096, max(16, (MAX_LOG_BYTES - 128) // 4))
        if len(clone.msg) > limit:
            clone.msg = clone.msg[:limit] + "…"
        if clone.exc_info:
            clone.msg += " exception=" + clone.exc_info[0].__name__
        clone.exc_info = clone.exc_text = clone.stack_info = None
        return super().format(clone)


class _SafeFileHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # Logging is best effort, including disk-full and rotation permission failures.
        pass


class _SafeStreamHandler(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        pass


def _formatter() -> logging.Formatter:
    return _SafeFormatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")


class AppLogger:
    """统一日志器，兼容 GUI 显示和文件输出。"""

    _instance: logging.Logger | None = None
    gui_show_errors = False
    _task_ids = itertools.count(1)

    @classmethod
    def get_logger(cls) -> logging.Logger:
        if cls._instance is None:
            logger = logging.getLogger("youtube_downloader")
            logger.setLevel(logging.DEBUG)
            logger.propagate = False

            if not logger.handlers:
                stream_handler = _SafeStreamHandler()
                stream_handler.setLevel(logging.INFO)
                stream_handler.setFormatter(_formatter())
                logger.addHandler(stream_handler)

            cls._instance = logger
        return cls._instance

    @classmethod
    def attach_gui(cls, slot: Any) -> GuiLogHandler:
        from gui_log import GuiLogHandler

        logger = cls.get_logger()
        for h in list(logger.handlers):
            if isinstance(h, GuiLogHandler):
                logger.removeHandler(h)
                h.close()
        handler = GuiLogHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(_formatter())
        handler.connect_to(slot)
        logger.addHandler(handler)
        return handler

    @classmethod
    def set_gui_show_errors(cls, enabled: bool) -> None:
        cls.gui_show_errors = enabled

    @classmethod
    def attach_file(cls, path: str | Path) -> bool:
        logger = cls.get_logger()
        try:
            path = Path(path).expanduser().resolve()
            if any(getattr(h, "baseFilename", None) == str(path) for h in logger.handlers):
                return True
            cls._detach_files(logger)
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = _SafeFileHandler(path, maxBytes=MAX_LOG_BYTES,
                                       backupCount=LOG_BACKUPS, encoding="utf-8")
        except OSError:
            cls._detach_files(logger)
            return False
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_formatter())
        logger.addHandler(handler)
        return True

    @staticmethod
    def _detach_files(logger: logging.Logger) -> None:
        for old in list(logger.handlers):
            if isinstance(old, logging.FileHandler):
                logger.removeHandler(old)
                try:
                    old.close()
                except OSError:
                    pass

    @classmethod
    def start_session(cls, client: str) -> None:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        try:
            version = (root / "VERSION").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            version = "unknown"
        cls.get_logger().info("session version=%s client=%s", version, client)

    @classmethod
    def log_exception(cls, exc: BaseException, context: str = "") -> None:
        cls.gui_show_errors = True
        logger = cls.get_logger()
        logger.error("%s exception=%s", context, type(exc).__name__)
        for frame in traceback.extract_tb(exc.__traceback__):
            logger.debug("frame=%s:%s function=%s", frame.filename, frame.lineno, frame.name)

    @classmethod
    def clear_handlers(cls) -> None:
        logger = cls.get_logger()
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except OSError:
                pass
        cls._instance = None
        cls.gui_show_errors = False
