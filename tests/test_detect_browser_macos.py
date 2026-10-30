"""macOS Application Support 路径下的浏览器检测。"""

from __future__ import annotations

from pathlib import Path

from downloader import YoutubeDownloader


def test_detect_browser_macos_chrome(tmp_path: Path, monkeypatch) -> None:
    chrome = tmp_path / "Library" / "Application Support" / "Google" / "Chrome"
    (chrome / "Default").mkdir(parents=True)
    (chrome / "Default" / "Cookies").write_bytes(b"x")
    monkeypatch.setattr("downloader.sys.platform", "darwin")
    monkeypatch.setattr("downloader.os.name", "posix")
    monkeypatch.setattr("downloader.os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)

    assert YoutubeDownloader.detect_browser() == ("chrome", "Default")


def test_detect_browser_macos_prefers_chrome_over_edge(tmp_path: Path, monkeypatch) -> None:
    app = tmp_path / "Library" / "Application Support"
    for browser, folder in (
        ("Google/Chrome", "chrome"),
        ("Microsoft Edge", "edge"),
    ):
        root = app / Path(browser)
        (root / "Default").mkdir(parents=True)
        (root / "Default" / "Cookies").write_bytes(b"x")
    monkeypatch.setattr("downloader.sys.platform", "darwin")
    monkeypatch.setattr("downloader.os.name", "posix")
    monkeypatch.setattr("downloader.os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)

    assert YoutubeDownloader.detect_browser() == ("chrome", "Default")


def test_explicit_redetect_does_not_export(monkeypatch):
    monkeypatch.setattr(YoutubeDownloader, "detect_browser", staticmethod(lambda: ("chrome", "Default")))
    monkeypatch.setattr("downloader.dump_browser_cookies", lambda *args: (_ for _ in ()).throw(AssertionError("unexpected export")))
    dl = YoutubeDownloader(cookies_from_browser="")
    assert dl.redetect_browser()
    assert dl._cookies_spec == "chrome:Default"
    assert dl._cookiefile_path is None
