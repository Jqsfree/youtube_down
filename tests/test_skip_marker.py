from downloader import YoutubeDownloader


def test_resolve_format_id_falls_back_when_exact_height_missing():
    formats = [
        {"format_id": "1", "resolution": "480p", "container": "mp4", "type": "Video+Audio"},
    ]

    # 选择 720 时：至少 720，且最多向上兼容一档 -> 不应从 480p 直接下载
    assert YoutubeDownloader.resolve_format_id(formats, target_height=720, strict=True) is None


def test_resolve_format_id_returns_none_when_no_video_formats():
    formats = [
        {"format_id": "1", "resolution": "audio only", "container": "mp4", "type": "Audio"},
    ]

    assert YoutubeDownloader.resolve_format_id(formats, target_height=720, strict=True) is None
