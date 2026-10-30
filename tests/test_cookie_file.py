"""Tests for manual cookie file support."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from downloader import YoutubeDownloader


@pytest.fixture
def cookie_txt(tmp_path: Path) -> Path:
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tVISITOR_INFO1_LIVE\ttest\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tCONSENT\tYES+1\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def bilibili_cookie_txt(tmp_path: Path) -> Path:
    path = tmp_path / "bilibili_cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t1893456000\tSESSDATA\ttest\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t1893456000\tbili_jct\tcsrf\n",
        encoding="utf-8",
    )
    return path


def test_set_cookiefile_clears_browser_spec(cookie_txt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        YoutubeDownloader, "detect_browser", staticmethod(lambda: ("edge", "Default"))
    )
    dl = YoutubeDownloader(cookies_from_browser="edge:Default")
    assert dl._cookies_spec == "edge:Default"

    dl.set_cookiefile(cookie_txt, persist=False)
    assert dl._cookiefile_path == cookie_txt.resolve()
    assert dl._cookiefile_platform == "youtube"
    assert dl._cookies_spec is None
    assert dl.cookie_source() == f"YouTube:{cookie_txt.name}"


def test_clear_cookiefile_disables_browser(cookie_txt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        YoutubeDownloader, "detect_browser", staticmethod(lambda: ("chrome", "Default"))
    )
    dl = YoutubeDownloader()
    dl.set_cookiefile(cookie_txt, persist=False)
    dl.clear_cookiefile(persist=False)
    assert dl._cookiefile_path is None
    assert dl._cookies_spec is None


def test_persist_cookiefile(tmp_path: Path, cookie_txt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "cfg"
    config_file = config_dir / "cookiefile.txt"
    monkeypatch.setattr("downloader._COOKIE_CONFIG", config_file)
    monkeypatch.setattr(
        "downloader._PLATFORM_COOKIE_CONFIGS",
        {
            "youtube": config_dir / "youtube_cookiefile.txt",
            "bilibili": config_dir / "bilibili_cookiefile.txt",
        },
    )
    monkeypatch.setattr(
        YoutubeDownloader, "detect_browser", staticmethod(lambda: None)
    )

    dl = YoutubeDownloader(cookies_from_browser="")
    dl.set_cookiefile(cookie_txt, persist=True)
    assert config_file.read_text(encoding="utf-8").strip() == str(cookie_txt.resolve())

    dl2 = YoutubeDownloader(cookies_from_browser=None)
    assert dl2._cookiefile_path == cookie_txt.resolve()


def test_set_cookiefile_missing_raises(tmp_path: Path) -> None:
    dl = YoutubeDownloader(cookies_from_browser="")
    with pytest.raises(ValueError, match="文件不存在"):
        dl.set_cookiefile(tmp_path / "missing.txt", persist=False)


def test_validate_cookie_file_rejects_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.txt"
    bad.write_text('{"cookies": []}', encoding="utf-8")
    ok, msg = YoutubeDownloader.validate_cookie_file(bad)
    assert not ok
    assert "youtube.com" in msg or "bilibili.com" in msg or "Netscape" in msg


def test_set_bilibili_cookiefile_sets_platform(bilibili_cookie_txt: Path) -> None:
    dl = YoutubeDownloader(cookies_from_browser="")
    dl.set_cookiefile(bilibili_cookie_txt, persist=False)
    assert dl._cookiefile_path == bilibili_cookie_txt.resolve()
    assert dl._cookiefile_platform == "bilibili"
    assert dl.cookie_source() == f"Bilibili:{bilibili_cookie_txt.name}"


def test_validate_cookie_file_accepts_bilibili(bilibili_cookie_txt: Path) -> None:
    ok, msg = YoutubeDownloader.validate_cookie_file(bilibili_cookie_txt)
    assert ok
    assert "Bilibili" in msg


def test_validate_cookies_requires_deno_for_file(cookie_txt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        YoutubeDownloader, "detect_browser", staticmethod(lambda: None)
    )
    monkeypatch.setattr("downloader._resolve_tool", lambda name: None)
    dl = YoutubeDownloader(cookies_from_browser="")
    dl.set_cookiefile(cookie_txt, persist=False)
    ok, msg = dl.validate_cookies()
    assert not ok
    assert "deno" in msg.lower()


def test_default_does_not_select_browser(monkeypatch):
    monkeypatch.setattr(YoutubeDownloader, "_load_saved_cookiefiles", staticmethod(lambda: {}))
    monkeypatch.setattr(YoutubeDownloader, "_load_saved_cookiefile", staticmethod(lambda: None))
    monkeypatch.setattr(YoutubeDownloader, "detect_browser", staticmethod(lambda: (_ for _ in ()).throw(AssertionError("browser accessed"))))
    assert YoutubeDownloader()._cookies_spec is None


def test_cookie_copy_filtered_and_removed_on_exception(cookie_txt, monkeypatch):
    import downloader
    cookie_txt.write_text(cookie_txt.read_text() + ".evil-youtube.com\tTRUE\t/\tTRUE\t1893456000\tSECRET\tprivate\n")
    original = cookie_txt.read_bytes()
    seen = []
    class FakeYDL:
        def __init__(self, opts):
            self.path = Path(opts["cookiefile"])
            seen.append(self.path)
            assert self.path != cookie_txt
            assert "SECRET" not in self.path.read_text()
            if os.name != "nt":
                assert self.path.stat().st_mode & 0o777 == 0o600
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.path.write_text("changed by yt-dlp")
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    import pytest
    with pytest.raises(RuntimeError):
        with downloader._open_ydl({"cookiefile": str(cookie_txt)}, "youtube"):
            assert seen[0].exists()
            raise RuntimeError("cancelled")
    assert not seen[0].exists()
    assert cookie_txt.read_bytes() == original


def test_concurrent_cookie_copies_have_independent_lifetimes(cookie_txt, monkeypatch):
    import downloader
    class FakeYDL:
        def __init__(self, opts): self.path = Path(opts["cookiefile"])
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    with downloader._open_ydl({"cookiefile": str(cookie_txt)}, "youtube") as first:
        with downloader._open_ydl({"cookiefile": str(cookie_txt)}, "youtube") as second:
            assert first.path != second.path
        assert first.path.exists()
        assert not second.path.exists()
    assert not first.path.exists()


def test_export_filters_before_saving(tmp_path, monkeypatch):
    from http.cookiejar import Cookie
    from yt_dlp.cookies import YoutubeDLCookieJar
    import downloader
    jar = YoutubeDLCookieJar()
    for domain in (".youtube.com", ".bilibili.com", ".tiktok.com", ".example.com", ".evilyoutube.com"):
        jar.set_cookie(Cookie(0, "SID", "fake", None, False, domain, True, True, "/", True, False, 1893456000, False, None, None, {}))
    class FakeYDL:
        def __init__(self, opts):
            assert "cookiefile" not in opts  # prevents automatic full-jar persistence
            self.cookiejar = jar
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    dest = tmp_path / "export.txt"
    downloader.dump_browser_cookies("chrome", dest)
    text = dest.read_text()
    assert "example.com" not in text and "evilyoutube.com" not in text
    assert "youtube.com" in text and "bilibili.com" in text and "tiktok.com" in text
    if os.name != "nt":
        assert dest.stat().st_mode & 0o777 == 0o600


def test_export_never_overwrites_existing_file(tmp_path, monkeypatch):
    import downloader
    dest = tmp_path / "original.txt"
    dest.write_text("original")
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", lambda *args: (_ for _ in ()).throw(RuntimeError("browser locked")))
    with pytest.raises((ValueError, FileExistsError)):
        downloader.dump_browser_cookies("chrome", dest)
    assert dest.read_text() == "original"
