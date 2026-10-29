"""应用自更新：从 GitHub Release 检查、下载并替换自身。

Must not import PySide6. Must not import yt_dlp.

流程：
  1. ``check_for_app_update()`` 读取 ``VERSION`` 与 GitHub latest release 比较。
  2. ``download_release_asset()`` 流式下载到临时目录并校验大小。
  3. ``install_and_restart()`` 写一段平台脚本，退出后替换可执行文件并重启。

只对打包后的发布版生效（``sys.frozen``）；源码运行时由 GUI 打开 Releases 页。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from yt_dlp_updater import version_newer

REPO = "Jqsfree/youtube_down"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

# 资产名与 .github/workflows/build.yml 的 artifact_name 保持一致
ASSET_WINDOWS = "YouTubeDownloader-Windows.exe"
ASSET_MACOS = "YouTubeDownloader-macOS.zip"
# release 里记录各资产 sha256 的清单（build.yml 生成），有则强制校验
ASSET_CHECKSUMS = "SHA256SUMS.txt"

ALLOWED_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
})
# GitHub 的资产下载会跳到这些 CDN，主机名可能变（objects / release-assets / ...）
ALLOWED_HOST_SUFFIXES = (".githubusercontent.com",)

CHECK_INTERVAL_SECONDS = 24 * 3600
_USER_AGENT = "MultiPlatformDownloader/app-updater"

ProgressCb = Callable[[int], None]


# ----------------------------------------------------------------------
# 版本 / 环境
# ----------------------------------------------------------------------

def app_root() -> Path:
    """源码根目录或 PyInstaller 解包目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def read_version(root: Path | None = None) -> str:
    """读取 ``VERSION``；打包后随 ``--add-data`` 一起解包到 ``_MEIPASS``。"""
    for base in (root, app_root(), Path(__file__).resolve().parent):
        if base is None:
            continue
        candidate = Path(base) / "VERSION"
        try:
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            continue
    return ""


def current_version() -> str:
    return read_version()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path | None:
    """发布版自身所在路径（Windows 为 exe 文件，macOS 为 .app 包）。"""
    if not is_frozen():
        return None
    exe = Path(sys.executable)
    if sys.platform == "darwin":
        # .../Foo.app/Contents/MacOS/Foo → Foo.app
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return None
    return exe


def asset_name_for(platform: str | None = None) -> str:
    plat = platform or sys.platform
    if plat.startswith("win"):
        return ASSET_WINDOWS
    if plat == "darwin":
        return ASSET_MACOS
    return ""


def is_prerelease(version: str) -> bool:
    """预发布判定：只看 ``-`` / ``+`` 之后的标识，避免 1.2.0 里的字母误判。"""
    lowered = (version or "").lower().lstrip("v")
    suffix = re.split(r"[-+]", lowered, maxsplit=1)
    if len(suffix) < 2:
        return False
    return any(
        token in suffix[1] for token in ("rc", "beta", "alpha", "pre", "dev")
    )


# ----------------------------------------------------------------------
# GitHub Release
# ----------------------------------------------------------------------

@dataclass
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass
class Release:
    version: str
    tag: str
    page_url: str
    notes: str = ""
    assets: list[Asset] = field(default_factory=list)

    def asset_for(self, platform: str | None = None) -> Asset | None:
        wanted = asset_name_for(platform)
        if not wanted:
            return None
        for asset in self.assets:
            if asset.name == wanted:
                return asset
        return None

    def checksums_asset(self) -> Asset | None:
        for asset in self.assets:
            if asset.name.lower() == ASSET_CHECKSUMS.lower():
                return asset
        return None


@dataclass
class UpdateCheck:
    available: bool
    current: str
    latest: str
    release: Release | None = None
    error: str = ""


def assert_allowed_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("url must be https")
    host = (parsed.hostname or "").lower()
    if host in ALLOWED_HOSTS:
        return
    if any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        return
    raise ValueError(f"disallowed host: {host}")


def parse_release(payload: dict[str, Any]) -> Release:
    """把 GitHub API 的 release JSON 转成 Release。"""
    tag = str(payload.get("tag_name") or "")
    version = tag[1:] if tag[:1] in ("v", "V") else tag
    assets = [
        Asset(
            name=str(item.get("name") or ""),
            url=str(item.get("browser_download_url") or ""),
            size=int(item.get("size") or 0),
        )
        for item in payload.get("assets") or []
        if item.get("name")
    ]
    return Release(
        version=version,
        tag=tag,
        page_url=str(payload.get("html_url") or RELEASES_PAGE),
        notes=str(payload.get("body") or ""),
        assets=assets,
    )


def _urlopen(url: str, timeout: float = 30):
    assert_allowed_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    resp = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - 已白名单校验
    # urllib 会自动跟随 302（release 资产正是跳到 objects.githubusercontent.com），
    # 落点也要校验，否则白名单只约束了第一跳。
    assert_allowed_url(resp.geturl())
    return resp


def fetch_latest_release(timeout: float = 20) -> Release:
    with _urlopen(RELEASES_API, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_release(payload)


def check_for_app_update(
    current: str | None = None, timeout: float = 20,
) -> UpdateCheck:
    """比较本地 VERSION 与 GitHub latest release。"""
    current = current if current is not None else current_version()
    try:
        release = fetch_latest_release(timeout=timeout)
    except Exception as exc:  # 网络 / 解析 / 白名单
        return UpdateCheck(False, current, "", None, str(exc))
    latest = release.version
    if not latest:
        return UpdateCheck(False, current, "", release, "release 缺少版本号")
    available = version_newer(latest, current) and not is_prerelease(latest)
    return UpdateCheck(available, current, latest, release)


# ----------------------------------------------------------------------
# 下载
# ----------------------------------------------------------------------

def download_release_asset(
    asset: Asset,
    dest_dir: Path,
    progress: ProgressCb | None = None,
    chunk_size: int = 1 << 20,
) -> Path:
    """流式下载 release 资产，返回本地路径；大小不符时抛错。"""
    assert_allowed_url(asset.url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (asset.name or "update.bin")
    tmp = dest.with_suffix(dest.suffix + ".part")
    received = 0
    with _urlopen(asset.url, timeout=60) as resp, open(tmp, "wb") as fh:
        total = asset.size or int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            fh.write(chunk)
            received += len(chunk)
            if progress and total > 0:
                progress(min(100, int(received / total * 100)))
    if asset.size and received != asset.size:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"下载不完整：{received} != {asset.size} 字节")
    tmp.replace(dest)
    if progress:
        progress(100)
    return dest


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums(text: str) -> dict[str, str]:
    """解析 ``<sha256>  <filename>`` 清单（兼容前缀 ``*``）。"""
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        digest = parts[0].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        name = parts[-1].lstrip("*").strip()
        if name:
            result[name] = digest
    return result


def verify_asset_checksum(path: Path, expected: str) -> None:
    actual = sha256_of(path)
    if actual != expected.lower():
        path.unlink(missing_ok=True)
        raise ValueError(f"校验和不匹配：{actual[:12]}… != {expected[:12]}…")


def fetch_checksums(release: Release, dest_dir: Path) -> dict[str, str]:
    """下载并解析 release 的清单；没有清单时返回空 dict。"""
    asset = release.checksums_asset()
    if asset is None:
        return {}
    path = download_release_asset(asset, dest_dir)
    return parse_checksums(path.read_text(encoding="utf-8", errors="replace"))


def download_update(release: Release, dest_dir: Path,
                     progress: ProgressCb | None = None) -> Path:
    """下载当前平台的更新包；release 带清单时强制校验 sha256。"""
    asset = release.asset_for()
    if asset is None:
        raise ValueError(f"release 里没有当前平台的安装包：{asset_name_for()}")
    path = download_release_asset(asset, dest_dir, progress=progress)
    checksums = fetch_checksums(release, dest_dir)
    expected = checksums.get(asset.name)
    if expected:
        verify_asset_checksum(path, expected)
    return path


# ----------------------------------------------------------------------
# 安装 / 重启
# ----------------------------------------------------------------------

def _spawn_detached(script: Path, args: list[str] | None = None) -> None:
    if sys.platform.startswith("win"):
        creation = 0
        for flag in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            creation |= int(getattr(subprocess, flag, 0))
        subprocess.Popen(  # noqa: S603 - 固定脚本
            ["cmd", "/c", str(script)] + list(args or []),
            creationflags=creation,
            close_fds=True,
        )
        return
    subprocess.Popen(  # noqa: S603 - 固定脚本
        ["/bin/sh", str(script)] + list(args or []),
        start_new_session=True,
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_windows_script(payload: Path, target: Path) -> Path:
    """写一个批处理：等旧进程退出 → 替换 exe → 启动新 exe → 自删。"""
    script = Path(tempfile.gettempdir()) / f"ytdown-update-{os.getpid()}.bat"
    old = target.with_name(target.name + ".old")
    lines = [
        "@echo off",
        "setlocal",
        "ping 127.0.0.1 -n 4 >nul",
        f'set "NEW={payload}"',
        f'set "TARGET={target}"',
        f'set "OLD={old}"',
        'if exist "%OLD%" del /f /q "%OLD%" >nul 2>&1',
        "set /a tries=0",
        ":retry",
        'move /y "%TARGET%" "%OLD%" >nul 2>&1',
        'if errorlevel 1 (',
        "  set /a tries+=1",
        "  if %tries% GEQ 30 goto rollback",
        "  ping 127.0.0.1 -n 2 >nul",
        "  goto retry",
        ")",
        'copy /y "%NEW%" "%TARGET%" >nul 2>&1',
        # 复制失败时 TARGET 已被搬走，必须搬回来，否则用户两头落空
        "if errorlevel 1 goto rollback",
        'if exist "%OLD%" del /f /q "%OLD%" >nul 2>&1',
        'start "" "%TARGET%"',
        "goto done",
        ":rollback",
        'if not exist "%TARGET%" move /y "%OLD%" "%TARGET%" >nul 2>&1',
        'start "" "%TARGET%"',
        ":done",
        'del /f /q "%~f0" >nul 2>&1',
    ]
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return script


def _write_macos_script(payload: Path, target: Path) -> Path:
    """解压 zip → 备份旧 .app → 覆盖 → 去隔离属性 → 打开。"""
    script = Path(tempfile.gettempdir()) / f"ytdown-update-{os.getpid()}.sh"
    old = target.with_name(target.name + ".old")
    lines = [
        "#!/bin/sh",
        f'NEW="{payload}"',
        f'TARGET="{target}"',
        f'OLD="{old}"',
        "sleep 2",
        'rm -rf "$OLD"',
        'WORK=$(mktemp -d)',
        'ditto -x -k "$NEW" "$WORK" || exit 1',
        'APP=$(find "$WORK" -maxdepth 1 -name "*.app" | head -n 1)',
        'if [ -z "$APP" ]; then exit 1; fi',
        'mv "$TARGET" "$OLD" || exit 1',
        # 覆盖失败必须回滚，否则用户既没有旧版也没有新版
        'if ! cp -R "$APP" "$TARGET"; then',
        '  rm -rf "$TARGET"',
        '  mv "$OLD" "$TARGET"',
        '  rm -rf "$WORK"',
        '  open -n "$TARGET"',
        '  exit 1',
        'fi',
        'xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null',
        'rm -rf "$WORK" "$OLD" "$NEW"',
        'open -n "$TARGET"',
    ]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def install_and_restart(payload: Path | None = None) -> str:
    """退出后替换自身并重启；返回给用户看的提示文字。"""
    if not is_frozen():
        return f"源码运行无法自动替换，请手动下载：{RELEASES_PAGE}"
    target = install_root()
    if target is None:
        return f"未找到安装路径，请手动下载：{RELEASES_PAGE}"
    if payload is None or not Path(payload).is_file():
        return f"安装包缺失，请手动下载：{RELEASES_PAGE}"

    payload = Path(payload)
    # /Applications 通常需要管理员授权；提前告知，别让脚本静默失败
    if not os.access(target.parent, os.W_OK):
        return (
            f"没有写入 {target.parent} 的权限，请手动替换或把应用放到用户目录后再试："
            f"{RELEASES_PAGE}"
        )
    if sys.platform == "darwin":
        script = _write_macos_script(payload, target)
    elif sys.platform.startswith("win"):
        script = _write_windows_script(payload, target)
    else:
        return f"当前系统不支持自动替换，请手动下载：{RELEASES_PAGE}"
    _spawn_detached(script)
    return "正在重启并安装新版本…"


def cleanup_old() -> None:
    """删除上次更新残留的 ``*.old`` 与下载缓存。"""
    _cleanup_stale_downloads()
    target = install_root()
    if target is None:
        return
    old = target.with_name(target.name + ".old")
    if sys.platform == "darwin":
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        return
    try:
        old.unlink(missing_ok=True)
    except OSError:
        pass


DOWNLOAD_CACHE_PREFIX = "ytdown-update-"


def _cleanup_stale_downloads(max_age: float = 3 * 24 * 3600) -> None:
    """清理超过 ``max_age`` 秒的更新下载缓存（不含正在用的）。"""
    temp = Path(tempfile.gettempdir())
    try:
        entries = list(temp.glob(f"{DOWNLOAD_CACHE_PREFIX}*"))
    except OSError:
        return
    now = time.time()
    for entry in entries:
        try:
            if now - entry.stat().st_mtime < max_age:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except OSError:
            continue


# ----------------------------------------------------------------------
# 检查节流（24h）
# ----------------------------------------------------------------------

def should_auto_check(last_check: float, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if last_check <= 0:
        return True
    return (now - last_check) >= CHECK_INTERVAL_SECONDS
