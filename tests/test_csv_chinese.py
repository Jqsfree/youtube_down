from pathlib import Path

from downloader import YoutubeDownloader


def test_load_csv_chinese_link_column_gbk_semicolon(tmp_path: Path) -> None:
    csv_path = tmp_path / "法庭实录.csv"
    csv_path.write_text(
        "标题;链接\n"
        "案件1;https://www.bilibili.com/video/BV1GJ411x7h7\n"
        "案件2;https://www.youtube.com/watch?v=jNQXAC9IVRw\n",
        encoding="gbk",
    )

    rows = YoutubeDownloader.load_csv_rows(csv_path)

    assert len(rows) == 2
    assert rows[0]["video_id"] == "BV1GJ411x7h7"
    assert rows[0]["_import_column"] == "链接"


def test_load_csv_scans_non_alias_column_with_urls(tmp_path: Path) -> None:
    csv_path = tmp_path / "custom.csv"
    csv_path.write_text(
        "name,watch_url\n"
        "demo,https://www.youtube.com/watch?v=dQw4w9WgXcQ\n",
        encoding="utf-8",
    )

    rows = YoutubeDownloader.load_csv_rows(csv_path)

    assert rows[0]["video_id"] == "dQw4w9WgXcQ"
    assert rows[0]["_import_column"] == "watch_url"


def test_load_csv_videoid_column_without_manual_field(tmp_path: Path) -> None:
    csv_path = tmp_path / "ids.csv"
    csv_path.write_text("videoid\ndQw4w9WgXcQ\n", encoding="utf-8")

    rows = YoutubeDownloader.load_csv_rows(csv_path)

    assert rows[0]["video_id"] == "dQw4w9WgXcQ"
    assert rows[0]["_import_column"] == "videoid"
