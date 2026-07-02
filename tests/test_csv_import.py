from pathlib import Path

from downloader import YoutubeDownloader


def test_load_csv_supports_url_and_custom_columns(tmp_path):
    csv_path = tmp_path / "videos.csv"
    csv_path.write_text(
        "Video URL\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\n",
        encoding="utf-8",
    )

    ids = YoutubeDownloader.load_csv(csv_path, column="Video URL")

    assert ids == ["dQw4w9WgXcQ"]


def test_load_csv_rows_preserves_output_dir(tmp_path):
    csv_path = tmp_path / "videos.csv"
    csv_path.write_text(
        "video_id,output_dir\ndQw4w9WgXcQ,downloads/queue_a\n",
        encoding="utf-8",
    )

    rows = YoutubeDownloader.load_csv_rows(csv_path, column="video_id")

    assert rows[0]["video_id"] == "dQw4w9WgXcQ"
    assert rows[0]["output_dir"] == "downloads/queue_a"
