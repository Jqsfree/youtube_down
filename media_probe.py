"""Tool lookup, ffprobe execution and media verification; no UI dependencies."""
from __future__ import annotations
import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Callable
import yt_dlp

_PROJECT_ROOT = Path(__file__).resolve().parent

_RESOURCE_DIRS: dict[str, list[str]] = {
    "ffmpeg": ["ffmpeg"],
    "ffprobe": ["ffmpeg"],
    "deno": ["deno"],
}

FFPROBE_UNAVAILABLE = "ffprobe 不可用：请安装 ffmpeg/ffprobe 或放入 resources/ffmpeg/"

VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".flv")

AUDIO_EXTS = (".m4a", ".mp3", ".opus", ".flac", ".wav")

def tool_filenames(name: str) -> list[str]:
    if os.name == "nt":
        return [f"{name}.exe", name]
    return [name]


def resolve_tool(name: str, root: Path | None = None) -> str | None:
    """解析可执行工具绝对路径：MEIPASS → resources/ → PATH。"""
    root = _PROJECT_ROOT if root is None else root
    for filename in tool_filenames(name):
        if getattr(sys, "frozen", False):
            bundled = os.path.join(sys._MEIPASS, filename)  # noqa: SLF001
            if os.path.isfile(bundled):
                return bundled

        for subdir in _RESOURCE_DIRS.get(name, [name]):
            local = root / "resources" / subdir / filename
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


def find_tool(name: str) -> str:
    """返回可执行文件路径；未找到时回退为 bare name（供 PATH 查找）。"""
    resolved = resolve_tool(name)
    return resolved if resolved else name


def run_probe(args: list[str], timeout: float = 30, executable: str | None = None) -> tuple[bool, str]:
    """运行 ffprobe，返回 (工具是否成功执行, 输出)。

    第一项为 False 表示 ffprobe 缺失/超时（无法判断文件）；文件本身无效时
    第一项仍为 True、输出为空，由调用方给出更准确的错误。
    """
    try:
        result = subprocess.run(
            [executable or find_tool("ffprobe"), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return True, result.stdout or ""


def has_video_stream(path: Path, probe: Callable | None = None) -> bool:
    """确认输出文件包含视频轨，避免仅音频文件被当成成功。"""
    probe = probe or run_probe
    ok, out = probe(
        [
            "-v", "quiet", "-select_streams", "v:0",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path),
        ]
    )
    if not ok:
        raise yt_dlp.utils.DownloadError(FFPROBE_UNAVAILABLE)
    return out.strip() == "video"


def audio_codec(path: Path, probe: Callable | None = None) -> str | None:
    """返回首个音轨 codec_name；无音轨时返回 None。"""
    probe = probe or run_probe
    ok, out = probe(
        [
            "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path),
        ]
    )
    if not ok:
        raise yt_dlp.utils.DownloadError(FFPROBE_UNAVAILABLE)
    return out.strip() or None


def verify_duration(path: Path, expected_duration: float | str | None, probe: Callable | None = None) -> None:
    probe = probe or run_probe
    # 校验文件是有效媒体（ffprobe 解析 + 时长比对）
    probed, duration_out = probe(
        ["-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)]
    )
    if not probed:
        raise yt_dlp.utils.DownloadError(FFPROBE_UNAVAILABLE)
    if not duration_out.strip():
        raise yt_dlp.utils.DownloadError(
            f"下载文件无法解析为有效媒体: {path}"
        )
    actual_duration = float(duration_out.strip())
    expected_duration = float(expected_duration or 0)
    if (
        expected_duration > 0
        and abs(actual_duration - expected_duration) / expected_duration > 0.05
    ):
        raise yt_dlp.utils.DownloadError(
            f"下载文件时长异常: 期望 {expected_duration:.0f}s,"
            f" 实际 {actual_duration:.0f}s (可能是合并中断)"
        )



def verify_height(path: Path, min_height: int = 0, expected_height: int | None = None, probe: Callable | None = None) -> None:
    probe = probe or run_probe
    # expected_height<=0 视为未指定（避免与真实分辨率做 0p 精确比对）
    if expected_height is not None and expected_height <= 0:
        expected_height = None
    if (
        expected_height is not None or (min_height and min_height > 0)
    ):
        probed, height_str = probe(
            ["-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0", str(path)],
            timeout=10,
        )
        if not probed:
            raise yt_dlp.utils.DownloadError(FFPROBE_UNAVAILABLE)
        height_str = height_str.strip()
        if not height_str:
            raise yt_dlp.utils.DownloadError(
                "无法校验视频分辨率：ffprobe 未返回宽高"
            )
        try:
            parts = [p.strip() for p in height_str.split(",") if p.strip()]
            dims = [int(p) for p in parts[:2]]
            # 竖屏按短边计档（与 format_height 一致）
            actual_height = min(dims) if len(dims) >= 2 else dims[0]
            if expected_height is not None:
                tolerance = max(8, int(expected_height * 0.02))
                if abs(actual_height - expected_height) > tolerance:
                    raise yt_dlp.utils.DownloadError(
                        f"下载视频分辨率 ({actual_height}p)"
                        f" 与目标档位 ({expected_height}p) 不符"
                    )
            elif actual_height < min_height:
                raise yt_dlp.utils.DownloadError(
                    f"下载视频分辨率 ({actual_height}p)"
                    f" 低于最低要求 ({min_height}p)"
                )
        except ValueError as exc:
            raise yt_dlp.utils.DownloadError(
                f"无法解析视频分辨率用于校验: {height_str!r}"
            ) from exc




def verify_file_size(path: Path) -> None:
    # 校验文件大小合理（> 1KB）
    if path.stat().st_size <= 1024:
        raise yt_dlp.utils.DownloadError(
            f"下载文件过小 ({path.stat().st_size} bytes): {path}"
        )
