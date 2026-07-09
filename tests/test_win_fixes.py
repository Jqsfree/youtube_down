from pathlib import Path

import pytest

from downloader import YoutubeDownloader
from worker import BatchDownloadWorker


def test_load_csv_supports_csvid_column(tmp_path: Path) -> None:
    csv_path = tmp_path / "videos.csv"
    csv_path.write_text(
        "csvid\n"
        "dQw4w9WgXcQ\n"
        "BV1GJ411x7h7\n",
        encoding="utf-8",
    )

    rows = YoutubeDownloader.load_csv_rows(csv_path, column="csvid")

    assert [row["video_id"] for row in rows] == ["dQw4w9WgXcQ", "BV1GJ411x7h7"]


def test_load_csv_auto_detects_csvid_column(tmp_path: Path) -> None:
    csv_path = tmp_path / "videos.csv"
    csv_path.write_text("csvid\ndQw4w9WgXcQ\n", encoding="utf-8")

    rows = YoutubeDownloader.load_csv_rows(csv_path, column="video_id")

    assert rows[0]["video_id"] == "dQw4w9WgXcQ"


def test_load_csv_headerless_single_column(tmp_path: Path) -> None:
    csv_path = tmp_path / "ids.txt"
    csv_path.write_text("dQw4w9WgXcQ\nBV1GJ411x7h7\n", encoding="utf-8")

    rows = YoutubeDownloader.load_csv_rows(csv_path, column="video_id")

    assert [row["video_id"] for row in rows] == ["dQw4w9WgXcQ", "BV1GJ411x7h7"]


def test_browser_cookie_broken_does_not_block_cookiefile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tVISITOR_INFO1_LIVE\ttest\n",
        encoding="utf-8",
    )
    dl = YoutubeDownloader(cookies_from_browser="")
    dl._browser_cookie_broken = True
    dl.set_cookiefile(cookie_path, persist=False)

    assert dl.prefers_cookies_for_fetch() is True
    assert dl._has_explicit_cookie() is True


def test_build_format_selector_avoids_unbounded_best_fallback() -> None:
    selector = YoutubeDownloader.build_format_selector("22", min_height=720, needs_audio_merge=True)

    assert "22+bestaudio" in selector
    assert "height>=720" in selector
    assert selector.endswith("bestaudio") or "bestvideo[height>=720]+bestaudio" in selector


def test_batch_cookie_retry_respects_min_height(tmp_path: Path) -> None:
    class FakeDownloader:
        def __init__(self) -> None:
            self._cancelled = False
            self.calls: list[tuple[str, str, int]] = []

        def has_cookie_for_source(self, source: str) -> bool:
            return True

        def get_info(self, source: str, use_cookies: bool = True, **kwargs):
            return {
                "title": "demo",
                "formats": [
                    {
                        "format_id": "22",
                        "resolution": "720p",
                        "ext": "mp4",
                        "vcodec": "avc1",
                        "acodec": "mp4a",
                        "filesize": 1024,
                    }
                ],
            }

        def list_formats(self, info, min_height=None):
            return [
                {
                    "format_id": "22",
                    "resolution": "720p",
                    "container": "mp4",
                    "type": "Video+Audio",
                }
            ]

        def resolve_format_id(self, formats, min_height=720):
            return "22"

        def download(self, video_id: str, format_id: str, output_dir: Path, **kwargs):
            self.calls.append((video_id, format_id, kwargs.get("min_height", 0)))
            return output_dir / f"{video_id}.mp4"

    downloader = FakeDownloader()
    worker = BatchDownloadWorker(
        downloader=downloader,
        video_ids=["dQw4w9WgXcQ"],
        format_id="22",
        output_dir=tmp_path,
        min_height=720,
    )

    worker.run()

    assert downloader.calls
    assert downloader.calls[0][1] == "22"
    assert downloader.calls[0][2] == 720


def test_prefers_cookies_false_on_windows_without_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("downloader.os.name", "nt")
    monkeypatch.setattr(
        YoutubeDownloader, "detect_browser", staticmethod(lambda: ("chrome", "Default"))
    )
    dl = YoutubeDownloader(cookies_from_browser=None)
    assert dl.prefers_cookies_for_fetch() is False
