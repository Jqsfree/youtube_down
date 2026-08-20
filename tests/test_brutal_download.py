"""Tests for brutal download mode."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import downloader
from downloader import (
    BRUTAL_MAX_WORKERS,
    BRUTAL_YDL_OPTS,
    ErrorCategory,
    YoutubeDownloader,
    is_brutal_retryable,
)
from worker import BatchDownloadWorker


@pytest.fixture
def downloader_no_init() -> YoutubeDownloader:
    import threading

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


def test_brutal_opts_constants() -> None:
    assert BRUTAL_YDL_OPTS["concurrent_fragment_downloads"] == 8
    assert BRUTAL_YDL_OPTS["retries"] == 20
    assert BRUTAL_YDL_OPTS["sleep_interval"] == 1


def test_is_brutal_retryable_categories() -> None:
    assert is_brutal_retryable(ErrorCategory("NETWORK_ERROR", False, ""))
    assert is_brutal_retryable(ErrorCategory("RATE_LIMIT", False, ""))
    assert is_brutal_retryable(ErrorCategory("UNKNOWN", False, ""))
    assert not is_brutal_retryable(ErrorCategory("VIDEO_UNAVAILABLE", False, ""))
    assert not is_brutal_retryable(ErrorCategory("CANCELLED", False, ""))


def test_make_opts_merges_brutal_overrides(downloader_no_init: YoutubeDownloader) -> None:
    downloader_no_init._ydl_opt_overrides = dict(BRUTAL_YDL_OPTS)
    downloader_no_init._cookies_spec = None
    downloader_no_init._cookiefile_path = None
    downloader_no_init._platform_cookiefiles = {}
    downloader_no_init._browser_cookie_broken = False
    opts = downloader_no_init._make_opts(use_cookies=False)
    assert opts["retries"] == 20
    assert opts["sleep_interval"] == 1


def test_clone_for_worker_copies_cookies_and_independent_lock(
    tmp_path: Path, downloader_no_init: YoutubeDownloader,
) -> None:
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# netscape\n", encoding="utf-8")
    downloader_no_init._cookiefile_path = cookie
    downloader_no_init._cookiefile_platform = "youtube"
    downloader_no_init._platform_cookiefiles = {"youtube": cookie}
    downloader_no_init._cookies_spec = "chrome:Default"

    clone = downloader_no_init.clone_for_worker(brutal=True)
    assert clone._cookiefile_path == cookie
    assert clone._platform_cookiefiles["youtube"] == cookie
    assert clone._cookies_spec == "chrome:Default"
    assert clone._cancel_coordinator is downloader_no_init
    assert clone._yt_dlp_lock is not downloader_no_init._yt_dlp_lock
    assert clone._ydl_opt_overrides["concurrent_fragment_downloads"] == 8


def test_uses_browser_cookies_only(downloader_no_init: YoutubeDownloader) -> None:
    downloader_no_init._cookiefile_path = None
    downloader_no_init._platform_cookiefiles = {}
    downloader_no_init._cookies_spec = "chrome:Default"
    downloader_no_init._browser_cookie_broken = False
    assert downloader_no_init.uses_browser_cookies_only() is True

    downloader_no_init._platform_cookiefiles = {"youtube": Path("/tmp/c.txt")}
    assert downloader_no_init.uses_browser_cookies_only() is False


def test_cancel_coordinator_propagates(downloader_no_init: YoutubeDownloader) -> None:
    clone = downloader_no_init.clone_for_worker()
    clone.cancel()
    assert downloader_no_init._is_cancelled() is True


def test_brutal_max_workers_downgrades_for_browser_cookies(
    tmp_path: Path, downloader_no_init: YoutubeDownloader,
) -> None:
    downloader_no_init._cookiefile_path = None
    downloader_no_init._platform_cookiefiles = {}
    downloader_no_init._cookies_spec = "chrome:Default"
    downloader_no_init._browser_cookie_broken = False

    worker = BatchDownloadWorker(
        downloader=downloader_no_init,
        video_ids=["abc"],
        format_id="auto",
        output_dir=tmp_path,
        brutal_mode=True,
    )
    assert worker._brutal_max_workers() == 1

    cookie = tmp_path / "c.txt"
    cookie.write_text("x", encoding="utf-8")
    downloader_no_init._platform_cookiefiles = {"youtube": cookie}
    downloader_no_init._cookies_spec = None
    assert worker._brutal_max_workers() == BRUTAL_MAX_WORKERS


def test_brutal_parallel_uses_thread_pool(tmp_path: Path, downloader_no_init: YoutubeDownloader) -> None:
    cookie = tmp_path / "c.txt"
    cookie.write_text("x", encoding="utf-8")
    downloader_no_init._cookiefile_path = cookie

    worker = BatchDownloadWorker(
        downloader=downloader_no_init,
        video_ids=["a", "b"],
        format_id="auto",
        output_dir=tmp_path,
        brutal_mode=True,
    )

    calls: list[int] = []

    def fake_brutal(index: int, vid: str, total: int) -> dict[str, str]:
        calls.append(index)
        return worker._result_row(vid, "success", "SUCCESS", "", False, "")

    worker._process_one_video_brutal = fake_brutal  # type: ignore[method-assign]

    with patch("worker.ThreadPoolExecutor") as mock_pool_cls:
        mock_pool = MagicMock()
        mock_pool_cls.return_value.__enter__.return_value = mock_pool

        def submit(fn, index, vid):
            fut = MagicMock()
            fut.result.side_effect = lambda: fn(index, vid)
            return fut

        mock_pool.submit.side_effect = submit
        with patch("worker.as_completed", side_effect=lambda futs: list(futs)):
            worker._run_brutal_parallel(
                [(0, "a"), (1, "b")],
                2,
                MagicMock(),
                MagicMock(),
                [],
            )

    mock_pool_cls.assert_called_once_with(max_workers=BRUTAL_MAX_WORKERS)
    assert sorted(calls) == [0, 1]
