from pathlib import Path

import yt_dlp

from worker import BatchDownloadWorker


class FakeDownloader:
    def __init__(self, fail_without_cookie: bool = False, has_cookie: bool = False) -> None:
        self._cancelled = False
        self.fail_without_cookie = fail_without_cookie
        self.has_cookie = has_cookie
        self.calls: list[tuple[str, bool, dict]] = []

    def clear_cancel(self) -> None:
        self._cancelled = False

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


def test_batches_share_single_overview(tmp_path: Path) -> None:
    """Sequential batches sharing an output root write one overview, no per-batch CSVs."""
    from batch_io import OVERVIEW_NAME, OverviewStore

    paths: set[str] = set()
    for i in range(3):
        out = tmp_path / f"out{i}"
        out.mkdir()
        downloader = FakeDownloader()
        worker = BatchDownloadWorker(
            downloader=downloader,
            video_ids=[f"dQw4w9WgXc{i}"],
            format_id="22",
            output_dir=out,
            results_dir=tmp_path,
            row_numbers={f"dQw4w9WgXc{i}": 7},
        )
        worker.run()
        paths.add(str(worker.overview_path))
        stems = [kw.get("filename_stem") for name, _, kw in downloader.calls if name == "download"]
        assert stems == [f"0007_dQw4w9WgXc{i}"]

    assert paths == {str(tmp_path / OVERVIEW_NAME)}
    rows = OverviewStore(tmp_path).rows()
    assert sorted(r["来源文件"] for r in rows) == ["out0", "out1", "out2"]
    assert all(r["状态"] == "成功" and r["行号"] == "7" for r in rows)
    assert not list(tmp_path.rglob("batch_results_*.csv"))
    assert not list(tmp_path.rglob("failed_videos_*.csv"))


def test_legacy_batch_results_still_resume(tmp_path: Path, monkeypatch) -> None:
    from batch_io import OverviewStore

    (tmp_path / "batch_results_20250101.csv").write_text(
        "video_id,status\ndQw4w9WgXcQ,success\n", encoding="utf-8",
    )
    out = tmp_path / "list"
    out.mkdir()
    existing = out / "dQw4w9WgXcQ.mp4"
    existing.write_bytes(b"x" * 2048)
    monkeypatch.setattr(
        "worker._check_existing",
        lambda video_id, output_dir, min_height=0: existing,
    )
    downloader = FakeDownloader()
    worker = BatchDownloadWorker(
        downloader=downloader,
        video_ids=["dQw4w9WgXcQ"],
        format_id="22",
        output_dir=out,
        results_dir=tmp_path,
    )

    worker.run()

    assert downloader.calls == []
    rows = OverviewStore(tmp_path).rows()
    assert [(r["来源文件"], r["状态"]) for r in rows] == [("list", "成功")]
    assert rows[0]["输出文件"]


def test_legacy_batch_results_without_file_redownloads(tmp_path: Path) -> None:
    """旧 CSV 标成功但磁盘无文件时必须重下，不能空路径写成功。"""
    (tmp_path / "batch_results_20250101.csv").write_text(
        "video_id,status\ndQw4w9WgXcQ,success\n", encoding="utf-8",
    )
    downloader = FakeDownloader()
    worker = BatchDownloadWorker(
        downloader=downloader,
        video_ids=["dQw4w9WgXcQ"],
        format_id="22",
        output_dir=tmp_path / "list",
        results_dir=tmp_path,
    )

    worker.run()

    assert any(c[0] == "download" for c in downloader.calls)


def test_existing_candidates_match_old_and_row_prefixed_names(tmp_path: Path) -> None:
    from worker import _existing_candidates

    (tmp_path / "dQw4w9WgXcQ.mp4").write_bytes(b"x")
    (tmp_path / "0004_dQw4w9WgXcQ.mp4").write_bytes(b"x")
    (tmp_path / "0004_other000000.mp4").write_bytes(b"x")

    names = [p.name for p in _existing_candidates("dQw4w9WgXcQ", tmp_path)]

    assert names == ["0004_dQw4w9WgXcQ.mp4", "dQw4w9WgXcQ.mp4"]
