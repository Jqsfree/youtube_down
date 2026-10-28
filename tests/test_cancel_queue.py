"""取消后 BatchDownloadWorker 不再下载、不发 100% 完成进度。"""

from pathlib import Path

from worker import BatchDownloadWorker


class CancelledDownloader:
    def __init__(self) -> None:
        self._cancelled = True
        self.calls: list[str] = []

    def clear_cancel(self) -> None:
        pass  # 保留取消状态，模拟用户在 run 过程中取消

    def _is_cancelled(self) -> bool:
        return True

    def get_info(self, source: str, **kwargs):  # pragma: no cover - 不应被调用
        self.calls.append(source)
        raise AssertionError("取消后不应取视频信息")


def test_cancelled_batch_skips_download_and_complete_progress(tmp_path: Path) -> None:
    downloader = CancelledDownloader()
    worker = BatchDownloadWorker(
        downloader=downloader,
        video_ids=["dQw4w9WgXcQ"],
        format_id="22",
        output_dir=tmp_path,
    )
    progress: list[int] = []
    finished: list[tuple[int, int, int, str]] = []
    status: list[str] = []
    worker.all_progress_changed.connect(progress.append)
    worker.all_finished.connect(lambda *args: finished.append(args))
    worker.status_changed.connect(status.append)

    worker.run()

    assert downloader.calls == []
    assert 100 not in progress
    assert finished == [(0, 0, 0, str(worker.overview_path))]
    assert status == ["已取消"]


def test_pause_blocks_between_videos_until_resume() -> None:
    import threading
    import time

    worker = BatchDownloadWorker.__new__(BatchDownloadWorker)
    worker._pause_event = threading.Event()
    worker._pause_event.set()
    worker._is_cancelled = lambda: False  # type: ignore[method-assign]

    worker.pause()
    waiter = threading.Thread(target=worker._wait_if_paused)
    waiter.start()
    time.sleep(0.1)
    assert waiter.is_alive()

    worker.resume()
    waiter.join(1)
    assert not waiter.is_alive()
