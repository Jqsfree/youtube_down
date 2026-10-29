"""活动队列：运行中可持续追加下载任务。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from worker import BatchDownloadWorker


class _FakePacer:
    """测试用节流器：不真的睡觉。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def on_success(self) -> None:
        pass

    def on_skip(self) -> None:
        pass

    def on_bot_or_rate_limit(self) -> None:
        pass

    def wait(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("worker.AdaptivePacer", _FakePacer)


class _FakeDownloader:
    """最小下载器桩。"""

    def __init__(self) -> None:
        self._cancelled = False

    def clear_cancel(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        return self._cancelled

    def has_cookie_for_source(self, source: str) -> bool:
        return False


def _make_worker(
    tmp_path: Path, ids: list[str], idle: float = 0.6, live: bool = True,
) -> BatchDownloadWorker:
    worker = BatchDownloadWorker(
        downloader=_FakeDownloader(),
        video_ids=list(ids),
        format_id="auto",
        output_dir=tmp_path,
        min_height=720,
    )
    worker._idle_exit_seconds = idle
    if live:
        worker.enable_live_queue()
    return worker


def _ok(worker: BatchDownloadWorker, vid: str) -> dict[str, str]:
    return worker._result_row(vid, "success", "SUCCESS", "", False, "")


def test_enqueue_while_running_is_consumed(tmp_path: Path) -> None:
    """初始任务排空后再 enqueue，worker 仍在等待并继续下载。"""
    worker = _make_worker(tmp_path, ["a"], idle=1.5)
    seen: list[str] = []
    consumed = threading.Event()

    def fake(index: int, vid: str, total: int) -> dict[str, str]:
        seen.append(vid)
        if vid == "b":
            consumed.set()
        return _ok(worker, vid)

    worker._process_one_video = fake  # type: ignore[method-assign]

    worker.start()
    deadline = time.monotonic() + 3
    while "a" not in seen and time.monotonic() < deadline:
        time.sleep(0.02)
    assert seen == ["a"]

    assert worker.enqueue(["b"]) is True
    assert consumed.wait(3), "追加的任务没有被消费"
    worker.close_queue()
    assert worker.wait(3000)
    assert seen == ["a", "b"]


def test_enqueue_rejects_after_finish(tmp_path: Path) -> None:
    """队列自然收尾后 enqueue 返回 False，交给 GUI 下一轮。"""
    worker = _make_worker(tmp_path, ["a"], idle=0.2)
    worker._process_one_video = lambda index, vid, total: _ok(worker, vid)  # type: ignore[method-assign]

    worker.start()
    assert worker.wait(5000)
    assert worker.enqueue(["b"]) is False
    assert worker._live == type(worker._live)()


def test_enqueue_before_run_is_rejected(tmp_path: Path) -> None:
    """未启动时不该接管任务（GUI 走 _csv_queue）。"""
    worker = _make_worker(tmp_path, ["a"], live=False)
    assert worker.enqueue(["b"]) is False


def test_enqueue_skips_duplicates_and_bumps_expected(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path, ["a"], idle=1.0)
    worker._process_one_video = lambda index, vid, total: _ok(worker, vid)  # type: ignore[method-assign]
    worker.start()
    assert worker.enqueue(["a"]) is True       # 重复 ID 被忽略
    assert worker.enqueue(["b", "c"]) is True
    assert worker._expected == 3
    worker.close_queue()
    assert worker.wait(3000)


def test_close_queue_stops_waiting(tmp_path: Path) -> None:
    """close_queue() 立即结束空闲等待。"""
    worker = _make_worker(tmp_path, [], idle=30.0)
    worker.start()
    time.sleep(0.1)
    assert worker.isRunning()
    worker.close_queue()
    assert worker.wait(3000)


def test_cancel_stops_consuming_live_items(tmp_path: Path) -> None:
    """取消后不再消费追加任务。"""
    dl = _FakeDownloader()
    worker = BatchDownloadWorker(
        downloader=dl,
        video_ids=["a"],
        format_id="auto",
        output_dir=tmp_path,
    )
    worker.enable_live_queue()
    worker._idle_exit_seconds = 0.2
    worker._process_one_video = lambda index, vid, total: _ok(worker, vid)  # type: ignore[method-assign]

    worker.start()
    assert worker.wait(5000)  # 空队列自然收尾
    dl.cancel()
    assert worker.enqueue(["b"]) is False


def test_iter_work_drains_pending_then_live(tmp_path: Path) -> None:
    """_iter_work 先跑初始列表，再从活动队列取。"""
    worker = _make_worker(tmp_path, ["a"], idle=1.0)
    worker._live.append((1, "b"))
    worker._next_index = 2
    worker._expected = 2
    got = [vid for _, vid in worker._iter_work([(0, "a")])]
    assert got == ["a", "b"]


def test_iter_work_without_live_mode_ignores_queue(tmp_path: Path) -> None:
    """未开启活动队列时（直接调用 _run_* 的旧用法）行为不变。"""
    worker = _make_worker(tmp_path, ["a"], live=False)
    worker._live.append((1, "b"))
    got = [vid for _, vid in worker._iter_work([(0, "a")])]
    assert got == ["a"]
