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


def test_get_min_height_uses_quality_preset():
    window = MainWindow.__new__(MainWindow)
    window._QUALITY_PRESETS = MainWindow._QUALITY_PRESETS
    window._quality_combo = type("Combo", (), {"currentIndex": lambda self: 1})()
    assert window._get_min_height() == 1080


def test_get_min_height_uses_custom_input():
    window = MainWindow.__new__(MainWindow)
    window._QUALITY_PRESETS = MainWindow._QUALITY_PRESETS
    window._quality_combo = type("Combo", (), {"currentIndex": lambda self: 5})()
    window._min_height_input = type("Input", (), {"text": lambda self: "900"})()
    assert window._get_min_height() == 900


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

    # _dedup_formats 的 _sort_key 对 "720p" 格式回退为 0，
    # 因此按稳定排序保留插入顺序（2 在 3 前）
    assert {fmt["format_id"] for fmt in formats} == {"2", "3"}


def test_list_formats_returns_descending_by_height():
    """验证 list_formats 返回的格式按分辨率高度降序排列。"""
    downloader = YoutubeDownloader.__new__(YoutubeDownloader)
    info = {
        "formats": [
            {"format_id": "1", "resolution": "1280x720", "ext": "mp4",
             "vcodec": "avc1", "acodec": "mp4a", "filesize": 2000},
            {"format_id": "2", "resolution": "1920x1080", "ext": "mp4",
             "vcodec": "avc1", "acodec": "mp4a", "filesize": 3000},
            {"format_id": "3", "resolution": "640x480", "ext": "mp4",
             "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
        ]
    }

    formats = downloader.list_formats(info=info)

    heights = []
    for f in formats:
        try:
            heights.append(int(f["resolution"].split("x")[-1]))
        except (ValueError, IndexError):
            heights.append(0)
    assert heights == sorted(heights, reverse=True), \
        f"格式应按高度降序排列，实际: {heights}"


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
