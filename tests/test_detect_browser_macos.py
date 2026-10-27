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


def test_try_autoload_browser_cookies_success(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "auto.txt"
    netscape = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tFALSE\t0\tSID\txvalue\n"
        ".youtube.com\tTRUE\t/\tFALSE\t0\tHSID\tyvalue\n"
    )
    monkeypatch.setattr("downloader._AUTO_BROWSER_COOKIE_FILE", dest)
    monkeypatch.setattr(
        "downloader.dump_browser_cookies",
        lambda spec, path: path.write_text(netscape, encoding="utf-8") or path,
    )
    dl = YoutubeDownloader.__new__(YoutubeDownloader)
    dl._cookiefile_path = None
    dl._cookiefile_platform = None
    dl._platform_cookiefiles = {}
    dl._cookies_spec = "chrome:Default"
    dl._browser_cookie_broken = False
    dl._ydl_opt_overrides = {}

    assert dl._try_autoload_browser_cookies() is True
    assert dl._cookiefile_path == dest
    assert dl._cookies_spec is None


def test_try_autoload_browser_cookies_keeps_spec_on_failure(monkeypatch) -> None:
    def boom(spec, path):
        raise RuntimeError("locked")

    monkeypatch.setattr("downloader.dump_browser_cookies", boom)
    dl = YoutubeDownloader.__new__(YoutubeDownloader)
    dl._cookiefile_path = None
    dl._cookiefile_platform = None
    dl._platform_cookiefiles = {}
    dl._cookies_spec = "chrome:Default"
    dl._browser_cookie_broken = False
    dl._ydl_opt_overrides = {}

    assert dl._try_autoload_browser_cookies() is False
    assert dl._cookies_spec == "chrome:Default"
