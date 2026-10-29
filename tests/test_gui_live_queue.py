"""GUI 侧活动队列与更新安装的安全边界（offscreen）。

重点覆盖两件「测试测不出来但会炸」的事：
1. 安装更新前必须先停掉后台线程（QApplication.quit 不触发 closeEvent）。
2. 运行中追加任务要走 worker.enqueue，且与 _batch_video_ids 索引保持对齐。
"""

from __future__ import annotations


def _window(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    import theme
    import gui

    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(theme, "settings", lambda: QSettings(ini, QSettings.Format.IniFormat))
    monkeypatch.setattr(gui, "settings", theme.settings)
    monkeypatch.setattr(gui.MainWindow, "_show_env_wizard", lambda self, *a, **k: None)
    return gui.MainWindow()


class _FakeWorker:
    """记录调用顺序的 worker 桩。"""

    def __init__(self, running: bool = True) -> None:
        self.running = running
        self.queued: list[str] = []
        self.events: list[str] = []
        self.closed = False

    def isRunning(self) -> bool:
        return self.running

    def enqueue(self, ids, **_kwargs) -> bool:
        self.queued.extend(ids)
        return self.running

    def close_queue(self) -> None:
        self.closed = True
        self.running = False

    def wait(self, *_args) -> bool:
        self.events.append("wait")
        self.running = False
        return True


def test_shutdown_for_update_stops_download_before_install(tmp_path, monkeypatch) -> None:
    """安装前必须 cancel + wait，否则 QThread 被析构会 abort。"""
    window = _window(tmp_path, monkeypatch)
    worker = _FakeWorker()
    window._worker = worker
    window._downloading = True
    window._downloader.cancel = lambda: worker.events.append("cancel")  # type: ignore[method-assign]

    window._shutdown_for_update()

    assert "cancel" in worker.events, "安装更新前没有取消下载"
    assert "wait" in worker.events, "安装更新前没有等待下载线程结束"
    assert worker.events.index("cancel") < worker.events.index("wait")
    assert worker.closed is True


def test_install_is_called_after_shutdown(tmp_path, monkeypatch) -> None:
    """确认 install_and_restart 在停机之后才被调用。"""
    from PySide6.QtWidgets import QMessageBox

    import app_updater

    window = _window(tmp_path, monkeypatch)
    window._batch_running = lambda: False  # type: ignore[method-assign]
    window._app_update_worker = None

    order: list[str] = []
    monkeypatch.setattr(window, "_shutdown_for_update",
                        lambda: order.append("shutdown"))
    monkeypatch.setattr(
        app_updater, "install_and_restart",
        lambda p: order.append("install") or "ok",
    )
    monkeypatch.setattr(app_updater, "is_frozen", lambda: True)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr("gui.QApplication.quit", lambda: order.append("quit"))

    window._on_app_download_done(True, str(tmp_path / "pkg.exe"))

    assert order == ["shutdown", "install", "quit"], f"顺序不对: {order}"


def test_live_enqueue_dedupes_against_batch_ids(tmp_path, monkeypatch) -> None:
    """重复 ID 不能进活动队列，否则 worker 索引与 _batch_video_ids 错位。"""
    window = _window(tmp_path, monkeypatch)
    worker = _FakeWorker()
    window._worker = worker
    window._downloading = True
    window._batch_video_ids = ["dQw4w9WgXcQ"]
    window._batch_total = 1

    window._add_sources(["dQw4w9WgXcQ", "aaaaaaaaaaa"])

    assert worker.queued == ["aaaaaaaaaaa"]
    assert window._batch_video_ids == ["dQw4w9WgXcQ", "aaaaaaaaaaa"]
    assert window._batch_total == 2
    assert window._table.item(1, 3).text() == "排队中"


def test_live_enqueue_falls_back_to_queue_when_worker_ended(tmp_path, monkeypatch) -> None:
    """worker 已收尾时返回 False，任务落到下一轮 _csv_queue。"""
    window = _window(tmp_path, monkeypatch)
    worker = _FakeWorker(running=False)
    window._worker = worker
    window._downloading = True
    window._batch_video_ids = ["dQw4w9WgXcQ"]
    window._batch_total = 1

    window._add_sources(["aaaaaaaaaaa"])

    assert worker.queued == []
    assert window._csv_queue == [
        ("paste", ["aaaaaaaaaaa"], None, {"aaaaaaaaaaa": 1})
    ]


def test_cancel_resets_pending_row_status(tmp_path, monkeypatch) -> None:
    """取消后还停在「排队中」的行要复位，否则界面永远卡住。"""
    window = _window(tmp_path, monkeypatch)
    worker = _FakeWorker()
    window._worker = worker
    window._downloading = True
    window._batch_video_ids = ["dQw4w9WgXcQ"]
    window._append_queue_row("YouTube", "dQw4w9WgXcQ", "排队中", 0)
    window._downloader.cancel = lambda: None  # type: ignore[method-assign]

    window._on_cancel()

    assert window._table.item(0, 3).text() == "已取消"


def test_close_event_stops_worker_without_quit(tmp_path, monkeypatch) -> None:
    """QThread.quit() 对无事件循环的 run() 无效，退出必须走 close_queue + cancel。"""
    window = _window(tmp_path, monkeypatch)
    worker = _FakeWorker()
    window._worker = worker
    window._downloading = True
    window._downloader.cancel = lambda: worker.events.append("cancel")  # type: ignore[method-assign]
    monkeypatch.setattr(window, "_save_settings", lambda: None)

    class _Event:
        accepted = False

        def accept(self) -> None:
            _Event.accepted = True

    window.closeEvent(_Event())

    assert worker.closed is True, "closeEvent 没有关掉活动队列"
    assert "wait" in worker.events
    assert _Event.accepted is True


def test_stop_background_threads_waits_for_expand_workers(tmp_path, monkeypatch) -> None:
    window = _window(tmp_path, monkeypatch)

    class _Expand:
        def __init__(self) -> None:
            self.waited = False

        def isRunning(self) -> bool:
            return True

        def wait(self, *_a) -> bool:
            self.waited = True
            return True

    expand = _Expand()
    window._expand_workers = [expand]
    window._stop_background_threads()

    assert expand.waited is True
    assert window._expand_workers == [expand]  # 列表本身不能被清空（会丢引用）
