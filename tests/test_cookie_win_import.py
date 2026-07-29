"""Windows-oriented cookie import functional tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from downloader import YoutubeDownloader
from platforms.base import Platform


def _netscape_youtube(crlf: bool = False) -> str:
    nl = "\r\n" if crlf else "\n"
    return (
        f"# Netscape HTTP Cookie File{nl}"
        f".youtube.com\tTRUE\t/\tTRUE\t1893456000\tVISITOR_INFO1_LIVE\ttest{nl}"
        f".youtube.com\tTRUE\t/\tTRUE\t1893456000\tCONSENT\tYES+1{nl}"
    )


def _netscape_bilibili() -> str:
    return (
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t1893456000\tSESSDATA\ttest\n"
    )


@pytest.mark.parametrize(
    "encoding",
    ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "gbk"],
)
def test_set_cookiefile_accepts_windows_encodings(
    tmp_path: Path, encoding: str
) -> None:
    path = tmp_path / f"cookies_{encoding}.txt"
    text = _netscape_youtube(crlf=True)
    if encoding.startswith("utf-16"):
        path.write_bytes(text.encode(encoding))
    else:
        path.write_text(text, encoding=encoding)

    dl = YoutubeDownloader(cookies_from_browser="")
    dl.set_cookiefile(path, persist=False, preferred_platform="youtube")

    assert dl._cookiefile_platform == "youtube"
    assert dl._platform_cookiefiles["youtube"] == path.resolve()
    ok, msg = YoutubeDownloader.validate_cookie_file(path, platform="youtube")
    assert ok, msg


def test_utf16_cookie_previously_failed_domain_detection(tmp_path: Path) -> None:
    """Regression: UTF-16 cookies looked like Netscape but lost domain names."""
    path = tmp_path / "cookies_utf16.txt"
    path.write_bytes(_netscape_youtube(crlf=True).encode("utf-16"))

    # Direct multi-encoding read must see youtube.com
    text = YoutubeDownloader._read_cookie_text(path)
    assert "youtube.com" in text.lower()

    dl = YoutubeDownloader(cookies_from_browser="")
    dl.set_cookiefile(path, persist=False)
    assert dl._cookiefile_platform == "youtube"


def test_preferred_platform_rejects_wrong_domain_file(tmp_path: Path) -> None:
    path = tmp_path / "yt.txt"
    path.write_text(_netscape_youtube(), encoding="utf-8")
    dl = YoutubeDownloader(cookies_from_browser="")
    with pytest.raises(ValueError, match="tiktok.com|bilibili.com|Cookie"):
        dl.set_cookiefile(path, persist=False, preferred_platform="tiktok")


def test_preferred_platform_stores_under_selected_slot(tmp_path: Path) -> None:
    path = tmp_path / "bili.txt"
    path.write_text(_netscape_bilibili(), encoding="utf-8")
    dl = YoutubeDownloader(cookies_from_browser="")
    dl.set_cookiefile(path, persist=False, preferred_platform="bilibili")
    assert dl._cookiefile_platform == "bilibili"
    assert "bilibili" in dl._platform_cookiefiles


def test_gui_apply_cookie_uses_selected_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gui import MainWindow

    path = tmp_path / "cookies.txt"
    path.write_bytes(_netscape_youtube(crlf=True).encode("utf-16"))

    window = MainWindow.__new__(MainWindow)
    window._downloader = YoutubeDownloader(cookies_from_browser="")
    window._selected_platform = Platform.YOUTUBE
    window._remember_cookie_checkbox = type("C", (), {"isChecked": lambda self: False})()
    window._cookie_persisted = False
    window._validate_cookie_worker = None
    window._worker = None
    window._import_cookie_btn = type("B", (), {"setEnabled": lambda *a, **k: None})()
    window._clear_cookie_btn = type("B", (), {"setEnabled": lambda *a, **k: None})()
    window._status_label = type("L", (), {"setText": lambda *a, **k: None})()
    window._cookie_labels = {}
    window._base_title = "t"
    window.setWindowTitle = lambda *a, **k: None  # type: ignore[method-assign]
    window._log = lambda *a, **k: None  # type: ignore[method-assign]
    window._refresh_cookie_ui = lambda: None  # type: ignore[method-assign]
    started: list[bool] = []
    window._start_cookie_validation = lambda: started.append(True)  # type: ignore[method-assign]

    # Avoid QMessageBox if unexpected
    monkeypatch.setattr("gui.QMessageBox.critical", lambda *a, **k: None)
    monkeypatch.setattr("gui.QMessageBox.warning", lambda *a, **k: None)

    window._apply_cookie_file(str(path))

    assert window._downloader._cookiefile_platform == "youtube"
    assert started == [True]


def test_httponly_prefixed_netscape_lines(tmp_path: Path) -> None:
    path = tmp_path / "httponly.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n",
        encoding="utf-8",
    )
    ok, msg = YoutubeDownloader.validate_cookie_file(path)
    assert ok, msg
    dl = YoutubeDownloader(cookies_from_browser="")
    dl.set_cookiefile(path, persist=False, preferred_platform="youtube")
    assert dl._cookiefile_platform == "youtube"
