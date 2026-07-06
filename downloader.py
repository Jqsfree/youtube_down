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

import yt_dlp

from logger_utils import AppLogger

_COOKIE_CONFIG = Path.home() / ".youtube_downloader" / "cookiefile.txt"

# Netscape cookies.txt 常见文件头
_NETSCAPE_MARKERS = ("# netscape", "# http cookie file")

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
        self._cookies_spec: str | None = cookies_from_browser
        self._cookie_broken: bool = False  # Cookie 连续失败标记
        if cookiefile:
            self.set_cookiefile(cookiefile, persist=False)
        elif cookies_from_browser == "":
            self._cookies_spec = None
        elif cookies_from_browser is None:
            saved = self._load_saved_cookiefile()
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

    def _make_opts(self, use_cookies: bool = True, **extra: Any) -> dict[str, Any]:
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
        if use_cookies and (self._cookies_spec or self._cookiefile_path):
            if self._cookiefile_path:
                opts["cookiefile"] = str(self._cookiefile_path)
            elif self._cookies_spec:
                parts = self._cookies_spec.split(":")
                opts["cookiesfrombrowser"] = tuple(parts)  # type: ignore[assignment]
        opts.update(extra)  # extra 中的 extractor_args 会覆盖默认值
        return opts

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_info(
        self, video_id: str, use_cookies: bool = True
    ) -> dict[str, Any]:
        """获取视频元信息和可用格式列表。

        Args:
            video_id: YouTube video ID。
            use_cookies: 是否使用浏览器 Cookie。默认 False。

        Returns:
            yt-dlp info dict（title, uploader, duration, formats 等）。

        Raises:
            yt_dlp.utils.DownloadError: yt-dlp 内部错误（由调用方分类处理）。
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        # Cookie 已标记不可用 → 跳过所有 Cookie 尝试
        if self._cookie_broken:
            use_cookies = False
        # Cookie 模式失败时自动回退无 Cookie（Chrome 锁定等场景；cookie 文件不回退）
        last_err = None
        use_browser = use_cookies and bool(self._cookies_spec) and not self._cookiefile_path
        attempts = [True, False] if use_browser else [use_cookies]
        with self._yt_dlp_lock:
            for attempt in attempts:
                try:
                    opts = self._make_opts(use_cookies=attempt)
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if info and len(info.get("formats", [])) > 0:
                            if attempt != use_cookies:  # Cookie 回退成功 → 标记
                                self._cookie_broken = True
                            return info
                        if info:
                            last_err = yt_dlp.utils.DownloadError(
                                "YouTube 返回了视频信息但格式列表为空"
                            )
                except Exception as exc:
                    if "copy chrome cookie" in str(exc).lower():
                        self._cookie_broken = True  # 锁库，后续全跳过 Cookie
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
            video_id: YouTube video ID。如提供 info 则可省略。
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
        for fmt in raw_formats:
            if fmt.get("ext") not in ("mp4", "m4a"):
                continue
            resolution = fmt.get("resolution") or ""
            if resolution == "audio only":
                resolution = "Audio"
            elif not resolution:
                continue

            if min_height is not None and resolution != "Audio":
                if _height(resolution) < min_height:
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

    def download(
        self,
        video_id: str,
        format_id: str,
        output_dir: Path,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        use_cookies: bool = True,
        needs_audio_merge: bool = True,
        min_height: int | None = None,
    ) -> Path:
        """下载指定格式的视频。

        Args:
            video_id: YouTube video ID。
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
        url = f"https://www.youtube.com/watch?v={video_id}"

        def _progress_hook(d: dict[str, Any]) -> None:
            if progress_callback:
                progress_callback(d)
            with self._cancel_lock:
                cancelled = self._cancelled
            if cancelled:
                raise yt_dlp.utils.DownloadError("下载已取消")

        if format_id == "best":
            fmt_str = "best"
        elif needs_audio_merge:
            fmt_str = f"{format_id}+bestaudio/best"
        else:
            fmt_str = format_id
        opts = self._make_opts(
            use_cookies=use_cookies,
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
                    # 只保留视频/音频扩展名，排除 .json/.jpg/.part 等
                    _VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".m4a", ".mp3"}
                    candidates = [
                        c for c in candidates if c.suffix.lower() in _VIDEO_EXTS
                    ]
                    if candidates:
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

            # 校验下载后视频分辨率不低于最低要求
            if min_height and min_height > 0:
                height_result = subprocess.run(
                    [_find_tool("ffprobe"), "-v", "error",
                     "-select_streams", "v:0", "-show_entries",
                     "stream=height", "-of", "csv=p=0", str(path)],
                    capture_output=True, text=True, timeout=10,
                )
                height_str = height_result.stdout.strip()
                if height_str:
                    try:
                        actual_height = int(height_str)
                        if actual_height < min_height:
                            raise yt_dlp.utils.DownloadError(
                                f"下载视频分辨率 ({actual_height}p)"
                                f" 低于最低要求 ({min_height}p)"
                            )
                    except ValueError:
                        AppLogger.get_logger().warning(
                            "无法解析视频高度用于分辨率校验: height_str=%s, file=%s",
                            height_str, path.name,
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

    def cookie_source(self) -> str:
        """当前 Cookie 来源描述（用于 UI 显示）。"""
        if self._cookiefile_path:
            return f"file:{self._cookiefile_path.name}"
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
        if "youtube.com" not in head:
            return False
        return any(m in head for m in _NETSCAPE_MARKERS) or "\t" in head

    @staticmethod
    def validate_cookie_file(path: str | Path) -> tuple[bool, str]:
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
        if "youtube.com" not in lower:
            return False, "文件中未找到 youtube.com 的 Cookie（请从已登录 YouTube 的浏览器导出）"
        if not YoutubeDownloader.is_netscape_cookie_file(cookie_path):
            return False, (
                "不是 Netscape cookies.txt 格式。\n"
                "请使用扩展「Get cookies.txt LOCALLY」导出，勿使用 JSON 格式。"
            )
        return True, "格式校验通过"

    def set_cookiefile(self, path: str | Path, *, persist: bool = True) -> None:
        """设置 Netscape 格式 cookie 文件（优先于浏览器 Cookie）。"""
        cookie_path = Path(path).expanduser().resolve()
        ok, msg = self.validate_cookie_file(cookie_path)
        if not ok:
            raise ValueError(msg)
        self._cookiefile_path = cookie_path
        self._cookies_spec = None
        self._cookie_broken = False
        if persist:
            self._persist_cookiefile(cookie_path)

    def clear_cookiefile(self, *, persist: bool = True) -> None:
        """清除 cookie 文件，恢复自动检测浏览器 Cookie。"""
        self._cookiefile_path = None
        self._cookie_broken = False
        if persist:
            self._persist_cookiefile(None)
        info = self.detect_browser()
        self._cookies_spec = f"{info[0]}:{info[1]}" if info else None

    def validate_cookies(self) -> tuple[bool, str]:
        """联网探测 Cookie 是否能让 YouTube 返回格式列表。"""
        if not self._cookiefile_path and not self._cookies_spec:
            return False, "未配置 Cookie"

        using_file = bool(self._cookiefile_path)
        if using_file and not _resolve_tool("deno") and not _resolve_tool("node"):
            return False, (
                "未检测到 deno 或 node.js。\n"
                "Cookie 模式需要 JS 运行时才能通过 YouTube 验证。\n"
                "请运行: powershell -ExecutionPolicy Bypass -File scripts\\setup_dev.ps1\n"
                "或: winget install Denoland.Deno\n"
                "安装后重启应用再试。"
            )

        test_id = "dQw4w9WgXcQ"
        cookie_err: Exception | None = None
        try:
            info = self.get_info(test_id, use_cookies=True)
            n = len(info.get("formats", []))
            return True, f"Cookie 验证通过 ({n} formats, {self.cookie_source()})"
        except Exception as exc:
            cookie_err = exc

        # 对比：无 Cookie 是否能获取格式
        try:
            info_no = self.get_info(test_id, use_cookies=False)
            if len(info_no.get("formats", [])) > 0:
                detail = clean_error(cookie_err) if cookie_err else "未知错误"
                return False, (
                    f"Cookie 未能改善访问（不使用 Cookie 反而能获取格式）。\n"
                    f"可能 cookies.txt 已过期或导出不完整。\n"
                    f"详情: {detail}\n"
                    "请重新登录 YouTube 后导出新的 cookies.txt。"
                )
        except Exception:
            pass

        detail = clean_error(cookie_err) if cookie_err else "YouTube 未返回格式"
        hints: list[str] = []
        if using_file:
            hints.append("确认 cookies.txt 来自已登录 YouTube 的浏览器（扩展 Get cookies.txt LOCALLY）")
        if "bot" in detail.lower() or "sign in" in detail.lower():
            hints.append("YouTube 仍要求验证，请重新导出 Cookie 或换网络/IP 后重试")
        if _resolve_tool("deno") or _resolve_tool("node"):
            hints.append("已检测到 JS 运行时，若仍失败多为 Cookie 过期")
        hint_text = "\n".join(f"- {h}" for h in hints) if hints else ""
        msg = f"Cookie 验证失败: {detail}"
        if hint_text:
            msg += f"\n{hint_text}"
        msg += "\n\nCookie 文件已加载，仍可直接点击「获取信息」测试目标视频。"
        return False, msg

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
    # CSV
    # ------------------------------------------------------------------

    @staticmethod
    def load_csv(filepath: Path, column: str = "video_id") -> list[str]:
        """从 CSV/TSV/TXT 读取 video ID 列表（自动去重、支持 URL 提取）。"""
        rows = YoutubeDownloader.load_csv_rows(filepath, column=column)
        return [row["video_id"] for row in rows]

    @staticmethod
    def load_csv_rows(filepath: Path, column: str = "video_id") -> list[dict[str, str]]:
        """从 CSV/TSV/TXT 读取带原始字段的记录，保留 output_dir 等信息。"""
        if not filepath.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")

        ext = filepath.suffix.lower()
        delimiter: str | None = None
        if ext == ".tsv":
            delimiter = "\t"
        elif ext == ".txt":
            delimiter = None
        else:
            delimiter = ","

        last_err: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "utf-16", "gbk", "latin-1"):
            try:
                with open(filepath, "r", encoding=encoding, newline="") as f:
                    sample = f.read(4096)
                    f.seek(0)
                    if delimiter is None:
                        try:
                            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                            delimiter = dialect.delimiter
                        except csv.Error:
                            delimiter = ","

                    reader = csv.DictReader(f, delimiter=delimiter)
                    fieldnames = [name.strip() if name else "" for name in (reader.fieldnames or [])]
                    if not fieldnames:
                        raise ValueError("文件中没有可解析的表头")

                    target_column = YoutubeDownloader._resolve_column(fieldnames, column)
                    if target_column is None:
                        raise ValueError(
                            f"文件中没有 '{column}' 列，可用列: {fieldnames}"
                        )

                    rows: list[dict[str, str]] = []
                    seen: set[str] = set()
                    for row in reader:
                        raw_value = row.get(target_column, "") or ""
                        vid = YoutubeDownloader._normalize_video_id(raw_value)
                        if not vid or vid in seen:
                            continue
                        seen.add(vid)
                        record = {key: (value or "") for key, value in row.items() if value is not None}
                        record["video_id"] = vid
                        rows.append(record)
                    return rows
            except (UnicodeDecodeError, UnicodeError) as exc:
                last_err = exc
                continue
            except ValueError:
                raise

        raise ValueError(f"无法识别文件编码或格式: {filepath}") from last_err

    @staticmethod
    def _resolve_column(fieldnames: list[str], preferred: str) -> str | None:
        """在表头中匹配用户指定字段，支持大小写和空格差异。"""
        normalized = {name.strip().lower().replace(" ", "_").replace("-", "_"): name for name in fieldnames}
        if preferred:
            key = preferred.strip().lower().replace(" ", "_").replace("-", "_")
            if key in normalized:
                return normalized[key]

        for key in ("video_id", "videoid", "id", "youtube_id", "youtubeid", "url", "video_url"):
            if key in normalized:
                return normalized[key]
        return None

    @staticmethod
    def _normalize_video_id(value: str) -> str:
        """从原始单元格内容中提取 11 位 YouTube video ID。"""
        text = (value or "").strip()
        if not text:
            return ""
        text = text.replace("\\", "/")
        patterns = [
            r"(?:v=|vi=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})",
            r"^([A-Za-z0-9_-]{11})$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return text

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
            if fmt.get("container") == "mp4"
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
