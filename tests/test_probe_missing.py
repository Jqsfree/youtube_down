"""ffprobe 缺失时抛 ENVIRONMENT 错误，不再落到 UNKNOWN。"""

from pathlib import Path

import pytest
import yt_dlp

import downloader
from downloader import FFPROBE_UNAVAILABLE, YoutubeDownloader, classify_error


def test_run_probe_reports_missing_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(downloader, "_PROJECT_ROOT", tmp_path / "proj")
    monkeypatch.setattr(downloader.sys, "frozen", False, raising=False)

    assert downloader._run_probe(["-version"]) == (False, "")


def test_run_probe_keeps_invalid_file_distinct_from_missing_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ffprobe 能运行但文件无效（returncode=1、无输出）不应被当成「ffprobe 不可用」
    class _Result:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(downloader.subprocess, "run", lambda *a, **k: _Result())
    assert downloader._run_probe(["-version"]) == (True, "")


def test_missing_ffprobe_raises_environment_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(downloader, "_run_probe", lambda *a, **k: (False, ""))

    with pytest.raises(yt_dlp.utils.DownloadError, match="ffprobe"):
        YoutubeDownloader._has_video_stream(tmp_path / "a.mp4")

    cat = classify_error(yt_dlp.utils.DownloadError(FFPROBE_UNAVAILABLE))
    assert cat.code == "ENVIRONMENT"
    assert cat.retry_cookie is False
