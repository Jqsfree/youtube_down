"""进度分母回归：有续传跳过 / 运行时追加时，整体进度必须单调且能到 100%。

旧实现用「本轮待下载条数」当分母、分子只数实跑条数，一旦有续传跳过
进度条就会卡在 N-M/N 然后突然跳到 100%。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worker import BatchDownloadWorker


class _FakeDownloader:
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

    def uses_browser_cookies_only(self) -> bool:
        return False

    def clone_for_worker(self, brutal: bool = False):  # noqa: ARG002
        return self


class _FakePacer:
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


def _make_worker(tmp_path: Path, ids: list[str], live: bool = True) -> BatchDownloadWorker:
    worker = BatchDownloadWorker(
        downloader=_FakeDownloader(),
        video_ids=list(ids),
        format_id="auto",
        output_dir=tmp_path,
        min_height=720,
    )
    if live:
        worker.enable_live_queue()
    return worker


def _ok(worker: BatchDownloadWorker, vid: str) -> dict[str, str]:
    return worker._result_row(vid, "success", "SUCCESS", "", False, "")


def test_progress_reaches_100_with_resume_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10 条里有 2 条续传跳过，进度仍要单调到 100。"""
    ids = [f"vid{i}" for i in range(10)]
    worker = _make_worker(tmp_path, ids, live=False)
    worker._process_one_video = lambda i, vid, total: _ok(worker, vid)  # type: ignore[method-assign]
    worker._overview.completed_ids = lambda src: {"vid0", "vid1"}  # type: ignore[method-assign]

    def fake_check(video_id, *args, **kwargs):
        return Path("/tmp/already.mp4") if video_id in {"vid0", "vid1"} else None

    monkeypatch.setattr("worker._check_existing", fake_check)

    progress: list[int] = []
    worker.all_progress_changed.connect(progress.append)
    worker.run()

    assert progress, "没有发出任何整体进度"
    assert progress == sorted(progress), f"进度回退: {progress}"
    assert progress[0] == 20, f"续传跳过应计入分子: {progress}"
    assert progress[-1] == 100, f"进度未到 100: {progress}"


def test_progress_counts_items_added_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行中追加任务：分母变大，但进度仍然单调不减并到 100。"""
    worker = _make_worker(tmp_path, ["a", "b"])
    worker._idle_exit_seconds = 1.5
    monkeypatch.setattr("worker._check_existing", lambda *a, **k: None)

    progress: list[int] = []
    worker.all_progress_changed.connect(progress.append)

    def fake(index: int, vid: str, total: int) -> dict[str, str]:
        if vid == "a":
            assert worker.enqueue(["c", "d"]) is True
        return _ok(worker, vid)

    worker._process_one_video = fake  # type: ignore[method-assign]
    worker.run()

    assert progress == sorted(progress), f"追加任务后进度回退: {progress}"
    assert progress[-1] == 100
    assert worker._expected == 4


def test_progress_counts_brutal_mode_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """暴力并行同样按「已完成 / 期望总数」推进。"""
    ids = [f"v{i}" for i in range(6)]
    worker = BatchDownloadWorker(
        downloader=_FakeDownloader(),
        video_ids=ids,
        format_id="auto",
        output_dir=tmp_path,
        brutal_mode=True,
    )
    worker._process_one_video_brutal = lambda i, vid, total: _ok(worker, vid)  # type: ignore[method-assign]
    worker._overview.completed_ids = lambda src: {"v0"}  # type: ignore[method-assign]
    monkeypatch.setattr(
        "worker._check_existing",
        lambda vid, *a, **k: Path("/tmp/x.mp4") if vid == "v0" else None,
    )

    progress: list[int] = []
    worker.all_progress_changed.connect(progress.append)
    worker.run()

    assert progress == sorted(progress)
    assert progress[-1] == 100


def test_progress_does_not_exceed_100(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消/重复推进也不应越过 100。"""
    worker = _make_worker(tmp_path, ["a"])
    monkeypatch.setattr("worker._check_existing", lambda *a, **k: None)
    worker._advance_progress(5)
    progress: list[int] = []
    worker.all_progress_changed.connect(progress.append)
    worker._advance_progress(5)
    assert progress[-1] == 100


def test_overall_pct_blends_current_video(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path, ["a", "b", "c", "d"])
    worker._progress_done = 1
    assert worker._overall_pct(0) == 25
    assert worker._overall_pct(50) == 37
    assert worker._overall_pct(100) == 50
