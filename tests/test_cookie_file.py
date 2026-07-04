"""Tests for manual cookie file support."""

from __future__ import annotations

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


def test_set_cookiefile_clears_browser_spec(cookie_txt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        YoutubeDownloader, "detect_browser", staticmethod(lambda: ("edge", "Default"))
    )
    dl = YoutubeDownloader(cookies_from_browser="edge:Default")
    assert dl._cookies_spec == "edge:Default"

    dl.set_cookiefile(cookie_txt, persist=False)
    assert dl._cookiefile_path == cookie_txt.resolve()
    assert dl._cookies_spec is None
    assert dl.cookie_source() == f"file:{cookie_txt.name}"


def test_clear_cookiefile_redetects_browser(cookie_txt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        YoutubeDownloader, "detect_browser", staticmethod(lambda: ("chrome", "Default"))
    )
    dl = YoutubeDownloader()
    dl.set_cookiefile(cookie_txt, persist=False)
    dl.clear_cookiefile(persist=False)
    assert dl._cookiefile_path is None
    assert dl._cookies_spec == "chrome:Default"


def test_persist_cookiefile(tmp_path: Path, cookie_txt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "cfg"
    config_file = config_dir / "cookiefile.txt"
    monkeypatch.setattr("downloader._COOKIE_CONFIG", config_file)
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
    assert "youtube.com" in msg or "Netscape" in msg


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
