from downloader import YoutubeDownloader
from gui import MainWindow


def test_format_height_uses_short_side_for_portrait():
    assert YoutubeDownloader.format_height(
        {"width": 720, "height": 1280, "resolution": "720x1280"}
    ) == 720
    assert YoutubeDownloader.format_height(
        {"resolution": "720x1280", "container": "mp4", "type": "Video Only"}
    ) == 720
    assert YoutubeDownloader.format_height(
        {"width": 1280, "height": 720, "resolution": "1280x720"}
    ) == 720


def test_format_height_uses_resolution_when_width_missing():
    # height 只有长边、width 缺失时，不能直接把 1280 当成档位
    assert YoutubeDownloader.format_height(
        {"format_id": "136", "height": 1280, "width": 0, "resolution": "720x1280"}
    ) == 720
    assert YoutubeDownloader.resolve_format_id(
        [{
            "format_id": "136",
            "height": 1280,
            "width": 0,
            "resolution": "720x1280",
            "container": "mp4",
            "type": "Video Only",
        }],
        target_height=720,
        strict=True,
    ) == "136"


def test_resolve_format_accepts_m4s_container():
    formats = [
        {
            "format_id": "dash720",
            "resolution": "1280x720",
            "width": 1280,
            "height": 720,
            "container": "m4s",
            "type": "Video Only",
            "filesize": 1000,
        },
    ]
    assert YoutubeDownloader.resolve_format_id(formats, target_height=720, strict=True) == "dash720"


def test_resolve_format_accepts_portrait_720():
    formats = [
        {
            "format_id": "v",
            "resolution": "720x1280",
            "width": 720,
            "height": 1280,
            "container": "mp4",
            "type": "Video Only",
        },
    ]
    assert YoutubeDownloader.resolve_format_id(formats, target_height=720, strict=True) == "v"


def test_worker_resolve_format_does_not_skip_portrait_720():
    from worker import AUTO_FORMAT_ID, BatchDownloadWorker

    worker = BatchDownloadWorker.__new__(BatchDownloadWorker)
    worker._format_id = AUTO_FORMAT_ID
    worker._min_height = 720
    worker._strict_quality = True
    downloader = YoutubeDownloader.__new__(YoutubeDownloader)
    info = {
        "formats": [
            {
                "format_id": "v",
                "resolution": "720x1280",
                "width": 720,
                "height": 1280,
                "ext": "mp4",
                "vcodec": "avc1",
                "acodec": "none",
                "filesize": 5000,
            },
        ]
    }
    worker._downloader = downloader
    fmt_id, _merge, expected = worker._resolve_format(info)
    assert fmt_id == "v"
    assert expected == 720


def test_resolve_format_falls_back_when_exact_height_missing():
    formats = [
        {"format_id": "1", "resolution": "480p", "container": "mp4", "type": "Video+Audio"},
        {"format_id": "2", "resolution": "720p", "container": "mp4", "type": "Video+Audio"},
    ]

    chosen = YoutubeDownloader.resolve_format_id(formats, target_height=1080, strict=True)

    assert chosen == "2"


def test_resolve_format_strict_does_not_upward_compat():
    formats = [
        {"format_id": "1", "resolution": "480p", "container": "mp4", "type": "Video+Audio"},
        {"format_id": "2", "resolution": "1080p", "container": "mp4", "type": "Video+Audio"},
        {"format_id": "3", "resolution": "1440p", "container": "mp4", "type": "Video+Audio"},
    ]

    # 选择 720p 时，不向上兼容
    chosen = YoutubeDownloader.resolve_format_id(formats, target_height=720, strict=True)
    assert chosen is None


def test_resolve_format_strict_non_1080_has_no_fallback():
    formats = [
        {"format_id": "3", "resolution": "1440p", "container": "mp4", "type": "Video+Audio"},
    ]

    chosen = YoutubeDownloader.resolve_format_id(formats, target_height=720, strict=True)
    assert chosen is None


def test_parse_min_height_uses_custom_value():
    assert MainWindow._parse_min_height("1080") == 1080
    assert MainWindow._parse_min_height("720p") == 720
    assert MainWindow._parse_min_height("invalid") == 720


def test_get_quality_settings_uses_strict_preset():
    window = MainWindow.__new__(MainWindow)
    window._QUALITY_PRESETS = MainWindow._QUALITY_PRESETS
    window._quality_combo = type("Combo", (), {"currentIndex": lambda self: 1})()
    assert window._get_quality_settings() == (1080, True)


def test_get_quality_settings_uses_4k_preset():
    window = MainWindow.__new__(MainWindow)
    window._QUALITY_PRESETS = MainWindow._QUALITY_PRESETS
    window._quality_combo = type("Combo", (), {"currentIndex": lambda self: 3})()
    assert window._get_quality_settings() == (2160, True)


def test_get_quality_settings_uses_custom_input():
    window = MainWindow.__new__(MainWindow)
    window._QUALITY_PRESETS = MainWindow._QUALITY_PRESETS
    window._quality_combo = type("Combo", (), {"currentIndex": lambda self: 4})()
    window._min_height_input = type("Input", (), {"text": lambda self: "900"})()
    assert window._get_quality_settings() == (900, True)


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


def test_worker_resolve_format_uses_strict_auto_mode():
    from worker import AUTO_FORMAT_ID, BatchDownloadWorker

    worker = BatchDownloadWorker.__new__(BatchDownloadWorker)
    worker._format_id = AUTO_FORMAT_ID
    worker._min_height = 720
    worker._strict_quality = True
    downloader = YoutubeDownloader.__new__(YoutubeDownloader)

    info = {
        "formats": [
            {"format_id": "1", "resolution": "480p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
            {"format_id": "2", "resolution": "720p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 2000},
            {"format_id": "3", "resolution": "1080p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 3000},
        ]
    }

    worker._downloader = downloader
    assert worker._resolve_format(info) == ("2", False, 720)


def test_worker_resolve_format_1080_falls_back_to_720():
    from worker import AUTO_FORMAT_ID, BatchDownloadWorker

    worker = BatchDownloadWorker.__new__(BatchDownloadWorker)
    worker._format_id = AUTO_FORMAT_ID
    worker._min_height = 1080
    worker._strict_quality = True
    downloader = YoutubeDownloader.__new__(YoutubeDownloader)

    info = {
        "formats": [
            {"format_id": "1", "resolution": "480p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
            {"format_id": "2", "resolution": "720p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 2000},
        ]
    }

    worker._downloader = downloader
    assert worker._resolve_format(info) == ("2", False, 720)


def test_worker_resolve_format_rejects_below_threshold():
    from worker import BatchDownloadWorker

    worker = BatchDownloadWorker.__new__(BatchDownloadWorker)
    worker._format_id = "1"
    worker._min_height = 720
    worker._strict_quality = True
    downloader = YoutubeDownloader.__new__(YoutubeDownloader)

    info = {
        "formats": [
            {"format_id": "1", "resolution": "480p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000},
            {"format_id": "2", "resolution": "720p", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "filesize": 2000},
        ]
    }

    worker._downloader = downloader
    assert worker._resolve_format(info) == ("2", False, 720)
