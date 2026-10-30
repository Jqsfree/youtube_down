
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


def test_stash_treats_stem_as_literal(tmp_path):
    from downloader import _stash_existing_files, _finish_stashed_files
    target = tmp_path / "clip[1].mp4"
    neighbor = tmp_path / "clip1.mp4"
    target.write_bytes(b"original")
    neighbor.write_bytes(b"neighbor")
    stashed = _stash_existing_files(tmp_path, "clip[1]", (".mp4",))
    assert [original for original, _ in stashed] == [target]
    assert neighbor.read_bytes() == b"neighbor"
    _finish_stashed_files(stashed, False)
    assert target.read_bytes() == b"original"
