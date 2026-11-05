"""Real Qt QThread signal-path test, with a fake download engine."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

from worker import BatchDownloadWorker


class FakeEngine:
    def __init__(self) -> None:
        self._cancelled = False

    def clear_cancel(self): self._cancelled = False
    def _is_cancelled(self): return self._cancelled
    def has_cookie_for_source(self, _source): return False
    def uses_browser_cookies_only(self): return False

    def get_info(self, source, **_kwargs):
        return {"title": source, "_platform": "generic", "formats": []}

    def download(self, **kwargs):
        path = kwargs["output_dir"] / "test.mp4"
        path.write_bytes(b"valid fake placeholder")
        return path


def test_qthread_runner_emits_completion_from_real_thread(tmp_path: Path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    engine = FakeEngine()
    worker = BatchDownloadWorker(
        downloader=engine, video_ids=["https://example.com/video.mp4"],
        format_id="auto", output_dir=tmp_path, min_height=0,
    )

    class NoWait:
        def on_success(self): pass
        def on_skip(self): pass
        def on_bot_or_rate_limit(self): pass
        def wait(self): pass

    worker._make_pacer = NoWait
    spy = QSignalSpy(worker.all_finished)
    worker.start()
    try:
        assert worker.wait(5000), "background thread did not finish"
        app.processEvents()
        assert spy.count() == 1
        assert list(spy.at(0))[:3] == [1, 0, 0]
        assert (tmp_path / "下载总览.csv").exists()
    finally:
        worker.close_queue()
        worker.wait(5000)
