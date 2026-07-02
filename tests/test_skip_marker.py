from downloader import YoutubeDownloader


def test_resolve_format_id_returns_none_for_below_threshold():
    formats = [
        {"format_id": "1", "resolution": "480p", "container": "mp4", "type": "Video+Audio"},
    ]

    assert YoutubeDownloader.resolve_format_id(formats, min_height=720) is None
