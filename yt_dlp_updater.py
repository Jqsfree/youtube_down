"""Hot-update yt-dlp / yt-dlp-ejs for frozen EXE builds.

Must not import PySide6. Must not import yt_dlp at module level.
Install OverlayFinder before any yt_dlp import so it beats PyInstaller's FrozenImporter.
"""

from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

OVERLAY_ENV = "VID_DOWNLOAD_YTDLP_OVERLAY"
PACKAGES = ("yt-dlp", "yt-dlp-ejs")
ALLOWED_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org"})
ALLOWED_TOP_DIRS = frozenset({"yt_dlp", "yt_dlp_ejs"})
CHECK_INTERVAL_SECONDS = 24 * 3600
_PYPI_JSON = "https://pypi.org/pypi/{name}/json"
_USER_AGENT = "MultiPlatformDownloader/yt-dlp-updater"

FetchJson = Callable[[str], dict[str, Any]]
DownloadFn = Callable[[str, str, Path], None]
ExtractFn = Callable[[Path, Path], None]


@dataclass
class WheelInfo:
    version: str
    url: str
    sha256: str
    filename: str


@dataclass
class CheckResult:
    available: bool
    current_yt_dlp: str
    latest_yt_dlp: str
    current_ejs: str
    latest_ejs: str
    error: str = ""


class OverlayFinder:
    """meta_path finder that loads yt_dlp* from an on-disk overlay directory."""

    def __init__(self, active_dir: Path) -> None:
        self.active_dir = Path(active_dir)
        self._path = [str(self.active_dir)]

    def find_spec(self, fullname: str, path: object = None, target: object = None):
        if not _is_overlay_module(fullname):
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, self._path, target)


def _is_overlay_module(fullname: str) -> bool:
    return (
        fullname == "yt_dlp"
        or fullname.startswith("yt_dlp.")
        or fullname == "yt_dlp_ejs"
        or fullname.startswith("yt_dlp_ejs.")
    )


def overlay_enabled() -> bool:
    if os.environ.get(OVERLAY_ENV, "").strip() in {"1", "true", "yes"}:
        return True
    return bool(getattr(sys, "frozen", False))


def default_overlay_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "MultiPlatformDownloader" / "yt-dlp-overlay"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "MultiPlatformDownloader" / "yt-dlp-overlay"


def overlay_in_use(root: Path | None = None) -> bool:
    if not overlay_enabled():
        return False
    root = root or default_overlay_root()
    return (root / "active" / "yt_dlp").is_dir()


def overlay_source_label(root: Path | None = None) -> str:
    return "overlay" if overlay_in_use(root) else "bundled"


def is_prerelease(version: str) -> bool:
    return bool(re.search(r"(?:a|b|rc|dev)\d*", version, re.I))


def parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in re.split(r"[.\-+]", version):
        if token.isdigit():
            parts.append(int(token))
        else:
            break
    return tuple(parts)


def version_newer(latest: str, current: str) -> bool:
    if not latest:
        return False
    if not current or current == "?":
        return True
    return parse_version(latest) > parse_version(current)


def assert_allowed_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("url must be https")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"disallowed host: {host}")


def select_wheel(pypi_json: dict[str, Any]) -> WheelInfo:
    version = str((pypi_json.get("info") or {}).get("version") or "")
    if not version:
        raise ValueError("missing version on PyPI json")
    if is_prerelease(version):
        raise ValueError("pre-release version skipped")
    for item in pypi_json.get("urls") or []:
        if item.get("packagetype") != "bdist_wheel":
            continue
        filename = str(item.get("filename") or "")
        if not filename.endswith("py3-none-any.whl"):
            continue
        url = str(item.get("url") or "")
        assert_allowed_url(url)
        sha256 = str((item.get("digests") or {}).get("sha256") or "")
        if not sha256:
            raise ValueError("wheel missing sha256")
        return WheelInfo(version=version, url=url, sha256=sha256, filename=filename)
    raise ValueError("no py3-none-any wheel")


def _urlopen(url: str, timeout: float = 30):
    assert_allowed_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_pypi_json(package_name: str) -> dict[str, Any]:
    url = _PYPI_JSON.format(name=package_name)
    with _urlopen(url) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def download_file(url: str, expected_sha256: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    digest = hashlib.sha256()
    try:
        with _urlopen(url) as resp, tmp.open("wb") as fh:
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                digest.update(chunk)
                fh.write(chunk)
        if digest.hexdigest() != expected_sha256.lower():
            raise ValueError("sha256 mismatch")
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def extract_wheel(wheel: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            _assert_safe_zip_name(name)
            top = name.split("/", 1)[0]
            if top not in ALLOWED_TOP_DIRS:
                continue
            target = dest_dir.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)


def _assert_safe_zip_name(name: str) -> None:
    if name.startswith("/") or name.startswith("\\"):
        raise ValueError("unsafe zip path")
    parts = Path(name.replace("\\", "/")).parts
    if ".." in parts:
        raise ValueError("unsafe zip path")
    if len(name) >= 2 and name[1] == ":":
        raise ValueError("unsafe zip path")


def activate_staging(root: Path) -> None:
    active = root / "active"
    staging = root / "staging"
    backup = root / "active.bak"
    if not staging.is_dir():
        raise ValueError("staging missing")
    if backup.exists():
        shutil.rmtree(backup)
    if active.exists():
        active.rename(backup)
    try:
        staging.rename(active)
    except Exception:
        if backup.exists() and not active.exists():
            backup.rename(active)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def load_state(root: Path | None = None) -> dict[str, Any]:
    path = (root or default_overlay_root()) / "state.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, Any], root: Path | None = None) -> None:
    root = root or default_overlay_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "state.json"
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_auto_check(state: dict[str, Any], now: float | None = None) -> bool:
    now = time.time() if now is None else now
    last = float(state.get("last_check_ts") or 0)
    if last <= 0:
        return True
    return (now - last) >= CHECK_INTERVAL_SECONDS


def mark_checked(root: Path | None = None, now: float | None = None) -> None:
    root = root or default_overlay_root()
    state = load_state(root)
    state["last_check_ts"] = time.time() if now is None else now
    save_state(state, root)


def installed_versions() -> dict[str, str]:
    versions = {"yt-dlp": "", "yt-dlp-ejs": ""}
    try:
        import yt_dlp as ydl

        versions["yt-dlp"] = str(ydl.version.__version__) if hasattr(ydl, "version") else "?"
    except Exception:
        pass
    try:
        import yt_dlp_ejs

        versions["yt-dlp-ejs"] = str(getattr(yt_dlp_ejs, "__version__", "?") or "?")
    except Exception:
        pass
    return versions


def check_for_updates(
    current: dict[str, str] | None = None,
    fetch_json: FetchJson | None = None,
) -> CheckResult:
    fetch = fetch_json or fetch_pypi_json
    current = current or installed_versions()
    cur_ydl = current.get("yt-dlp") or ""
    cur_ejs = current.get("yt-dlp-ejs") or ""
    try:
        ydl_wheel = select_wheel(fetch("yt-dlp"))
        ejs_wheel = select_wheel(fetch("yt-dlp-ejs"))
    except Exception as exc:
        return CheckResult(
            available=False,
            current_yt_dlp=cur_ydl,
            latest_yt_dlp="",
            current_ejs=cur_ejs,
            latest_ejs="",
            error=str(exc),
        )
    newer = version_newer(ydl_wheel.version, cur_ydl) or version_newer(
        ejs_wheel.version, cur_ejs
    )
    return CheckResult(
        available=newer,
        current_yt_dlp=cur_ydl,
        latest_yt_dlp=ydl_wheel.version,
        current_ejs=cur_ejs,
        latest_ejs=ejs_wheel.version,
    )


def perform_update(
    root: Path | None = None,
    fetch_json: FetchJson | None = None,
    download: DownloadFn | None = None,
    extract: ExtractFn | None = None,
) -> str:
    root = root or default_overlay_root()
    fetch = fetch_json or fetch_pypi_json
    download_fn = download or download_file
    extract_fn = extract or extract_wheel
    staging = root / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    versions: dict[str, str] = {}
    try:
        for name in PACKAGES:
            wheel = select_wheel(fetch(name))
            versions[name] = wheel.version
            dest = staging / wheel.filename
            download_fn(wheel.url, wheel.sha256, dest)
            extract_fn(dest, staging)
            dest.unlink(missing_ok=True)
        if not (staging / "yt_dlp").is_dir() or not (staging / "yt_dlp_ejs").is_dir():
            raise ValueError("incomplete overlay: both yt_dlp and yt_dlp_ejs required")
        activate_staging(root)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    state = load_state(root)
    state["installed_yt_dlp"] = versions.get("yt-dlp", "")
    state["installed_ejs"] = versions.get("yt-dlp-ejs", "")
    save_state(state, root)
    ydl_ver = versions.get("yt-dlp", "")
    return f"已下载 yt-dlp {ydl_ver}，请重启后生效"


_installed_finder: OverlayFinder | None = None


def install_overlay_finder(root: Path | None = None) -> bool:
    """Insert OverlayFinder at meta_path[0] when an active overlay exists."""
    global _installed_finder
    if not overlay_enabled():
        return False
    root = root or default_overlay_root()
    active = root / "active"
    if not (active / "yt_dlp").is_dir():
        return False
    if _installed_finder is not None and _installed_finder in sys.meta_path:
        sys.meta_path.remove(_installed_finder)
    finder = OverlayFinder(active)
    sys.meta_path.insert(0, finder)
    _installed_finder = finder
    return True
