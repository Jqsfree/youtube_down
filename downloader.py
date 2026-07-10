"""YouTube 下载核心封装 —— 对 yt-dlp 的唯一调用入口。

GUI 层不允许直接 import yt_dlp。所有视频信息获取和下载操作
必须通过本模块的 YoutubeDownloader 类完成。

批量下载采用两阶段策略：
  1. 默认无 Cookie（快速，覆盖绝大多数公开视频）
  2. 仅对需要登录/年龄验证的视频启用 Cookie 重试
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp

from logger_utils import AppLogger

_COOKIE_CONFIG_DIR = Path.home() / ".youtube_downloader"
_COOKIE_CONFIG = _COOKIE_CONFIG_DIR / "cookiefile.txt"
_PLATFORM_COOKIE_CONFIGS = {
    "youtube": _COOKIE_CONFIG_DIR / "youtube_cookiefile.txt",
    "bilibili": _COOKIE_CONFIG_DIR / "bilibili_cookiefile.txt",
}
_PLATFORM_COOKIE_DOMAINS = {
    "youtube": ("youtube.com", "youtu.be"),
    "bilibili": ("bilibili.com", "b23.tv"),
}
_PLATFORM_LABELS = {
    "youtube": "YouTube",
    "bilibili": "Bilibili",
    "generic": "Generic",
}

# Netscape cookies.txt 常见文件头
_NETSCAPE_MARKERS = ("# netscape", "# http cookie file")

# CSV 列名别名（含中文表头）
_CSV_COLUMN_ALIASES: tuple[str, ...] = (
    "video_id", "videoid", "video-id", "media_id", "source", "id",
    "csvid", "csv_id", "video", "link",
    "youtube_id", "youtubeid", "bvid", "bv", "aid", "av",
    "url", "video_url", "source_url", "watch", "uri",
    "视频链接", "视频地址", "视频id", "视频_id", "视频", "链接", "地址",
    "bv号", "bv_id", "网址", "url地址", "播放链接", "分享链接", "视频url",
)

_CSV_ENCODINGS: tuple[str, ...] = (
    "utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be",
    "gb18030", "gbk", "cp936", "latin-1",
)

_CSV_DELIMITERS: tuple[str, ...] = (",", ";", "\t", "|")

_PROJECT_ROOT = Path(__file__).resolve().parent

# 工具名 → resources/ 下的子目录（ffmpeg 与 ffprobe 同目录）
_RESOURCE_DIRS: dict[str, list[str]] = {
    "ffmpeg": ["ffmpeg"],
    "ffprobe": ["ffmpeg"],
    "deno": ["deno"],
}


def _tool_filenames(name: str) -> list[str]:
    if os.name == "nt":
        return [f"{name}.exe", name]
    return [name]


def _resolve_tool(name: str) -> str | None:
    """解析可执行工具绝对路径：MEIPASS → resources/ → PATH。"""
    for filename in _tool_filenames(name):
        if getattr(sys, "frozen", False):
            bundled = os.path.join(sys._MEIPASS, filename)  # noqa: SLF001
            if os.path.isfile(bundled):
                return bundled

        for subdir in _RESOURCE_DIRS.get(name, [name]):
            local = _PROJECT_ROOT / "resources" / subdir / filename
            if local.is_file():
                return str(local)

    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        found = shutil.which(f"{name}.exe")
        if found:
            return found
    return None


def _find_tool(name: str) -> str:
    """返回可执行文件路径；未找到时回退为 bare name（供 PATH 查找）。"""
    resolved = _resolve_tool(name)
    return resolved if resolved else name


def _configure_js_runtimes(opts: dict[str, Any]) -> None:
    """为 YouTube JS 挑战配置 deno/node 路径（Cookie 模式尤其需要）。"""
    runtimes: dict[str, dict[str, str]] = {}
    for name in ("deno", "node"):
        path = _resolve_tool(name)
        if path:
            runtimes[name] = {"path": path}
    if runtimes:
        opts["js_runtimes"] = runtimes

# 匹配 ANSI 转义序列（颜色码等），用于清理错误消息
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _QuietLogger:
    """禁止 yt-dlp 向 stderr 输出任何日志。"""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


# ------------------------------------------------------------------
# 环境检测
# ------------------------------------------------------------------

@dataclass
class EnvItem:
    name: str
    status: str       # "ok" | "error" | "warning"
    version: str
    message: str
    fatal: bool = False  # 致命缺失 → 不允许跳过
    install_kind: str = ""  # "pip" | "binary" | ""


@dataclass(frozen=True)
class MediaInput:
    """Normalized media source resolved from a user-provided ID or URL."""

    platform: str
    original: str
    media_id: str
    url: str

    @property
    def label(self) -> str:
        return _PLATFORM_LABELS.get(self.platform, self.platform)


def check_environment() -> tuple[bool, list[EnvItem]]:
    """启动时检查所有依赖。返回 (all_ok, items)。"""
    items: list[EnvItem] = []

    # yt-dlp (致命)
    try:
        import yt_dlp as _ydl
        ver = str(_ydl.version.__version__) if hasattr(_ydl, 'version') else "?"
        items.append(EnvItem("yt-dlp", "ok", ver, "", fatal=True))
    except ImportError:
        items.append(EnvItem(
            "yt-dlp", "error", "", "未安装: pip install yt-dlp",
            fatal=True, install_kind="pip",
        ))

    # yt-dlp-ejs
    try:
        import yt_dlp_ejs  # noqa: F811
        ejs_ver = getattr(yt_dlp_ejs, "__version__", "?")
        items.append(EnvItem("yt-dlp-ejs", "ok", str(ejs_ver), ""))
    except ImportError:
        items.append(EnvItem(
            "yt-dlp-ejs", "error", "",
            "未安装: pip install yt-dlp-ejs（Cookie 模式下必需）",
            install_kind="pip",
        ))

    # ffmpeg (致命)
    ffmpeg_path = _resolve_tool("ffmpeg")
    if ffmpeg_path:
        try:
            r = subprocess.run(
                [ffmpeg_path, "-version"], capture_output=True, text=True, timeout=5
            )
            ver_line = r.stdout.split("\n")[0] if r.stdout else "?"
            items.append(EnvItem("ffmpeg", "ok", ver_line, "", fatal=True))
        except Exception:
            items.append(EnvItem("ffmpeg", "ok", "?", "", fatal=True))
    else:
        hint = (
            "未找到 ffmpeg。\n"
            "Windows: winget install Gyan.FFmpeg\n"
            "或运行: powershell -ExecutionPolicy Bypass -File scripts\\setup_dev.ps1\n"
            "或将 ffmpeg.exe 放入 resources/ffmpeg/"
        )
        items.append(EnvItem(
            "ffmpeg", "error", "", hint, fatal=True, install_kind="binary",
        ))

    # ffprobe
    if _resolve_tool("ffprobe"):
        items.append(EnvItem("ffprobe", "ok", "", ""))
    else:
        items.append(EnvItem(
            "ffprobe", "warning", "", "未找到 ffprobe，无法校验下载文件",
            install_kind="binary",
        ))

    # deno
    deno_path = _resolve_tool("deno")
    if deno_path:
        try:
            r = subprocess.run(
                [deno_path, "--version"], capture_output=True, text=True, timeout=5
            )
            ver_line = r.stdout.split("\n")[0] if r.stdout else "?"
            items.append(EnvItem("deno", "ok", ver_line, ""))
        except Exception:
            items.append(EnvItem("deno", "warning", "", "deno 存在但无法获取版本"))
    else:
        items.append(EnvItem("deno", "warning", "", "未找到 deno（Cookie 模式可能需要）"))

    # 浏览器 Cookie
    browser_info = YoutubeDownloader.detect_browser()
    if browser_info:
        name, profile = browser_info
        items.append(EnvItem(f"Cookie ({name})", "ok", f"profile={profile}", ""))
    else:
        items.append(EnvItem("Cookie", "warning", "", "未检测到浏览器 Cookie 目录"))

    all_ok = all(i.status != "error" for i in items)
    return all_ok, items


# ------------------------------------------------------------------
# 错误分类
# ------------------------------------------------------------------
# 按优先级从高到低排列，每个分类独立定义关键词和重试策略。

# 注意：BOT_VERIFICATION 和 AUTH_REQUIRED 都返回 retry_cookie=True，
# 但分为两个类别以便结果 CSV 区分统计。
_CATEGORY_RULES: list[tuple[str, bool, list[str]]] = [
    # (类别代码,  重试Cookie, 关键词列表)
    ("CANCELLED",          False, ["下载已取消", "cancel"]),
    ("BOT_VERIFICATION",   True,  [
        "not a bot",
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
        "this video has been removed",
    ]),
    ("NETWORK_ERROR",      False, [
        "timeout", "timed out",
        "ssl", "eof", "connection",
        "unable to download",
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
    msg = str(exc).lower()

    for code, retry, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if kw in msg:
                return ErrorCategory(code, retry, str(exc))

    return ErrorCategory("UNKNOWN", False, str(exc))


def needs_cookie_retry(exc: Exception) -> bool:
    """快速判断错误是否需要 Cookie 重试（不返回完整分类）。"""
    return classify_error(exc).retry_cookie


class YoutubeDownloader:
    """封装所有 yt-dlp 交互。

    用法::

        dl = YoutubeDownloader()
        info = dl.get_info("dQw4w9WgXcQ")
        dl.download("dQw4w9WgXcQ", "22", Path("./out"))
    """

    def __init__(
        self,
        cookies_from_browser: str | None = None,
        cookiefile: str | Path | None = None,
    ) -> None:
        """初始化下载器。

        Args:
            cookies_from_browser: 浏览器名称:profile 格式。
                None 时自动检测，'' 时不使用 cookie。
            cookiefile: Netscape 格式 cookie 文件路径。
                比 cookiesfrombrowser 更可靠，优先使用。
        """
        self._cancelled: bool = False
        self._cancel_lock = threading.Lock()
        self._yt_dlp_lock = threading.RLock()
        self._cookiefile_path: Path | None = None
        self._cookiefile_platform: str | None = None
        self._platform_cookiefiles: dict[str, Path] = {}
        self._cookies_spec: str | None = cookies_from_browser
        self._browser_cookie_broken: bool = False  # 浏览器 Cookie 不可用（如 Windows Chrome 锁库）
        if cookiefile:
            self.set_cookiefile(cookiefile, persist=False)
        elif cookies_from_browser == "":
            self._cookies_spec = None
        elif cookies_from_browser is None:
            self._platform_cookiefiles = self._load_saved_cookiefiles()
            saved = self._platform_cookiefiles.get("youtube") or self._load_saved_cookiefile()
            if saved:
                self.set_cookiefile(saved, persist=False)
            else:
                info = self.detect_browser()
                self._cookies_spec = f"{info[0]}:{info[1]}" if info else None
        else:
            self._cookies_spec = cookies_from_browser

    # ------------------------------------------------------------------
    # 浏览器检测
    # ------------------------------------------------------------------

    @staticmethod
    def parse_input(value: str) -> MediaInput:
        """解析 YouTube / Bilibili ID 或 URL，返回 yt-dlp 可直接使用的 URL。"""
        text = (value or "").strip()
        if not text:
            raise ValueError("请输入视频链接或 ID")
        text = text.replace("\\", "/")
        normalized = text
        if re.match(r"^(www\.|m\.|b23\.tv|bilibili\.com|youtube\.com|youtu\.be)", normalized, re.I):
            normalized = f"https://{normalized}"

        parsed = urlparse(normalized)
        host = parsed.netloc.lower()
        path = parsed.path or ""

        if host.endswith("youtu.be"):
            media_id = path.strip("/").split("/")[0]
            if media_id:
                return MediaInput("youtube", text, media_id, f"https://www.youtube.com/watch?v={media_id}")
        if "youtube.com" in host:
            query = parse_qs(parsed.query)
            media_id = (query.get("v") or [""])[0]
            if not media_id:
                match = re.search(r"/(?:shorts|embed|v)/([A-Za-z0-9_-]{11})", path)
                media_id = match.group(1) if match else ""
            if media_id:
                return MediaInput("youtube", text, media_id, f"https://www.youtube.com/watch?v={media_id}")

        if "bilibili.com" in host or host.endswith("b23.tv"):
            media_id = YoutubeDownloader._extract_bilibili_id(normalized) or normalized
            return MediaInput("bilibili", text, media_id, normalized)

        bili_id = YoutubeDownloader._extract_bilibili_id(text)
        if bili_id:
            url_id = bili_id
            return MediaInput("bilibili", text, bili_id, f"https://www.bilibili.com/video/{url_id}")

        yt_match = re.search(r"^([A-Za-z0-9_-]{11})$", text)
        if yt_match:
            media_id = yt_match.group(1)
            return MediaInput("youtube", text, media_id, f"https://www.youtube.com/watch?v={media_id}")

        if parsed.scheme in {"http", "https"} and host:
            return MediaInput("generic", text, normalized, normalized)

        # Preserve historical behavior: unknown bare values are treated as YouTube IDs.
        return MediaInput("youtube", text, text, f"https://www.youtube.com/watch?v={text}")

    @staticmethod
    def _extract_bilibili_id(value: str) -> str:
        bv_match = re.search(r"\b(BV[0-9A-Za-z]{10})\b", value, flags=re.I)
        if bv_match:
            raw = bv_match.group(1)
            return "BV" + raw[2:]
        av_match = re.search(r"\bav(\d+)\b", value, flags=re.I)
        if av_match:
            return f"av{av_match.group(1)}"
        return ""

    @staticmethod
    def _platform_label(platform: str) -> str:
        return _PLATFORM_LABELS.get(platform, platform)

    @staticmethod
    def _cookie_platform_from_text(text: str) -> str | None:
        lower = text.lower()
        for platform, domains in _PLATFORM_COOKIE_DOMAINS.items():
            if any(domain in lower for domain in domains):
                return platform
        return None

    @staticmethod
    def detect_browser() -> tuple[str, str] | None:
        """自动检测系统中可用的浏览器及 Profile。

        按优先级：Chrome(Default→Profile*) → Firefox → Edge → ...

        Returns:
            (browser_name, profile_name) 或 None。
        """
        if os.name == "nt":
            localappdata = os.environ.get("LOCALAPPDATA", "")
            appdata = os.environ.get("APPDATA", "")

            # Chrome — 多 Profile
            chrome_dir = os.path.join(localappdata, "Google", "Chrome", "User Data")
            if os.path.isdir(chrome_dir):
                profiles = ["Default"] + sorted(
                    d for d in os.listdir(chrome_dir)
                    if d.startswith("Profile ") and os.path.isdir(os.path.join(chrome_dir, d))
                )
                for profile in profiles:
                    if os.path.isfile(os.path.join(chrome_dir, profile, "Cookies")):
                        return ("chrome", profile)

            # Firefox
            firefox_dir = os.path.join(appdata, "Mozilla", "Firefox", "Profiles")
            if os.path.isdir(firefox_dir):
                return ("firefox", "default")

            # Edge
            edge_dir = os.path.join(localappdata, "Microsoft", "Edge", "User Data")
            if os.path.isdir(edge_dir):
                return ("edge", "Default")

            return None

        # Linux / macOS
        home = os.path.expanduser("~")

        # Chrome
        chrome_dir = os.path.join(home, ".config/google-chrome")
        if os.path.isdir(chrome_dir):
            profiles = ["Default"] + sorted(
                d for d in os.listdir(chrome_dir)
                if d.startswith("Profile ") and os.path.isdir(os.path.join(chrome_dir, d))
            )
            for profile in profiles:
                cookie_file = os.path.join(chrome_dir, profile, "Cookies")
                if os.path.isfile(cookie_file):
                    return ("chrome", profile)

        # Firefox
        firefox_dir = os.path.join(home, ".mozilla/firefox")
        if os.path.isdir(firefox_dir):
            return ("firefox", "default")

        # Edge
        edge_dir = os.path.join(home, ".config/microsoft-edge")
        if os.path.isdir(edge_dir):
            return ("edge", "Default")

        # Chromium
        chromium_dir = os.path.join(home, ".config/chromium")
        if os.path.isdir(chromium_dir):
            return ("chromium", "Default")

        # Brave
        brave_dir = os.path.join(home, ".config/BraveSoftware/Brave-Browser")
        if os.path.isdir(brave_dir):
            return ("brave", "Default")

        # Opera
        opera_dir = os.path.join(home, ".config/opera")
        if os.path.isdir(opera_dir):
            return ("opera", "Default")

        return None

    # ------------------------------------------------------------------
    # 内部：构建 yt-dlp 选项
    # ------------------------------------------------------------------

    def prefers_cookies_for_fetch(self) -> bool:
        """是否应在「获取信息」时尝试 Cookie（有 cookie 文件或可用浏览器源）。"""
        if self._cookiefile_path or self._platform_cookiefiles:
            return True
        if self._browser_cookie_broken:
            return False
        # Windows 上 Chrome/Edge Cookie 数据库常被锁定，默认不自动读取浏览器
        if os.name == "nt":
            return False
        return bool(self._cookies_spec)

    def _has_explicit_cookie(
        self,
        media: MediaInput | None = None,
        cookiefile_override: str | Path | None = None,
        cookies_from_browser_override: str | None = None,
    ) -> bool:
        if cookiefile_override is not None or cookies_from_browser_override:
            return True
        if self._cookiefile_for_platform(media.platform if media else None):
            return True
        return False

    @staticmethod
    def build_format_selector(
        format_id: str,
        *,
        min_height: int = 0,
        needs_audio_merge: bool = True,
    ) -> str:
        """构建 yt-dlp format 字符串，避免无约束的 /best 回退到低画质。"""
        height = f"[height>={min_height}]" if min_height > 0 else ""
        if format_id == "best":
            if min_height > 0:
                return (
                    f"bestvideo[height>={min_height}]+bestaudio/"
                    f"bestvideo[height>={min_height}]+bestaudio"
                )
            return "bestvideo+bestaudio/bestvideo+bestaudio"
        if needs_audio_merge:
            primary = f"{format_id}+bestaudio"
            if min_height > 0:
                return f"{primary}/bestvideo{height}+bestaudio"
            return primary
        return format_id

    def _make_opts(
        self,
        use_cookies: bool = True,
        media: MediaInput | None = None,
        cookiefile_override: str | Path | None = None,
        cookies_from_browser_override: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """构建 yt-dlp 通用选项。

        Args:
            use_cookies: 是否启用浏览器 Cookie。默认 True。
        """
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "logger": _QuietLogger(),
            # 网络韧性
            "extractor_retries": 10,
            "retries": 10,
            "socket_timeout": 20,
            # 请求节奏控制（yt-dlp 官方建议：访客 ~300/h，登录 ~2000/h）
            "sleep_interval": 3,
            "max_sleep_interval": 8,
            "sleep_interval_requests": 1.5,
            # 内部重试退避
            "retry_sleep_functions": {
                "http": lambda n: min(4 * (2 ** n), 60),
                "fragment": lambda n: min(2 * (2 ** n), 30),
                "extractor": lambda n: min(3 * (2 ** n), 30),
            },
            # 限速检测
            "throttledratelimit": 100 * 1024,
            # 版本提醒
            "warn_when_outdated": True,
        }
        ffmpeg_path = _resolve_tool("ffmpeg")
        if ffmpeg_path:
            opts["ffmpeg_location"] = str(Path(ffmpeg_path).parent)
        _configure_js_runtimes(opts)
        if media and media.platform == "bilibili":
            opts["http_headers"] = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            }
        cookiefile = (
            Path(cookiefile_override).expanduser().resolve()
            if cookiefile_override else self._cookiefile_for_platform(media.platform if media else None)
        )
        cookies_spec = cookies_from_browser_override or self._cookies_spec
        if use_cookies and (cookies_spec or cookiefile):
            if cookiefile:
                opts["cookiefile"] = str(cookiefile)
            elif cookies_spec:
                parts = cookies_spec.split(":")
                opts["cookiesfrombrowser"] = tuple(parts)  # type: ignore[assignment]
        opts.update(extra)  # extra 中的 extractor_args 会覆盖默认值
        return opts

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_info(
        self,
        video_id: str,
        use_cookies: bool = True,
        cookiefile_override: str | Path | None = None,
        cookies_from_browser_override: str | None = None,
    ) -> dict[str, Any]:
        """获取视频元信息和可用格式列表。

        Args:
            video_id: YouTube/Bilibili ID 或完整 URL。
            use_cookies: 是否使用浏览器 Cookie。默认 False。

        Returns:
            yt-dlp info dict（title, uploader, duration, formats 等）。

        Raises:
            yt_dlp.utils.DownloadError: yt-dlp 内部错误（由调用方分类处理）。
        """
        media = self.parse_input(video_id)
        url = media.url
        has_explicit = self._has_explicit_cookie(
            media, cookiefile_override, cookies_from_browser_override
        )
        # 仅禁用浏览器 Cookie；显式 cookie 文件/覆盖不受 _browser_cookie_broken 影响
        if self._browser_cookie_broken and not has_explicit:
            use_cookies = False
        # Cookie 模式失败时自动回退无 Cookie（Chrome 锁定等场景；cookie 文件不回退）
        last_err = None
        has_cookiefile_override = cookiefile_override is not None
        has_browser_override = bool(cookies_from_browser_override)
        platform_cookiefile = self._cookiefile_for_platform(media.platform)
        use_browser = (
            use_cookies
            and bool(self._cookies_spec)
            and platform_cookiefile is None
            and not has_cookiefile_override
            and not has_browser_override
            and not self._browser_cookie_broken
        )
        attempts = [True, False] if use_browser else [use_cookies]
        with self._yt_dlp_lock:
            for attempt in attempts:
                try:
                    opts = self._make_opts(
                        use_cookies=attempt,
                        media=media,
                        cookiefile_override=cookiefile_override,
                        cookies_from_browser_override=cookies_from_browser_override,
                    )
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if info and len(info.get("formats", [])) > 0:
                            info.setdefault("_platform", media.platform)
                            info.setdefault("_source_url", media.url)
                            info.setdefault("_media_id", media.media_id)
                            if attempt != use_cookies:  # 浏览器 Cookie 回退成功 → 标记
                                self._browser_cookie_broken = True
                            return info
                        if info:
                            last_err = yt_dlp.utils.DownloadError(
                                f"{media.label} 返回了视频信息但格式列表为空"
                            )
                except Exception as exc:
                    if "copy chrome cookie" in str(exc).lower():
                        self._browser_cookie_broken = True  # 锁库，后续跳过浏览器 Cookie
                    last_err = exc
        if last_err:
            detail = clean_error(last_err)
            raise yt_dlp.utils.DownloadError(
                f"No formats available for: {video_id} ({detail})"
            ) from last_err
        raise yt_dlp.utils.DownloadError(f"No formats available for: {video_id}")

    def list_formats(
        self,
        video_id: str = "",
        info: dict[str, Any] | None = None,
        min_height: int | None = None,
    ) -> list[dict[str, Any]]:
        """获取所有可用格式（过滤后）。

        Args:
            video_id: YouTube/Bilibili ID 或 URL。如提供 info 则可省略。
            info: 预先获取的 info dict（避免重复请求）。

        Returns:
            格式列表，每项含 format_id, resolution, codec, container,
            fps, filesize, filesize_str, type, note。
        """
        if info is None:
            if not video_id:
                raise ValueError("必须提供 video_id 或 info 参数")
            info = self.get_info(video_id)

        raw_formats: list[dict[str, Any]] = info.get("formats", [])

        def _height(resolution: str) -> int:
            try:
                return int(resolution.split("x")[-1].replace("p", ""))
            except (ValueError, IndexError):
                return 0

        results: list[dict[str, Any]] = []
        _VIDEO_EXTS = {"mp4", "m4a", "webm", "flv", "mkv", "mov", "m4s", "fmp4"}
        for fmt in raw_formats:
            if fmt.get("ext") not in _VIDEO_EXTS:
                continue
            resolution = fmt.get("resolution") or ""
            if resolution == "audio only":
                continue
            height = fmt.get("height") or 0
            if not resolution and height:
                resolution = f"{fmt.get('width', '?')}x{height}"
            elif not resolution:
                continue

            fmt_type = self._format_type(fmt)
            if fmt_type == "Audio":
                continue

            if min_height is not None:
                res_height = height or _height(resolution)
                if res_height < min_height:
                    continue

            results.append({
                "format_id": fmt.get("format_id", ""),
                "resolution": resolution,
                "codec": fmt.get("vcodec") or fmt.get("acodec") or "—",
                "container": fmt.get("ext") or "—",
                "fps": fmt.get("fps"),
                "filesize": fmt.get("filesize"),
                "filesize_str": self._human_size(fmt.get("filesize")),
                "type": self._format_type(fmt),
                "note": self._format_note(fmt),
            })

        # 每档分辨率只保留最优版本：Video Only > Video+Audio，大文件 > 小文件
        return self._dedup_formats(results)

    @staticmethod
    def _has_video_stream(path: Path) -> bool:
        """确认输出文件包含视频轨，避免仅音频文件被当成成功。"""
        result = subprocess.run(
            [
                _find_tool("ffprobe"), "-v", "quiet", "-select_streams", "v:0",
                "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0 and result.stdout.strip() == "video"

    def download(
        self,
        video_id: str,
        format_id: str,
        output_dir: Path,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        use_cookies: bool = True,
        needs_audio_merge: bool = True,
        min_height: int = 0,
        cookiefile_override: str | Path | None = None,
        cookies_from_browser_override: str | None = None,
    ) -> Path:
        """下载指定格式的视频。

        Args:
            video_id: YouTube/Bilibili ID 或完整 URL。
            format_id: yt-dlp format ID。
            output_dir: 输出目录。
            progress_callback: 进度回调。
            use_cookies: 是否使用浏览器 Cookie。
            needs_audio_merge: Video Only 格式才需要合并音频，
                Video+Audio 格式传 False 避免双音轨。
        """
        with self._cancel_lock:
            self._cancelled = False
        output_dir.mkdir(parents=True, exist_ok=True)

        output_template = str(output_dir / "%(id)s.%(ext)s")
        media = self.parse_input(video_id)
        url = media.url

        def _progress_hook(d: dict[str, Any]) -> None:
            if progress_callback:
                progress_callback(d)
            with self._cancel_lock:
                cancelled = self._cancelled
            if cancelled:
                raise yt_dlp.utils.DownloadError("下载已取消")

        fmt_str = self.build_format_selector(
            format_id,
            min_height=min_height,
            needs_audio_merge=needs_audio_merge,
        )
        opts = self._make_opts(
            use_cookies=use_cookies,
            media=media,
            cookiefile_override=cookiefile_override,
            cookies_from_browser_override=cookies_from_browser_override,
            format=fmt_str,
            outtmpl=output_template,
            progress_hooks=[_progress_hook],
            merge_output_format="mp4",
            concurrent_fragment_downloads=8,
            buffersize=4 * 1024 * 1024,
            file_access_retries=5,
            fragment_retries=5,
            keep_fragments=False,
            prefer_ffmpeg=True,
        )

        with self._yt_dlp_lock:
            with yt_dlp.YoutubeDL(opts) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                except Exception as exc:
                    AppLogger.log_exception(exc, f"下载失败: {video_id}")
                    raise
                if info is None:
                    raise yt_dlp.utils.DownloadError("下载失败：无法获取视频信息")
                filename: str = ydl.prepare_filename(info)
                path = Path(filename)
                if not path.exists():
                    stem = path.stem
                    candidates = list(output_dir.glob(f"{stem}.*"))
                    _VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".flv"}
                    video_candidates = [
                        c for c in candidates if c.suffix.lower() in _VIDEO_EXTS
                    ]
                    if video_candidates:
                        path = max(video_candidates, key=lambda p: p.stat().st_size)
                    elif candidates:
                        path = candidates[0]
                    else:
                        raise yt_dlp.utils.DownloadError(
                            f"下载完成但找不到输出文件: {path}"
                        )
                # 校验文件大小合理（> 1KB）
                if path.stat().st_size <= 1024:
                    raise yt_dlp.utils.DownloadError(
                        f"下载文件过小 ({path.stat().st_size} bytes): {path}"
                    )

                if not self._has_video_stream(path):
                    raise yt_dlp.utils.DownloadError(
                        f"下载结果只有音频或缺少视频轨: {path}"
                    )

                # 校验文件是有效媒体（ffprobe 解析 + 时长比对）
                ffprobe_result = subprocess.run(
                    [_find_tool("ffprobe"), "-v", "quiet", "-show_entries",
                     "format=duration", "-of", "csv=p=0", str(path)],
                    capture_output=True, text=True, timeout=30,
                )
                if ffprobe_result.returncode != 0 or not ffprobe_result.stdout.strip():
                    raise yt_dlp.utils.DownloadError(
                        f"下载文件无法解析为有效媒体: {path}"
                    )
                actual_duration = float(ffprobe_result.stdout.strip())
                expected_duration = float(info.get("duration") or 0)
                if (
                    expected_duration > 0
                    and abs(actual_duration - expected_duration) / expected_duration > 0.05
                ):
                    raise yt_dlp.utils.DownloadError(
                        f"下载文件时长异常: 期望 {expected_duration:.0f}s,"
                        f" 实际 {actual_duration:.0f}s (可能是合并中断)"
                    )

                return path

    @staticmethod
    def cleanup_partial_files(output_dir: Path) -> None:
        """清理下载中残留的分片/临时文件，避免占满磁盘。"""
        if not output_dir.exists():
            return
        fragment_pattern = re.compile(r".*\.f\d+\..+")
        for path in output_dir.iterdir():
            if not path.is_file():
                continue
            name = path.name.lower()
            if name.endswith(".part") or fragment_pattern.match(name):
                path.unlink(missing_ok=True)

    def cancel(self) -> None:
        """取消当前下载（线程安全）。"""
        with self._cancel_lock:
            self._cancelled = True

    # ------------------------------------------------------------------
    # Cookie 管理
    # ------------------------------------------------------------------

    @staticmethod
    def _load_saved_cookiefile() -> Path | None:
        if not _COOKIE_CONFIG.is_file():
            return None
        try:
            path = Path(_COOKIE_CONFIG.read_text(encoding="utf-8").strip())
        except OSError:
            return None
        return path if path.is_file() else None

    @staticmethod
    def _load_saved_cookiefiles() -> dict[str, Path]:
        result: dict[str, Path] = {}
        for platform, config_path in _PLATFORM_COOKIE_CONFIGS.items():
            if not config_path.is_file():
                continue
            try:
                path = Path(config_path.read_text(encoding="utf-8").strip())
            except OSError:
                continue
            if path.is_file():
                result[platform] = path
        legacy = YoutubeDownloader._load_saved_cookiefile()
        if legacy and "youtube" not in result:
            result["youtube"] = legacy
        return result

    @staticmethod
    def _persist_cookiefile(path: Path | None) -> None:
        try:
            if path is None:
                if _COOKIE_CONFIG.is_file():
                    _COOKIE_CONFIG.unlink()
                return
            _COOKIE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            _COOKIE_CONFIG.write_text(str(path.resolve()), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _persist_platform_cookiefile(platform: str, path: Path | None) -> None:
        config_path = _PLATFORM_COOKIE_CONFIGS.get(platform)
        if config_path is None:
            return
        try:
            if path is None:
                if config_path.is_file():
                    config_path.unlink()
                return
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(str(path.resolve()), encoding="utf-8")
        except OSError:
            pass

    def _cookiefile_for_platform(self, platform: str | None) -> Path | None:
        if platform and platform in self._platform_cookiefiles:
            return self._platform_cookiefiles[platform]
        if (
            self._cookiefile_path
            and (self._cookiefile_platform is None or platform is None or self._cookiefile_platform == platform)
        ):
            return self._cookiefile_path
        return None

    def has_cookie_for_source(self, source: str) -> bool:
        """Return whether a cookie source is available for a media input."""
        try:
            media = self.parse_input(source)
        except ValueError:
            media = None
        if self._cookiefile_for_platform(media.platform if media else None):
            return True
        return bool(self._cookies_spec)

    def cookie_source(self) -> str:
        """当前 Cookie 来源描述（用于 UI 显示）。"""
        if self._cookiefile_path:
            prefix = self._platform_label(self._cookiefile_platform) if self._cookiefile_platform else "file"
            return f"{prefix}:{self._cookiefile_path.name}"
        if self._cookies_spec:
            return f"browser:{self._cookies_spec}"
        return ""

    @staticmethod
    def is_netscape_cookie_file(path: str | Path) -> bool:
        """快速判断是否为 Netscape cookies.txt（本地校验，不访问网络）。"""
        cookie_path = Path(path)
        if not cookie_path.is_file() or cookie_path.stat().st_size == 0:
            return False
        try:
            head = cookie_path.read_text(encoding="utf-8", errors="replace")[:4096].lower()
        except OSError:
            return False
        return any(m in head for m in _NETSCAPE_MARKERS) or "\t" in head

    @staticmethod
    def validate_cookie_file(path: str | Path, platform: str | None = None) -> tuple[bool, str]:
        """本地校验 cookie 文件格式（不访问网络）。"""
        cookie_path = Path(path).expanduser()
        if not cookie_path.is_file():
            return False, f"文件不存在: {cookie_path}"
        if cookie_path.stat().st_size == 0:
            return False, "文件为空"
        try:
            text = cookie_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return False, f"无法读取文件: {exc}"
        lower = text.lower()
        if not YoutubeDownloader.is_netscape_cookie_file(cookie_path):
            return False, (
                "不是 Netscape cookies.txt 格式。\n"
                "请使用扩展「Get cookies.txt LOCALLY」导出，勿使用 JSON 格式。"
            )
        detected = YoutubeDownloader._cookie_platform_from_text(lower)
        if platform:
            expected_domains = _PLATFORM_COOKIE_DOMAINS.get(platform, ())
            if not any(domain in lower for domain in expected_domains):
                label = YoutubeDownloader._platform_label(platform)
                domains = " / ".join(expected_domains)
                return False, f"文件中未找到 {domains} 的 Cookie（请从已登录 {label} 的浏览器导出）"
            detected = platform
        elif detected is None:
            return False, "文件中未找到 youtube.com 或 bilibili.com 的 Cookie"
        msg = f"格式校验通过 ({YoutubeDownloader._platform_label(detected)})"
        if detected == "bilibili" and "sessdata" not in lower:
            msg += "；未检测到 SESSDATA，可能不是登录 Cookie"
        return True, msg

    def set_cookiefile(self, path: str | Path, *, persist: bool = True) -> None:
        """设置 Netscape 格式 cookie 文件（优先于浏览器 Cookie）。"""
        cookie_path = Path(path).expanduser().resolve()
        if not cookie_path.is_file():
            raise ValueError(f"文件不存在: {cookie_path}")
        try:
            text = cookie_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError(f"无法读取文件: {exc}") from exc
        platform = self._cookie_platform_from_text(text)
        ok, msg = self.validate_cookie_file(cookie_path, platform=platform)
        if not ok:
            raise ValueError(msg)
        self._cookiefile_path = cookie_path
        self._cookiefile_platform = platform
        if platform:
            self._platform_cookiefiles[platform] = cookie_path
        self._cookies_spec = None
        self._browser_cookie_broken = False
        if persist:
            if platform:
                self._persist_platform_cookiefile(platform, cookie_path)
            # Keep legacy config for YouTube so existing installs continue to work.
            if platform in (None, "youtube"):
                self._persist_cookiefile(cookie_path)

    def clear_cookiefile(self, *, persist: bool = True) -> None:
        """清除 cookie 文件，恢复自动检测浏览器 Cookie。"""
        old_platform = self._cookiefile_platform
        self._cookiefile_path = None
        self._cookiefile_platform = None
        self._browser_cookie_broken = False
        if persist:
            if old_platform:
                self._platform_cookiefiles.pop(old_platform, None)
                self._persist_platform_cookiefile(old_platform, None)
            self._persist_cookiefile(None)
        info = self.detect_browser()
        self._cookies_spec = f"{info[0]}:{info[1]}" if info else None

    def validate_cookies(self) -> tuple[bool, str]:
        """联网探测 Cookie 是否能让当前平台返回格式列表。"""
        if not self._cookiefile_path and not self._cookies_spec:
            return False, "未配置 Cookie"

        using_file = bool(self._cookiefile_path)
        platform = self._cookiefile_platform or "youtube"
        label = self._platform_label(platform)
        if platform == "youtube" and using_file and not _resolve_tool("deno") and not _resolve_tool("node"):
            return False, (
                "未检测到 deno 或 node.js。\n"
                "Cookie 模式需要 JS 运行时才能通过 YouTube 验证。\n"
                "请运行: powershell -ExecutionPolicy Bypass -File scripts\\setup_dev.ps1\n"
                "或: winget install Denoland.Deno\n"
                "安装后重启应用再试。"
            )

        test_source = "dQw4w9WgXcQ" if platform == "youtube" else "BV1GJ411x7h7"
        cookie_err: Exception | None = None
        try:
            info = self.get_info(test_source, use_cookies=True)
            n = len(info.get("formats", []))
            return True, f"Cookie 验证通过 ({n} formats, {self.cookie_source()})"
        except Exception as exc:
            cookie_err = exc

        # 对比：无 Cookie 是否能获取格式
        try:
            info_no = self.get_info(test_source, use_cookies=False)
            if len(info_no.get("formats", [])) > 0:
                detail = clean_error(cookie_err) if cookie_err else "未知错误"
                return False, (
                    f"Cookie 未能改善访问（不使用 Cookie 反而能获取格式）。\n"
                    f"可能 cookies.txt 已过期或导出不完整。\n"
                    f"详情: {detail}\n"
                    f"请重新登录 {label} 后导出新的 cookies.txt。"
                )
        except Exception:
            pass

        detail = clean_error(cookie_err) if cookie_err else f"{label} 未返回格式"
        hints: list[str] = []
        if using_file:
            hints.append(f"确认 cookies.txt 来自已登录 {label} 的浏览器（扩展 Get cookies.txt LOCALLY）")
        if platform == "bilibili" and using_file:
            hints.append("Bilibili 高画质通常需要 cookies.txt 中包含 SESSDATA")
        if "bot" in detail.lower() or "sign in" in detail.lower():
            hints.append(f"{label} 仍要求验证，请重新导出 Cookie 或换网络/IP 后重试")
        if _resolve_tool("deno") or _resolve_tool("node"):
            hints.append("已检测到 JS 运行时，若仍失败多为 Cookie 过期")
        hint_text = "\n".join(f"- {h}" for h in hints) if hints else ""
        msg = f"Cookie 验证失败: {detail}"
        if hint_text:
            msg += f"\n{hint_text}"
        msg += "\n\nCookie 文件已加载，可导入 CSV 后开始批量下载。"
        return False, msg

    # ------------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_fieldname(name: str) -> str:
        return (name or "").strip().strip("\ufeff").strip()

    @staticmethod
    def _normalize_column_key(name: str) -> str:
        return YoutubeDownloader._clean_fieldname(name).lower().replace(" ", "_").replace("-", "_")

    @staticmethod
    def load_csv(filepath: Path, column: str = "") -> list[str]:
        """从 CSV/TSV/TXT 读取媒体来源列表（自动去重、支持 URL 提取）。"""
        rows = YoutubeDownloader.load_csv_rows(filepath, column=column)
        return [row["video_id"] for row in rows]

    @staticmethod
    def load_csv_rows(filepath: Path, column: str = "") -> list[dict[str, str]]:
        """从 CSV/TSV/TXT 读取带原始字段的记录，保留 output_dir 等信息。"""
        if not filepath.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")

        ext = filepath.suffix.lower()
        if ext == ".tsv":
            delimiters: tuple[str, ...] = ("\t",)
        else:
            delimiters = _CSV_DELIMITERS

        last_err: Exception | None = None
        last_value_err: Exception | None = None
        for encoding in _CSV_ENCODINGS:
            for delimiter in delimiters:
                try:
                    rows = YoutubeDownloader._load_csv_rows_with_delimiter(
                        filepath, encoding=encoding, delimiter=delimiter, column=column,
                    )
                    if rows:
                        return rows
                except UnicodeDecodeError as exc:
                    last_err = exc
                    break
                except UnicodeError as exc:
                    last_err = exc
                    break
                except ValueError as exc:
                    last_value_err = exc
                    continue
                except csv.Error:
                    continue

        if last_value_err:
            raise last_value_err
        raise ValueError(
            f"无法识别文件编码或格式: {filepath}"
            + (f"（{last_err}）" if last_err else "")
        ) from last_err

    @staticmethod
    def _load_csv_rows_with_delimiter(
        filepath: Path,
        *,
        encoding: str,
        delimiter: str,
        column: str,
    ) -> list[dict[str, str]]:
        with open(filepath, "r", encoding=encoding, newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            if not sample.strip():
                return []

            reader = csv.DictReader(f, delimiter=delimiter)
            fieldnames = [
                YoutubeDownloader._clean_fieldname(name)
                for name in (reader.fieldnames or [])
            ]
            fieldnames = [name for name in fieldnames if name]
            if len(fieldnames) == 1 and any(ch in fieldnames[0] for ch in (";", "\t", "|")):
                embedded = fieldnames[0]
                for alt in (";", "\t", "|"):
                    if alt in embedded:
                        f.seek(0)
                        nested = YoutubeDownloader._load_csv_rows_with_delimiter(
                            filepath,
                            encoding=encoding,
                            delimiter=alt,
                            column=column,
                        )
                        if nested:
                            return nested
                        break

            if not fieldnames:
                f.seek(0)
                return YoutubeDownloader._read_headerless_column(f, delimiter)

            if len(fieldnames) == 1 and YoutubeDownloader._looks_like_media_cell(fieldnames[0]):
                f.seek(0)
                headerless = YoutubeDownloader._read_headerless_column(f, delimiter)
                if headerless:
                    return headerless

            raw_rows = list(reader)
            used_column, rows = YoutubeDownloader._detect_best_column(
                fieldnames, raw_rows, column,
            )
            if rows:
                for record in rows:
                    record["_import_column"] = used_column or ""
                return rows

            f.seek(0)
            headerless = YoutubeDownloader._read_headerless_column(f, delimiter)
            if headerless:
                for record in headerless:
                    record["_import_column"] = ""
                return headerless

            preview_cols = ", ".join(fieldnames[:8])
            if len(fieldnames) > 8:
                preview_cols += ", ..."
            raise ValueError(
                f"未在表头 [{preview_cols}] 中找到有效视频 ID/链接。"
                f"请确认列内为 YouTube ID、BV 号或完整 URL"
            )

    @staticmethod
    def _detect_best_column(
        fieldnames: list[str],
        raw_rows: list[dict[str, str]],
        preferred: str,
    ) -> tuple[str | None, list[dict[str, str]]]:
        target = YoutubeDownloader._resolve_column(fieldnames, preferred)
        if target:
            rows = YoutubeDownloader._extract_rows_from_raw(raw_rows, target)
            if rows:
                return target, rows

        best_column: str | None = None
        best_rows: list[dict[str, str]] = []
        best_score = -1
        for col in fieldnames:
            rows = YoutubeDownloader._extract_rows_from_raw(raw_rows, col)
            score = sum(
                YoutubeDownloader._media_cell_score(row.get(col, "") or "")
                for row in raw_rows
            )
            if len(rows) > len(best_rows) or (len(rows) == len(best_rows) and score > best_score):
                best_rows = rows
                best_column = col
                best_score = score
        return best_column, best_rows

    @staticmethod
    def _looks_like_media_cell(value: str) -> bool:
        return YoutubeDownloader._media_cell_score(value) > 0

    @staticmethod
    def _media_cell_score(value: str) -> int:
        text = (value or "").strip()
        if not text:
            return 0
        lower = text.lower()
        if "://" in text or "youtu" in lower or "bilibili" in lower or "b23.tv" in lower:
            return 3
        if re.search(r"\bBV[0-9A-Za-z]{10}\b", text, flags=re.I):
            return 3
        if re.search(r"\bav\d+\b", text, flags=re.I):
            return 2
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
            return 2
        return 0

    @staticmethod
    def _extract_rows_from_raw(
        raw_rows: list[dict[str, str]],
        target_column: str,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        target_key = YoutubeDownloader._normalize_column_key(target_column)
        for row in raw_rows:
            raw_value = row.get(target_column, "") or ""
            media_value = raw_value
            if target_key in {"aid", "av"} and raw_value.strip().isdigit():
                media_value = f"av{raw_value.strip()}"
            vid = YoutubeDownloader._normalize_video_id(media_value)
            if not vid or vid in seen:
                continue
            if target_key not in {"aid", "av"} and YoutubeDownloader._media_cell_score(raw_value) <= 0:
                continue
            seen.add(vid)
            media = YoutubeDownloader.parse_input(media_value)
            record = {key: (value or "") for key, value in row.items() if value is not None}
            record["video_id"] = vid
            record["platform"] = media.platform
            record["source_url"] = media.url
            record["media_id"] = media.media_id
            cookiefile = (
                record.get("cookiefile")
                or record.get("cookies_file")
                or record.get("cookie_file")
                or ""
            ).strip()
            if cookiefile:
                record["cookiefile"] = cookiefile
            browser_cookie = (record.get("cookies_from_browser") or "").strip()
            if browser_cookie:
                record["cookies_from_browser"] = browser_cookie
            rows.append(record)
        return rows

    @staticmethod
    def _read_headerless_column(handle: Any, delimiter: str) -> list[dict[str, str]]:
        """解析无表头单列文件（每行一个 ID/URL）。"""
        reader = csv.reader(handle, delimiter=delimiter)
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for line in reader:
            if not line:
                continue
            for raw_value in line:
                text = (raw_value or "").strip()
                if not text or text.startswith("#"):
                    continue
                vid = YoutubeDownloader._normalize_video_id(text)
                if not vid or vid in seen:
                    continue
                if YoutubeDownloader._media_cell_score(text) <= 0:
                    continue
                seen.add(vid)
                media = YoutubeDownloader.parse_input(text)
                rows.append({
                    "video_id": vid,
                    "platform": media.platform,
                    "source_url": media.url,
                    "media_id": media.media_id,
                })
        return rows

    @staticmethod
    def _resolve_column(fieldnames: list[str], preferred: str) -> str | None:
        """在表头中匹配视频 ID 列，支持中英文别名。"""
        normalized = {
            YoutubeDownloader._normalize_column_key(name): name
            for name in fieldnames
            if YoutubeDownloader._clean_fieldname(name)
        }
        if preferred:
            key = YoutubeDownloader._normalize_column_key(preferred)
            if key in normalized:
                return normalized[key]

        for alias in _CSV_COLUMN_ALIASES:
            key = YoutubeDownloader._normalize_column_key(alias)
            if key in normalized:
                return normalized[key]
        return None

    @staticmethod
    def _normalize_video_id(value: str) -> str:
        """从原始单元格内容中提取可下载媒体来源。"""
        text = (value or "").strip()
        if not text:
            return ""
        media = YoutubeDownloader.parse_input(text)
        if media.platform == "youtube":
            if "youtube" in text.lower() or "youtu.be" in text.lower() or re.fullmatch(
                r"[A-Za-z0-9_-]{11}", media.media_id
            ):
                return media.media_id
            return ""
        if media.platform == "bilibili":
            parsed = urlparse(media.original if "://" in media.original else media.url)
            if parsed.netloc.lower().endswith("b23.tv"):
                return media.url
            return media.media_id
        if "://" in text:
            return media.url
        return ""

    def redetect_browser(self) -> bool:
        """重新检测浏览器（Profile 可能已切换）。
        返回 True 表示检测到了新的 Cookie 源。
        """
        old = self._cookies_spec
        info = self.detect_browser()
        self._cookies_spec = f"{info[0]}:{info[1]}" if info else None
        changed = self._cookies_spec is not None
        if changed and self._cookies_spec == old:
            changed = False  # 没变，不算重载成功
        return changed

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_format_id(formats: list[dict[str, Any]], min_height: int = 720) -> str | None:
        """按目标清晰度选择格式：低于 720p 直接排除；低于 1080p 可回退到 720p；1080p 及以上优先选更高。"""
        def _height(resolution: str) -> int:
            try:
                return int(resolution.split("x")[-1].replace("p", ""))
            except (ValueError, IndexError):
                return 0

        video_formats = [
            fmt for fmt in formats
            if fmt.get("container") in {"mp4", "webm", "flv", "mkv", "mov"}
            and fmt.get("type") in {"Video+Audio", "Video Only"}
        ]
        if not video_formats:
            return None

        if min_height <= 720:
            candidates = [fmt for fmt in video_formats if _height(fmt.get("resolution", "")) >= 720]
            if candidates:
                candidates.sort(key=lambda fmt: _height(fmt.get("resolution", "")), reverse=True)
                return candidates[0].get("format_id", None)
            return None

        candidates = [fmt for fmt in video_formats if _height(fmt.get("resolution", "")) >= min_height]
        if candidates:
            candidates.sort(key=lambda fmt: _height(fmt.get("resolution", "")), reverse=True)
            return candidates[0].get("format_id", None)

        fallback = [fmt for fmt in video_formats if _height(fmt.get("resolution", "")) >= 720]
        if fallback:
            fallback.sort(key=lambda fmt: _height(fmt.get("resolution", "")), reverse=True)
            return fallback[0].get("format_id", None)

        return None

    @staticmethod
    def _dedup_formats(formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """每档分辨率只保留最优版本：Video Only 优先（画质更高），再按文件大小。"""
        best: dict[str, dict[str, Any]] = {}
        for f in formats:
            key = f["resolution"]
            if key not in best:
                best[key] = f
                continue
            # Video Only > Video+Audio
            cur_type = 0 if f["type"] == "Video Only" else 1
            old_type = 0 if best[key]["type"] == "Video Only" else 1
            if cur_type < old_type or (
                cur_type == old_type
                and (f["filesize"] or 0) > (best[key]["filesize"] or 0)
            ):
                best[key] = f
        def _sort_key(f: dict[str, Any]) -> int:
            res = f.get("resolution", "")
            try:
                return int(res.split("x")[-1])
            except (ValueError, IndexError):
                return 0
        return sorted(best.values(), key=_sort_key, reverse=True)

    @staticmethod
    def _format_note(fmt: dict[str, Any]) -> str:
        note = fmt.get("format_note", "")
        if note and note != "unknown":
            return note
        res = fmt.get("resolution") or ""
        if res and res != "audio only":
            return res
        return ""

    @staticmethod
    def _format_type(fmt: dict[str, Any]) -> str:
        vcodec = fmt.get("vcodec", "none")
        acodec = fmt.get("acodec", "none")
        has_video = vcodec and vcodec != "none"
        has_audio = acodec and acodec != "none"
        if has_video and has_audio:
            return "Video+Audio"
        if has_video:
            return "Video Only"
        return "Audio"

    @staticmethod
    def _human_size(size_bytes: int | None) -> str:
        if size_bytes is None:
            return "—"
        if size_bytes < 1024:
            return f"{size_bytes}B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"
