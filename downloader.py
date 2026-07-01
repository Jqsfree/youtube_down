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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yt_dlp


def _find_tool(name: str) -> str:
    """PyInstaller 打包后优先找内嵌的二进制，否则回退 PATH。"""
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, name)  # noqa: SLF001
        if os.path.exists(bundled):
            return bundled
    return name

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


def check_environment() -> tuple[bool, list[EnvItem]]:
    """启动时检查所有依赖。返回 (all_ok, items)。"""
    items: list[EnvItem] = []

    # yt-dlp
    try:
        import yt_dlp as _ydl
        ver = str(_ydl.version.__version__) if hasattr(_ydl, 'version') else "?"
        items.append(EnvItem("yt-dlp", "ok", ver, ""))
    except ImportError:
        items.append(EnvItem("yt-dlp", "error", "", "未安装: pip install yt-dlp"))

    # yt-dlp-ejs
    try:
        import yt_dlp_ejs  # noqa: F811
        ejs_ver = getattr(yt_dlp_ejs, "__version__", "?")
        items.append(EnvItem("yt-dlp-ejs", "ok", str(ejs_ver), ""))
    except ImportError:
        items.append(EnvItem("yt-dlp-ejs", "error", "",
            "未安装: pip install yt-dlp-ejs（Cookie 模式下必需）"))

    # ffmpeg
    ffmpeg_path = shutil.which(_find_tool("ffmpeg"))
    if ffmpeg_path:
        try:
            r = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5
            )
            ver_line = r.stdout.split("\n")[0] if r.stdout else "?"
            items.append(EnvItem("ffmpeg", "ok", ver_line, ""))
        except Exception:
            items.append(EnvItem("ffmpeg", "ok", "?", ""))
    else:
        items.append(EnvItem("ffmpeg", "error", "", "未找到 ffmpeg"))

    # ffprobe
    if shutil.which(_find_tool("ffprobe")):
        items.append(EnvItem("ffprobe", "ok", "", ""))
    else:
        items.append(EnvItem("ffprobe", "warning", "", "未找到 ffprobe，无法校验下载文件"))

    # deno
    deno_path = shutil.which(_find_tool("deno"))
    if deno_path:
        try:
            r = subprocess.run(
                ["deno", "--version"], capture_output=True, text=True, timeout=5
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
    ("RATE_LIMIT",         False, ["http error 429", "too many requests"]),
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

    def __init__(self, cookies_from_browser: str | None = None) -> None:
        """初始化下载器。

        Args:
            cookies_from_browser: 浏览器名称:profile 格式，
                如 'chrome:Default', 'firefox'。
                None 时自动检测，'' 时不使用 cookie。
        """
        self._cancelled: bool = False
        self._cookies_spec: str | None = cookies_from_browser
        if self._cookies_spec is None:
            info = self.detect_browser()
            self._cookies_spec = f"{info[0]}:{info[1]}" if info else None

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
        home = os.path.expanduser("~")

        # Chrome — 多 Profile 检测
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

    def _make_opts(self, use_cookies: bool = False, **extra: Any) -> dict[str, Any]:
        """构建 yt-dlp 通用选项。

        Args:
            use_cookies: 是否启用浏览器 Cookie。默认 False。
        """
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "logger": _QuietLogger(),
            # 网络韧性：瞬时故障自动重试
            "extractor_retries": 3,
            "retries": 3,
        }
        if use_cookies and self._cookies_spec:
            # self._cookies_spec 格式: "chrome:Default" 或 "chrome"
            parts = self._cookies_spec.split(":")
            opts["cookiesfrombrowser"] = tuple(parts)  # type: ignore[assignment]
        opts.update(extra)  # extra 中的 extractor_args 会覆盖默认值
        return opts

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_info(
        self, video_id: str, use_cookies: bool = False
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
        opts = self._make_opts(use_cookies=use_cookies)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None or len(info.get("formats", [])) == 0:
                raise yt_dlp.utils.DownloadError(
                    f"No formats available for: {video_id}"
                )
            return info

    def list_formats(
        self, video_id: str = "", info: dict[str, Any] | None = None
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

        results: list[dict[str, Any]] = []
        for fmt in raw_formats:
            if fmt.get("ext") not in ("mp4", "m4a"):
                continue
            resolution = fmt.get("resolution") or ""
            if resolution == "audio only":
                resolution = "Audio"
            elif not resolution:
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

        return results

    def download(
        self,
        video_id: str,
        format_id: str,
        output_dir: Path,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        use_cookies: bool = False,
    ) -> Path:
        """下载指定格式的视频。

        Args:
            video_id: YouTube video ID。
            format_id: yt-dlp format ID。
            output_dir: 输出目录。
            progress_callback: 进度回调。
            use_cookies: 是否使用浏览器 Cookie。默认 False。

        Returns:
            下载完成后的文件路径。

        Raises:
            yt_dlp.utils.DownloadError: 下载失败（由调用方分类处理）。
        """
        self._cancelled = False
        output_dir.mkdir(parents=True, exist_ok=True)

        output_template = str(output_dir / "%(title)s.%(ext)s")
        url = f"https://www.youtube.com/watch?v={video_id}"

        def _progress_hook(d: dict[str, Any]) -> None:
            if progress_callback:
                progress_callback(d)
            if self._cancelled:
                raise yt_dlp.utils.DownloadError("下载已取消")

        # "best" 不需要追加 bestaudio（已经是完整格式选择器）
        fmt_str = format_id if format_id == "best" else f"{format_id}+bestaudio/best"
        opts = self._make_opts(
            use_cookies=use_cookies,
            format=fmt_str,
            outtmpl=output_template,
            progress_hooks=[_progress_hook],
            merge_output_format="mp4",
            # 速度优化
            concurrent_fragment_downloads=8,  # DASH 片段并行下载
            buffersize=2 * 1024 * 1024,       # 2MB 缓冲区
            file_access_retries=3,            # 文件写入重试
            fragment_retries=3,               # 片段下载重试
        )

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
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

            # 校验文件是有效媒体（ffprobe 能解析则文件完整）
            ffprobe_result = subprocess.run(
                [_find_tool("ffprobe"), "-v", "quiet", "-show_entries",
                 "format=duration", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            if ffprobe_result.returncode != 0 or not ffprobe_result.stdout.strip():
                raise yt_dlp.utils.DownloadError(
                    f"下载文件无法解析为有效媒体: {path}"
                )

            return path

    def cancel(self) -> None:
        """取消当前下载。"""
        self._cancelled = True

    # ------------------------------------------------------------------
    # Cookie 管理
    # ------------------------------------------------------------------

    def validate_cookies(self) -> tuple[bool, str]:
        """启动时验证 Cookie 是否真的可用（非空、YouTube 接受）。

        用一个已知公开视频做测试提取，确认 Cookie 未被拒绝、
        JS 挑战能正常解决、能拿到可用格式列表。
        """
        if not self._cookies_spec:
            return False, "未配置浏览器 Cookie"
        try:
            info = self.get_info("dQw4w9WgXcQ", use_cookies=True)
            n = len(info.get("formats", []))
            if n == 0:
                return False, "Cookie 有效但 YouTube 未返回视频格式"
            return True, f"Cookie 验证通过 ({n} formats)"
        except Exception as exc:
            return False, f"Cookie 验证失败: {clean_error(exc)}"

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
        """从 CSV 读取 video ID 列表（UTF-8/BOM，自动去重跳过空行）。"""
        if not filepath.exists():
            raise FileNotFoundError(f"文件不存在: {filepath}")

        last_err: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "utf-16", "gbk", "latin-1"):
            try:
                with open(filepath, "r", encoding=encoding) as f:
                    reader = csv.DictReader(f)
                    fieldnames = reader.fieldnames or []
                    if column not in fieldnames:
                        raise ValueError(
                            f"CSV 中没有 '{column}' 列，可用列: {fieldnames}"
                        )
                    ids: list[str] = []
                    seen: set[str] = set()
                    for row in reader:
                        vid = row[column].strip()
                        if vid and vid not in seen:
                            seen.add(vid)
                            ids.append(vid)
                    return ids
            except (UnicodeDecodeError, UnicodeError) as exc:
                last_err = exc
                continue

        raise ValueError(f"无法识别 CSV 文件编码: {filepath}") from last_err

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

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
