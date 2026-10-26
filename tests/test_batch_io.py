import csv
import threading
from pathlib import Path

from batch_io import (
    OVERVIEW_NAME,
    OverviewStore,
    discover_inputs,
    filename_stem,
    media_id_from_stem,
    output_dir_for,
    parse_pasted_text,
)
from downloader import YoutubeDownloader


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discover_inputs_recurses_and_skips_result_files(tmp_path: Path) -> None:
    _write(tmp_path / "a" / "b" / "list.csv", "url\nhttps://youtu.be/dQw4w9WgXcQ\n")
    _write(tmp_path / "top.txt", "RajrwxvF4Yw\n")
    _write(tmp_path / ".hidden" / "x.csv", "url\nhttps://youtu.be/9xK8sxOIpOQ\n")
    _write(tmp_path / OVERVIEW_NAME, "来源文件,video_id\nx,dQw4w9WgXcQ\n")
    _write(tmp_path / "batch_results_20260101.csv", "video_id\ndQw4w9WgXcQ\n")
    _write(tmp_path / "cookies.txt", "# Netscape HTTP Cookie File\n")
    _write(tmp_path / "notes.md", "https://youtu.be/dQw4w9WgXcQ\n")

    inputs, errors = discover_inputs(tmp_path)

    assert [inp.rel for inp in inputs] == ["a/b/list", "top"]
    assert errors == []


def test_discover_inputs_single_file_uses_stem(tmp_path: Path) -> None:
    path = _write(tmp_path / "x.csv", "url\nhttps://youtu.be/dQw4w9WgXcQ\n")

    inputs, _ = discover_inputs(path)

    assert [inp.rel for inp in inputs] == ["x"]


def test_discover_inputs_reports_missing_and_empty(tmp_path: Path) -> None:
    _, errors = discover_inputs(tmp_path / "nope")
    assert errors and "不存在" in errors[0]

    empty = tmp_path / "empty"
    empty.mkdir()
    _, errors = discover_inputs(empty)
    assert errors


def test_load_csv_rows_keeps_original_row_numbers(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "x.csv",
        "url\nhttps://youtu.be/dQw4w9WgXcQ\nnot a link\nhttps://youtu.be/RajrwxvF4Yw\n"
        "https://youtu.be/dQw4w9WgXcQ\n",
    )

    rows = YoutubeDownloader.load_csv_rows(path)

    assert [(r["media_id"], r["_row"]) for r in rows] == [
        ("dQw4w9WgXcQ", "1"), ("RajrwxvF4Yw", "3"),
    ]


def test_headerless_rows_keep_line_numbers(tmp_path: Path) -> None:
    path = _write(tmp_path / "ids.txt", "dQw4w9WgXcQ\n\nRajrwxvF4Yw\n")

    rows = YoutubeDownloader.load_csv_rows(path)

    assert [r["_row"] for r in rows][-1] in {"2", "3"}
    assert len(rows) == 2


def test_parse_pasted_text_mixed_separators() -> None:
    text = (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ, https://youtu.be/RajrwxvF4Yw\n"
        "9xK8sxOIpOQ；hello  https://youtu.be/dQw4w9WgXcQ、BV1xx411c7mD"
    )

    valid, invalid = parse_pasted_text(text)

    assert len(valid) == 4
    assert "hello" in invalid


def test_output_dir_and_stem_helpers(tmp_path: Path) -> None:
    assert output_dir_for(tmp_path, "舞蹈/擦边") == tmp_path / "舞蹈" / "擦边"
    assert output_dir_for(tmp_path, "a:b/c?d") == tmp_path / "a_b" / "c_d"
    assert filename_stem(3, "AbZ9o3oWYjo") == "0003_AbZ9o3oWYjo"
    assert media_id_from_stem("0003_AbZ9o3oWYjo") == "AbZ9o3oWYjo"
    assert media_id_from_stem("AbZ9o3oWYjo") == "AbZ9o3oWYjo"


def test_overview_upsert_dedups_and_sorts(tmp_path: Path) -> None:
    store = OverviewStore(tmp_path)
    video = tmp_path / "x" / "0002_bbb.mp4"
    video.parent.mkdir()
    video.write_bytes(b"x")
    store.upsert(source="x", row_no=2, media_id="bbb", status="failed", category="NETWORK", message="boom")
    store.upsert(source="x", row_no=1, media_id="aaa", status="skipped", message="低于 720p")
    store.upsert(source="x", row_no=2, media_id="bbb", status="success", output_file=video)

    with open(tmp_path / OVERVIEW_NAME, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    assert [r["video_id"] for r in rows] == ["aaa", "bbb"]
    assert rows[1]["状态"] == "成功"
    assert rows[1]["错误信息"] == ""
    assert rows[1]["输出文件"] == "x/0002_bbb.mp4"
    assert OverviewStore(tmp_path).completed_ids("x") == {"bbb"}

    video.unlink()
    assert OverviewStore(tmp_path).completed_ids("x") == set()


def test_overview_concurrent_upserts(tmp_path: Path) -> None:
    store = OverviewStore(tmp_path)

    def _job(n: int) -> None:
        store.upsert(source="s", row_no=n, media_id=f"id{n:03d}", status="failed", message="x")

    threads = [threading.Thread(target=_job, args=(n,)) for n in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(OverviewStore(tmp_path).rows()) == 40
    assert not list(tmp_path.glob(".*.tmp"))
