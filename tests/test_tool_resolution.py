"""Tests for _resolve_tool / _find_tool path resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import downloader


@pytest.fixture(autouse=True)
def _clear_path_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setenv("PATH", "")


def test_resolve_tool_from_resources_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res = tmp_path / "resources" / "ffmpeg"
    res.mkdir(parents=True)
    exe = res / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    exe.write_bytes(b"")
    if os.name != "nt":
        exe.chmod(0o755)

    monkeypatch.setattr(downloader, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    resolved = downloader._resolve_tool("ffmpeg")
    assert resolved == str(exe)


def test_resolve_tool_from_meipass_when_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meipass = tmp_path / "meipass"
    meipass.mkdir()
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    bundled = meipass / exe_name
    bundled.write_bytes(b"")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(downloader, "_PROJECT_ROOT", tmp_path / "other")

    resolved = downloader._resolve_tool("ffmpeg")
    assert resolved == str(bundled)


def test_resolve_tool_falls_back_to_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    exe = bin_dir / exe_name
    exe.write_bytes(b"")
    if os.name != "nt":
        exe.chmod(0o755)

    monkeypatch.setattr(downloader, "_PROJECT_ROOT", tmp_path / "proj")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("PATH", str(bin_dir))

    resolved = downloader._resolve_tool("ffmpeg")
    assert resolved is not None
    assert Path(resolved).resolve() == exe.resolve()


def test_find_tool_returns_bare_name_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(downloader, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert downloader._find_tool("ffmpeg") == "ffmpeg"


def test_ffprobe_shares_ffmpeg_resource_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res = tmp_path / "resources" / "ffmpeg"
    res.mkdir(parents=True)
    probe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    probe = res / probe_name
    probe.write_bytes(b"")
    if os.name != "nt":
        probe.chmod(0o755)

    monkeypatch.setattr(downloader, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert downloader._resolve_tool("ffprobe") == str(probe)
