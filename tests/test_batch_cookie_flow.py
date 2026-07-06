from pathlib import Path

import yt_dlp

from worker import BatchDownloadWorker


class FakeDownloader:
    def __init__(self, fail_without_cookie: bool = False, has_cookie: bool = False) -> None:
        self._cancelled = False
        self.fail_without_cookie = fail_without_cookie
        self.has_cookie = has_cookie
        self.calls: list[tuple[str, bool, dict]] = []

    def has_cookie_for_source(self, source: str) -> bool:
        return self.has_cookie

    def get_info(self, source: str, use_cookies: bool = True, **kwargs):
        self.calls.append(("get_info", use_cookies, kwargs))
        if self.fail_without_cookie and not use_cookies:
            raise yt_dlp.utils.DownloadError("login required")
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

    def download(self, video_id: str, format_id: str, output_dir: Path, use_cookies: bool = True, **kwargs):
        self.calls.append(("download", use_cookies, kwargs))
        return output_dir / f"{video_id}.mp4"


def test_batch_download_starts_without_cookie(tmp_path: Path) -> None:
    downloader = FakeDownloader()
    worker = BatchDownloadWorker(
        downloader=downloader,
        video_ids=["dQw4w9WgXcQ"],
        format_id="22",
        output_dir=tmp_path,
    )

    worker.run()

    assert any(name == "get_info" and not used for name, used, _ in downloader.calls)
    assert any(name == "download" and not used for name, used, _ in downloader.calls)
    assert not any(call[1] for call in downloader.calls)


def test_batch_retries_with_cookie_only_when_available(tmp_path: Path) -> None:
    downloader = FakeDownloader(fail_without_cookie=True, has_cookie=True)
    worker = BatchDownloadWorker(
        downloader=downloader,
        video_ids=["dQw4w9WgXcQ"],
        format_id="22",
        output_dir=tmp_path,
    )

    worker.run()

    assert any(name == "get_info" and not used for name, used, _ in downloader.calls)
    assert any(name == "get_info" and used for name, used, _ in downloader.calls)
    assert any(name == "download" and used for name, used, _ in downloader.calls)


def test_batch_passes_csv_cookie_override_on_retry(tmp_path: Path) -> None:
    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    downloader = FakeDownloader(fail_without_cookie=True)
    worker = BatchDownloadWorker(
        downloader=downloader,
        video_ids=["dQw4w9WgXcQ"],
        format_id="22",
        output_dir=tmp_path,
        cookie_overrides={"dQw4w9WgXcQ": {"cookiefile": str(cookie_path)}},
    )

    worker.run()

    assert any(
        name == "get_info"
        and used
        and kwargs.get("cookiefile_override") == str(cookie_path)
        for name, used, kwargs in downloader.calls
    )
