"""批次 1 新增逻辑：限速解析、播放列表展开、选项合并、仅音频分支。"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import downloader
from downloader import (
    YoutubeDownloader,
    looks_like_playlist,
    parse_rate,
)
from worker import BatchDownloadWorker, _resume_min_height


def _bare_downloader() -> YoutubeDownloader:
    dl = YoutubeDownloader.__new__(YoutubeDownloader)
    dl._cancelled = False
    dl._cancel_lock = threading.Lock()
    dl._yt_dlp_lock = threading.RLock()
    dl._cookiefile_path = None
    dl._cookiefile_platform = None
    dl._platform_cookiefiles = {}
    dl._cookies_spec = None
    dl._browser_cookie_broken = False
    dl._ydl_opt_overrides = {}
    dl._cancel_coordinator = None
    return dl


def test_parse_rate() -> None:
    assert parse_rate("5M") == 5 * 1024**2
    assert parse_rate("500k") == 500 * 1024
    assert parse_rate("1048576") == 1048576
    assert parse_rate("") is None
    assert parse_rate("不限速") is None
    assert parse_rate("0") is None


def test_looks_like_playlist() -> None:
    assert looks_like_playlist("https://www.youtube.com/playlist?list=PL123")
    assert looks_like_playlist("https://www.youtube.com/@someone")
    assert looks_like_playlist("https://space.bilibili.com/123456")
    assert not looks_like_playlist("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123")
    assert not looks_like_playlist("https://youtu.be/dQw4w9WgXcQ")
    assert not looks_like_playlist("https://www.youtube.com/shorts/S4d2AB_unnw")


class _FakeYDL:
    def __init__(self, info=None, error: Exception | None = None) -> None:
        self._info = info
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False):
        if self._error:
            raise self._error
        return self._info


def test_expand_playlist_dedups_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    info = {
        "entries": [
            {"url": "dQw4w9WgXcQ"},
            {"webpage_url": "https://www.youtube.com/watch?v=RajrwxvF4Yw"},
            {"url": "dQw4w9WgXcQ"},
            None,
        ]
    }
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", lambda opts: _FakeYDL(info))
    dl = _bare_downloader()

    assert dl.expand_playlist("https://www.youtube.com/playlist?list=PL1") == [
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=RajrwxvF4Yw",
    ]
    assert dl.expand_playlist("https://www.youtube.com/playlist?list=PL1", limit=1) == [
        "dQw4w9WgXcQ"
    ]


def test_expand_playlist_returns_original_without_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", lambda opts: _FakeYDL({"id": "x"}))
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert _bare_downloader().expand_playlist(url) == [url]


def test_expand_playlist_falls_back_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = RuntimeError("boom")
    monkeypatch.setattr(
        downloader.yt_dlp, "YoutubeDL", lambda opts: _FakeYDL(error=error)
    )
    # 普通视频：展开失败仍按单条排队
    video = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert _bare_downloader().expand_playlist(video) == [video]
    # 播放列表：展开失败返回空，避免把整条列表当单视频下载
    playlist = "https://www.youtube.com/playlist?list=PL1"
    assert _bare_downloader().expand_playlist(playlist) == []


def test_expand_playlist_empty_playlist_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        downloader.yt_dlp, "YoutubeDL", lambda opts: _FakeYDL({"entries": []})
    )
    playlist = "https://www.youtube.com/playlist?list=PL1"
    assert _bare_downloader().expand_playlist(playlist) == []


def test_make_opts_merges_postprocessors_and_network_options() -> None:
    dl = _bare_downloader()
    dl._ydl_opt_overrides = {
        "postprocessors": [{"key": "FFmpegMetadata"}],
        "proxy": "http://127.0.0.1:7897",
        "ratelimit": 1024,
    }

    opts = dl._make_opts(
        use_cookies=False, postprocessors=[{"key": "FFmpegExtractAudio"}]
    )

    assert [p["key"] for p in opts["postprocessors"]] == [
        "FFmpegExtractAudio",
        "FFmpegMetadata",
    ]
    assert opts["proxy"] == "http://127.0.0.1:7897"
    assert opts["ratelimit"] == 1024
    assert "impersonate" not in opts


def test_impersonate_only_for_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    dl = _bare_downloader()
    dl._impersonate_extract = True

    assert "impersonate" not in dl._make_opts(use_cookies=False)
    assert dl._make_opts(use_cookies=False, for_extraction=True)["impersonate"] == "chrome"


class _AudioDownloader:
    def __init__(self) -> None:
        self._cancelled = False
        self.download_kwargs: dict = {}

    def clear_cancel(self) -> None:
        self._cancelled = False

    def has_cookie_for_source(self, source: str) -> bool:
        return False

    def get_info(self, source: str, use_cookies: bool = True, **kwargs):
        return {"title": "demo", "duration": 60}

    def list_formats(self, info):
        raise AssertionError("仅音频模式不应调用 list_formats")

    def download(self, video_id: str, format_id: str, output_dir: Path, **kwargs):
        self.download_kwargs = {"format_id": format_id, **kwargs}
        return output_dir / "0001_demo.m4a"


def test_audio_only_skips_format_list_and_passes_conversion(tmp_path: Path) -> None:
    dl = _AudioDownloader()
    worker = BatchDownloadWorker(
        downloader=dl,
        video_ids=["dQw4w9WgXcQ"],
        format_id="auto",
        output_dir=tmp_path,
        audio_only=True,
        audio_format="mp3",
    )

    worker.run()

    assert dl.download_kwargs["format_id"] == "bestaudio/best"
    assert dl.download_kwargs["audio_only"] is True
    assert dl.download_kwargs["audio_format"] == "mp3"


def test_check_existing_audio_mode_matches_audio_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import worker as worker_mod

    audio = tmp_path / "0001_demo.m4a"
    audio.write_bytes(b"x" * 2048)

    def fake_probe(args: list[str], timeout: float = 0):
        return True, ("video" if "codec_type" in args else "120")

    monkeypatch.setattr(worker_mod, "_run_probe", fake_probe)

    assert worker_mod._check_existing("demo", tmp_path, audio_only=True) == audio
    assert worker_mod._check_existing("demo", tmp_path) is None


def test_stash_existing_restores_old_file_when_download_fails(
    tmp_path: Path,
) -> None:
    from downloader import (
        VIDEO_EXTS,
        _finish_stashed_files,
        _stash_existing_files,
    )

    old = tmp_path / "0001_demo.mp4"
    old.write_bytes(b"old" * 1000)
    audio = tmp_path / "0001_demo.m4a"
    audio.write_bytes(b"x" * 2048)
    other = tmp_path / "0002_other.mp4"
    other.write_bytes(b"x" * 2048)

    stashed = _stash_existing_files(tmp_path, "0001_demo", VIDEO_EXTS)
    assert not old.exists()  # 旧文件已暂存，yt-dlp 不会误判为已下载
    assert audio.exists() and other.exists()

    (tmp_path / "0001_demo.mp4").write_bytes(b"new" * 1000)
    _finish_stashed_files(stashed, False)

    assert old.read_bytes() == b"old" * 1000  # 失败后旧文件恢复
    assert not list(tmp_path.glob("*.bak"))
    assert audio.exists() and other.exists()


def test_stash_existing_discards_old_file_on_success(tmp_path: Path) -> None:
    from downloader import (
        VIDEO_EXTS,
        _finish_stashed_files,
        _stash_existing_files,
    )

    old = tmp_path / "0001_demo.mp4"
    old.write_bytes(b"old" * 1000)
    stashed = _stash_existing_files(tmp_path, "0001_demo", VIDEO_EXTS)

    new = tmp_path / "0001_demo.mp4"
    new.write_bytes(b"new" * 1000)
    _finish_stashed_files(stashed, True)

    assert new.read_bytes() == b"new" * 1000
    assert not list(tmp_path.glob("*.bak"))


def test_download_restores_existing_file_when_yt_dlp_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """download() 的 try/finally 集成：yt-dlp 抛错时旧文件必须回来。"""
    old = tmp_path / "0001_demo.mp4"
    old.write_bytes(b"old" * 1000)

    class _BoomYDL:
        def __init__(self, opts) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True):
            raise RuntimeError("boom")

    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _BoomYDL)
    dl = _bare_downloader()

    with pytest.raises(RuntimeError):
        dl.download(
            "dQw4w9WgXcQ", "18", tmp_path, use_cookies=False,
            filename_stem="0001_demo",
        )

    assert old.read_bytes() == b"old" * 1000
    assert not list(tmp_path.glob("*.bak"))


def test_stash_recovers_orphan_backup_from_crash(tmp_path: Path) -> None:
    """上次进程在暂存后被杀：只剩 .bak，下次下载前必须还原。"""
    from downloader import (
        VIDEO_EXTS,
        _finish_stashed_files,
        _stash_existing_files,
    )

    bak = tmp_path / ".0001_demo.mp4.bak"
    bak.write_bytes(b"old")
    stashed = _stash_existing_files(tmp_path, "0001_demo", VIDEO_EXTS)

    _finish_stashed_files(stashed, False)

    assert (tmp_path / "0001_demo.mp4").read_bytes() == b"old"
    assert not list(tmp_path.glob("*.bak"))


def test_cli_ui_overrides() -> None:
    import argparse

    import download_cli

    args = argparse.Namespace(
        subs=True, subs_langs="zh-Hans,en", audio=False, metadata=True, thumbnail=True,
        proxy="http://127.0.0.1:7897", limit_rate="5M",
    )
    overrides = download_cli._ui_overrides(args)

    assert overrides["subtitleslangs"] == ["zh-Hans", "en"]

    assert overrides["writesubtitles"] is True
    assert overrides["writethumbnail"] is True
    assert overrides["proxy"] == "http://127.0.0.1:7897"
    assert overrides["ratelimit"] == 5 * 1024**2
    assert [p["key"] for p in overrides["postprocessors"]] == [
        "FFmpegEmbedSubtitle",
        "FFmpegMetadata",
        "EmbedThumbnail",
    ]

    args.subs = False
    args.audio = True
    assert "writesubtitles" not in download_cli._ui_overrides(args)


def test_generic_direct_url_skips_quality_gate() -> None:
    worker = BatchDownloadWorker.__new__(BatchDownloadWorker)
    worker._format_id = "auto"
    worker._audio_only = False
    worker._strict_quality = True
    worker._min_height = 720
    worker._downloader = type("D", (), {"list_formats": lambda self, info=None: []})()

    assert worker._resolve_format({"_platform": "generic", "formats": []}) == ("best", False, 0)


def test_generic_resume_ignores_min_height() -> None:
    assert _resume_min_height("https://example.com/a.mp4", 720) == 0
    assert _resume_min_height("https://www.youtube.com/watch?v=dQw4w9WgXcQ", 720) == 720


def test_best_selector_falls_back_to_best() -> None:
    assert YoutubeDownloader.build_format_selector("best", min_height=0).endswith("/best")


def test_proxy_precheck() -> None:
    import socket

    from downloader import proxy_reachable

    assert proxy_reachable("")
    assert proxy_reachable("http://127.0.0.1")  # 没写端口，交给 yt-dlp
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()
    assert not proxy_reachable(f"http://127.0.0.1:{closed_port}", timeout=0.5)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        assert proxy_reachable(f"http://127.0.0.1:{listener.getsockname()[1]}", timeout=0.5)
    finally:
        listener.close()
