"""GUI 下载设置经 QSettings 往返（offscreen，不触碰真实配置）。"""


def _window(tmp_path, monkeypatch):
    """QSettings(org, app) 在 macOS 上始终用 NativeFormat，setDefaultFormat 无效，
    所以直接替换 theme/gui 里的 settings() 指向临时 INI。"""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    import theme
    import gui

    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(theme, "settings", lambda: QSettings(ini, QSettings.Format.IniFormat))
    monkeypatch.setattr(gui, "settings", theme.settings)
    return gui.MainWindow()


def test_download_settings_roundtrip(tmp_path, monkeypatch) -> None:
    first = _window(tmp_path, monkeypatch)
    first._quality_combo.setCurrentIndex(2)
    first._subs_lang_combo.setCurrentIndex(2)
    first._audio_format_combo.setCurrentText("opus")
    first._proxy_input.setText("http://127.0.0.1:7897")
    first._save_settings()

    second = _window(tmp_path, monkeypatch)
    assert second._quality_combo.currentIndex() == 2
    assert second._subs_lang_combo.currentIndex() == 2
    assert second._audio_format_combo.currentText() == "opus"
    assert second._proxy_input.text() == "http://127.0.0.1:7897"


def test_cancel_guard_ignores_calls_when_not_downloading(tmp_path, monkeypatch) -> None:
    window = _window(tmp_path, monkeypatch)
    calls: list[int] = []
    window._downloader.cancel = lambda: calls.append(1)  # type: ignore[method-assign]

    window._on_cancel()
    assert calls == []

    window._downloading = True
    window._on_cancel()
    assert calls == [1]


def test_paste_after_finished_batch_starts_clean(tmp_path, monkeypatch) -> None:
    window = _window(tmp_path, monkeypatch)
    window._csv_ids = ["oldid"]
    window._csv_queue = []
    window._queue_results.append((1, 0, 0, "/tmp/old.csv"))
    window._batch_errors.append((0, "oldid", "boom"))
    window._batch_done = window._batch_total = 1
    window._append_queue_row("YouTube", "oldid", "完成", 100)

    window._add_sources(["dQw4w9WgXcQ"])

    assert window._csv_ids == ["dQw4w9WgXcQ"]
    assert window._csv_queue == [
        ("paste", ["dQw4w9WgXcQ"], None, {"dQw4w9WgXcQ": 1})
    ]
    assert window._queue_results == []
    assert window._batch_errors == []
    assert (window._batch_done, window._batch_total) == (0, 0)
    assert window._table.rowCount() == 1
    assert window._table.item(0, 1).text() == "dQw4w9WgXcQ"
    assert window._table.item(0, 3).text() == "等待中"


def test_paste_keeps_pending_csv_groups(tmp_path, monkeypatch) -> None:
    window = _window(tmp_path, monkeypatch)
    window._csv_ids = ["oldid"]
    window._csv_queue = [("a.csv", ["oldid"], None, {"oldid": 3})]
    window._append_queue_row("YouTube", "oldid", "等待中", 0)

    window._add_sources(["dQw4w9WgXcQ"])

    assert window._csv_queue[0] == ("a.csv", ["oldid"], None, {"oldid": 3})
    assert window._csv_queue[1] == (
        "paste", ["dQw4w9WgXcQ"], None, {"dQw4w9WgXcQ": 1}
    )
    assert window._table.rowCount() == 2


def test_finished_batch_clears_queue_ids_but_keeps_rows(tmp_path, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    window = _window(tmp_path, monkeypatch)
    window._csv_ids = ["oldid"]
    window._csv_queue = []
    window._download_btn.setEnabled(True)
    window._append_queue_row("YouTube", "oldid", "失败", 0)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    window._on_batch_all_finished(0, 1, 0, "/tmp/下载总览.csv")

    assert window._csv_ids == []
    assert not window._download_btn.isEnabled()
    assert window._table.rowCount() == 1  # 失败行保留，可右键重试
