from downloader import YoutubeDownloader
from gui import MainWindow


def test_resolve_format_prefers_minimum_height():
    formats = [
        {"format_id": "1", "resolution": "480p", "container": "mp4", "type": "Video+Audio"},
        {"format_id": "2", "resolution": "720p", "container": "mp4", "type": "Video+Audio"},
        {"format_id": "3", "resolution": "1080p", "container": "mp4", "type": "Video+Audio"},
    ]

    chosen = YoutubeDownloader.resolve_format_id(formats, min_height=720)

    assert chosen == "3"


def test_resolve_format_falls_back_to_720_for_1080p_goal():
    formats = [
        {"format_id": "1", "resolution": "480p", "container": "mp4", "type": "Video+Audio"},
        {"format_id": "2", "resolution": "720p", "container": "mp4", "type": "Video+Audio"},
    ]

    chosen = YoutubeDownloader.resolve_format_id(formats, min_height=1080)

    assert chosen == "2"


def test_parse_min_height_uses_custom_value():
    assert MainWindow._parse_min_height("1080") == 1080
    assert MainWindow._parse_min_height("720p") == 720
    assert MainWindow._parse_min_height("invalid") == 720


def test_list_formats_filters_below_min_height():
    downloader = YoutubeDownloader.__new__(YoutubeDownloader)
    info = {
        "formats": [
            {"format_id": "1", "resolution": "480p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
            {"format_id": "2", "resolution": "720p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 2000},
            {"format_id": "3", "resolution": "1080p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 3000},
        ]
    }

    formats = downloader.list_formats(info=info, min_height=720)

    assert [fmt["format_id"] for fmt in formats] == ["2", "3"]


def test_worker_resolve_format_uses_auto_mode():
    from worker import AUTO_FORMAT_ID, BatchDownloadWorker

    worker = BatchDownloadWorker.__new__(BatchDownloadWorker)
    worker._format_id = AUTO_FORMAT_ID
    worker._min_height = 720
    downloader = YoutubeDownloader.__new__(YoutubeDownloader)

    info = {
        "formats": [
            {"format_id": "1", "resolution": "480p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
            {"format_id": "2", "resolution": "720p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 2000},
        ]
    }

    worker._downloader = downloader
    assert worker._resolve_format(info) == ("best", False)


def test_worker_resolve_format_rejects_below_threshold():
    from worker import BatchDownloadWorker

    worker = BatchDownloadWorker.__new__(BatchDownloadWorker)
    worker._format_id = "1"
    worker._min_height = 720
    downloader = YoutubeDownloader.__new__(YoutubeDownloader)

    info = {
        "formats": [
            {"format_id": "1", "resolution": "480p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
            {"format_id": "2", "resolution": "720p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 2000},
        ]
    }

    worker._downloader = downloader
    assert worker._resolve_format(info) == ("2", False)

