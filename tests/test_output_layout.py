from pathlib import Path

from gui import MainWindow


def test_queue_output_dir_uses_source_name(monkeypatch, tmp_path):
    window = MainWindow.__new__(MainWindow)
    window._output_dir = tmp_path
    window._csv_queue = [("first", ["a", "b"]), ("second", ["c"])]

    output_dir = window._build_queue_output_dir(Path(tmp_path), "first")

    assert output_dir == tmp_path / "first"
