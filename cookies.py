"""Cookie file IO, browser discovery and per-operation private cookie copies.

No GUI or downloader dependency. Downloader keeps the legacy entry points.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable
import yt_dlp
from private_files import private_directory
from platforms.facade import platform_cookie_config_names, platform_cookie_domains, platform_labels

_COOKIE_CONFIG_DIR = Path.home() / ".youtube_downloader"

_COOKIE_CONFIG = _COOKIE_CONFIG_DIR / "cookiefile.txt"

_PLATFORM_COOKIE_CONFIGS = {
    key: _COOKIE_CONFIG_DIR / name
    for key, name in platform_cookie_config_names().items()
}

_PLATFORM_COOKIE_DOMAINS = platform_cookie_domains()

_PLATFORM_LABELS = platform_labels()

_NETSCAPE_MARKERS = ("# netscape", "# http cookie file")

_COOKIE_ENCODINGS = (
    "utf-8-sig",
    "utf-8",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "gbk",
    "cp936",
    "latin-1",
)

def platform_label(platform: str) -> str:
    return _PLATFORM_LABELS.get(platform, platform)


def cookie_platform_from_text(text: str) -> str | None:
    lower = text.lower()
    for platform, domains in _PLATFORM_COOKIE_DOMAINS.items():
        if any(domain in lower for domain in domains):
            return platform
    return None


def chromium_cookie_profile(user_data_dir: str) -> str | None:
    """在 Chromium 系 User Data 目录中找第一个含 Cookies 的 Profile。"""
    if not os.path.isdir(user_data_dir):
        return None
    profiles = ["Default"] + sorted(
        d for d in os.listdir(user_data_dir)
        if d.startswith("Profile ") and os.path.isdir(os.path.join(user_data_dir, d))
    )
    for profile in profiles:
        if os.path.isfile(os.path.join(user_data_dir, profile, "Cookies")):
            return profile
    return None


def detect_browser() -> tuple[str, str] | None:
    """自动检测系统中可用的浏览器及 Profile。

    按优先级：Chrome(Default→Profile*) → Edge → Brave → Firefox → ...

    Returns:
        (browser_name, profile_name) 或 None。
    """
    if os.name == "nt":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        candidates = (
            ("chrome", os.path.join(localappdata, "Google", "Chrome", "User Data")),
            ("edge", os.path.join(localappdata, "Microsoft", "Edge", "User Data")),
            ("brave", os.path.join(
                localappdata, "BraveSoftware", "Brave-Browser", "User Data",
            )),
        )
        for name, path in candidates:
            profile = chromium_cookie_profile(path)
            if profile:
                return (name, profile)
        firefox_dir = os.path.join(appdata, "Mozilla", "Firefox", "Profiles")
        if os.path.isdir(firefox_dir):
            return ("firefox", "default")
        return None

    home = os.path.expanduser("~")
    # macOS：Application Support（无 User Data 层）
    if sys.platform == "darwin":
        mac_app = os.path.join(home, "Library", "Application Support")
        mac_candidates = (
            ("chrome", os.path.join(mac_app, "Google", "Chrome")),
            ("edge", os.path.join(mac_app, "Microsoft Edge")),
            ("brave", os.path.join(mac_app, "BraveSoftware", "Brave-Browser")),
            ("chromium", os.path.join(mac_app, "Chromium")),
            ("opera", os.path.join(mac_app, "com.operasoftware.Opera")),
        )
        for name, path in mac_candidates:
            profile = chromium_cookie_profile(path)
            if profile:
                return (name, profile)
        firefox_dir = os.path.join(mac_app, "Firefox", "Profiles")
        if os.path.isdir(firefox_dir):
            return ("firefox", "default")

    # Linux（及 macOS 上可能存在的 Linux 风格路径）
    linux_candidates = (
        ("chrome", os.path.join(home, ".config/google-chrome")),
        ("edge", os.path.join(home, ".config/microsoft-edge")),
        ("brave", os.path.join(home, ".config/BraveSoftware/Brave-Browser")),
        ("chromium", os.path.join(home, ".config/chromium")),
        ("opera", os.path.join(home, ".config/opera")),
    )
    for name, path in linux_candidates:
        profile = chromium_cookie_profile(path)
        if profile:
            return (name, profile)
    firefox_dir = os.path.join(home, ".mozilla/firefox")
    if os.path.isdir(firefox_dir):
        return ("firefox", "default")

    return None


def read_cookie_text(path: Path) -> str:
    """按多编码尝试读取 cookies.txt（兼容 Windows UTF-16 / BOM / GBK）。"""
    raw = path.read_bytes()
    if not raw:
        return ""
    # BOM 快速路径
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    last_err: Exception | None = None
    for encoding in _COOKIE_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
        # 拒绝仍含大量 NUL 的误解码（典型 UTF-16 被当成 latin-1）
        if "\x00" in text[:4096] and encoding not in {"utf-16", "utf-16-le", "utf-16-be"}:
            continue
        if (
            any(m in text[:4096].lower() for m in _NETSCAPE_MARKERS)
            or "\t" in text
            or cookie_platform_from_text(text) is not None
        ):
            return text
    # 最后回退：尽量给出可读文本
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover
        raise OSError(f"无法解码 Cookie 文件: {last_err or exc}") from exc


def is_netscape_cookie_file(path: str | Path) -> bool:
    """快速判断是否为 Netscape cookies.txt（本地校验，不访问网络）。"""
    cookie_path = Path(path)
    if not cookie_path.is_file() or cookie_path.stat().st_size == 0:
        return False
    try:
        head = read_cookie_text(cookie_path)[:4096].lower()
    except OSError:
        return False
    return any(m in head for m in _NETSCAPE_MARKERS) or "\t" in head


def validate_cookie_file(path: str | Path, platform: str | None = None) -> tuple[bool, str]:
    """本地校验 cookie 文件格式（不访问网络）。"""
    cookie_path = Path(path).expanduser()
    if not cookie_path.is_file():
        return False, f"文件不存在: {cookie_path}"
    if cookie_path.stat().st_size == 0:
        return False, "文件为空"
    try:
        text = read_cookie_text(cookie_path)
    except OSError as exc:
        return False, f"无法读取文件: {exc}"
    lower = text.lower()
    if not (
        any(m in lower[:4096] for m in _NETSCAPE_MARKERS) or "\t" in text
    ):
        return False, (
            "不是 Netscape cookies.txt 格式。\n"
            "请使用扩展「Get cookies.txt LOCALLY」导出，勿使用 JSON 格式。"
        )
    detected = cookie_platform_from_text(lower)
    if platform:
        expected_domains = _PLATFORM_COOKIE_DOMAINS.get(platform, ())
        if not any(domain in lower for domain in expected_domains):
            label = platform_label(platform)
            domains = " / ".join(expected_domains)
            return False, f"文件中未找到 {domains} 的 Cookie（请从已登录 {label} 的浏览器导出）"
        detected = platform
    elif detected is None:
        return False, "文件中未找到 youtube.com / bilibili.com / tiktok.com 的 Cookie"
    msg = f"格式校验通过 ({platform_label(detected)})"
    if detected == "bilibili" and "sessdata" not in lower:
        msg += "；未检测到 SESSDATA，可能不是登录 Cookie"
    return True, msg


def load_saved_cookiefile(config_path: Path = _COOKIE_CONFIG) -> Path | None:
    if not config_path.is_file():
        return None
    try:
        path = Path(config_path.read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return path if path.is_file() else None


def load_saved_cookiefiles(configs: dict[str, Path] = _PLATFORM_COOKIE_CONFIGS, legacy_loader: Callable[[], Path | None] | None = None) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for platform, config_path in configs.items():
        if not config_path.is_file():
            continue
        try:
            path = Path(config_path.read_text(encoding="utf-8").strip())
        except OSError:
            continue
        if path.is_file():
            result[platform] = path
    legacy = (legacy_loader or load_saved_cookiefile)()
    if legacy and "youtube" not in result:
        result["youtube"] = legacy
    return result


def persist_cookiefile(path: Path | None, config_path: Path = _COOKIE_CONFIG) -> None:
    try:
        if path is None:
            if config_path.is_file():
                config_path.unlink()
            return
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(str(path.resolve()), encoding="utf-8")
    except OSError:
        pass


def persist_platform_cookiefile(platform: str, path: Path | None, configs: dict[str, Path] = _PLATFORM_COOKIE_CONFIGS) -> None:
    config_path = configs.get(platform)
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


def allowed_cookie_domain(domain: str, platform: str | None) -> bool:
    domains = (_PLATFORM_COOKIE_DOMAINS.get(platform, ()) if platform else
               tuple(d for group in _PLATFORM_COOKIE_DOMAINS.values() for d in group))
    domain = domain.lower().lstrip(".")
    return any(domain == d or domain.endswith("." + d) for d in domains)


@contextmanager
def open_ydl(opts: dict[str, Any], platform: str | None = None):
    """Own the mutable yt-dlp cookie file for exactly one operation."""
    source = opts.get("cookiefile")
    if not source:
        with yt_dlp.YoutubeDL(opts) as ydl:
            yield ydl
        return
    with private_directory("ytdown-cookie-") as private_dir:
        copy = Path(private_dir) / "cookies.txt"
        text = read_cookie_text(Path(source))
        lines = ["# Netscape HTTP Cookie File"]
        for line in text.splitlines():
            fields = line.removeprefix("#HttpOnly_").split("\t")
            if len(fields) == 7 and allowed_cookie_domain(fields[0], platform):
                lines.append(line)
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        with yt_dlp.YoutubeDL({**opts, "cookiefile": str(copy)}) as ydl:
            yield ydl


def dump_browser_cookies(browser_spec: str, dest: Path) -> Path:
    """从浏览器导出 Netscape cookies（CLI / GUI 共用）。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError("Cookie 导出目标已存在，请使用新的私有临时文件")
    parts = tuple(p for p in browser_spec.split(":") if p)
    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "cookiesfrombrowser": parts,
    }
    created = False
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            jar = ydl.cookiejar
            for cookie in list(jar):
                if not allowed_cookie_domain(cookie.domain, None):
                    jar.clear(cookie.domain, cookie.path, cookie.name)
            # Create privately before save; never change permissions after writing secrets.
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            created = True
            jar.save(filename=str(dest), ignore_discard=True, ignore_expires=True)
        if not dest.is_file() or dest.stat().st_size < 80:
            raise RuntimeError("无法导出有效 Cookie，请检查浏览器或改用导入文件")
    except BaseException:
        if created:
            dest.unlink(missing_ok=True)
        raise
    return dest


