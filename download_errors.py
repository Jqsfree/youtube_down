"""Error categories and classification shared by GUI/CLI, without UI dependencies."""
from __future__ import annotations
import re
from dataclasses import dataclass
import yt_dlp

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


_CATEGORY_RULES: list[tuple[str, bool, list[str]]] = [
    # (类别代码,  重试Cookie, 关键词列表)
    ("CANCELLED",          False, ["下载已取消", "cancel"]),
    ("ENVIRONMENT",        False, [
        "ffprobe 不可用",
        "ffmpeg not found", "ffprobe not found", "deno not found",
        "ffmpeg and ffprobe not found", "ffmpeg is not installed", "ffprobe is not installed",
        "deno is not installed", "no supported javascript runtime",
        "no such file or directory",
        "filenotfounderror",
    ]),
    ("BOT_VERIFICATION",   True,  [
        "sign in to confirm you're not a bot",
        "confirm you're not a bot",
        "not a bot",
        "captcha",
    ]),
    ("AUTH_REQUIRED",      True,  [
        "sign in to confirm your age",
        "login required",
        "sign in required",
        "members only",
        "this video may be inappropriate",
    ]),
    ("PRIVATE_VIDEO",      True,  ["private video", "this video is private"]),
    ("RATE_LIMIT",         False, [
        "http error 429", "too many requests",
        "rate-limited", "rate limited",
        "this content isn't available, try again later",
    ]),
    ("VIDEO_UNAVAILABLE",  False, [
        "video unavailable",
        "copyright",
        "removed",
        "deleted",
        "doesn't exist",
        "not found",
        "no video formats found",
        "requested format is not available",
        "this video is not available",
        "this video is unavailable",
        "this video has been removed",
    ]),
    ("NETWORK_ERROR",      False, [
        "timeout", "timed out",
        "ssl", "eof", "connection",
        "unable to download",
        "proxy",
    ]),
]


@dataclass
class ErrorCategory:
    """下载失败的错误分类结果。"""

    code: str           # SUCCESS / AUTH_REQUIRED / BOT_VERIFICATION / ...
    retry_cookie: bool  # 是否应重试 Cookie
    message: str        # 人类可读的错误消息


def clean_error(exc: Exception) -> str:
    """清理异常消息中的 ANSI 转义序列和控制字符。"""
    msg = str(exc)
    msg = _ANSI_RE.sub("", msg)
    return msg.strip()


def classify_error(exc: Exception) -> ErrorCategory:
    """根据异常内容对错误进行精细分类。

    用于决定是否需要 Cookie 重试，以及写入结果 CSV 的 error_category 字段。

    Args:
        exc: yt-dlp 或网络相关异常。

    Returns:
        ErrorCategory（code 为 SUCCESS / AUTH_REQUIRED / BOT_VERIFICATION /
        RATE_LIMIT / VIDEO_UNAVAILABLE / PRIVATE_VIDEO / NETWORK_ERROR / CANCELLED / UNKNOWN）。
    """
    # Use only known exception contracts; an opaque wrapper need not repeat HTTP text.
    from urllib.error import HTTPError
    from yt_dlp.networking.exceptions import HTTPError as EngineHTTPError
    msg = str(exc).lower()
    if any(word in msg for word in _CATEGORY_RULES[0][2]):
        return ErrorCategory("CANCELLED", False, str(exc))
    cause = exc
    if isinstance(exc, yt_dlp.utils.DownloadError):
        info = getattr(exc, "exc_info", None)
        if isinstance(info, tuple) and len(info) >= 2 and isinstance(info[1], Exception):
            cause = info[1]
    status = (cause.code if isinstance(cause, HTTPError) else
              cause.status if isinstance(cause, EngineHTTPError) else None)
    if status == 429:
        return ErrorCategory("RATE_LIMIT", False, str(exc))
    if status in (404, 410):
        return ErrorCategory("VIDEO_UNAVAILABLE", False, str(exc))

    for code, retry, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if kw in msg:
                return ErrorCategory(code, retry, str(exc))

    return ErrorCategory("UNKNOWN", False, str(exc))


def needs_cookie_retry(exc: Exception) -> bool:
    """快速判断错误是否需要 Cookie 重试（不返回完整分类）。"""
    return classify_error(exc).retry_cookie


def is_brutal_retryable(category: ErrorCategory) -> bool:
    """暴力模式下可对单条额外重试的错误类别。"""
    return category.code in {"NETWORK_ERROR", "RATE_LIMIT", "UNKNOWN"}


