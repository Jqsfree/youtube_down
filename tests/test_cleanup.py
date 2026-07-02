from pathlib import Path

from downloader import YoutubeDownloader


def test_cleanup_partial_files_removes_temp_artifacts(tmp_path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    (output_dir / "video.mp4.part").write_text("partial", encoding="utf-8")
    (output_dir / "video.f248.webm").write_text("partial", encoding="utf-8")
    (output_dir / "final.mp4").write_text("done", encoding="utf-8")

    YoutubeDownloader.cleanup_partial_files(output_dir)

    assert not (output_dir / "video.mp4.part").exists()
    assert not (output_dir / "video.f248.webm").exists()
    assert (output_dir / "final.mp4").exists()
