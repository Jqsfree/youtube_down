from pathlib import Path

from downloader import YoutubeDownloader


def test_parse_youtube_url_normalizes_to_watch_url():
    media = YoutubeDownloader.parse_input("https://youtu.be/dQw4w9WgXcQ")

    assert media.platform == "youtube"
    assert media.media_id == "dQw4w9WgXcQ"
    assert media.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_parse_bilibili_bv_builds_video_url():
    media = YoutubeDownloader.parse_input("BV1GJ411x7h7")

    assert media.platform == "bilibili"
    assert media.media_id == "BV1GJ411x7h7"
    assert media.url == "https://www.bilibili.com/video/BV1GJ411x7h7"


def test_parse_bilibili_av_builds_video_url():
    media = YoutubeDownloader.parse_input("av170001")

    assert media.platform == "bilibili"
    assert media.media_id == "av170001"
    assert media.url == "https://www.bilibili.com/video/av170001"


def test_parse_b23_short_link_keeps_url_for_yt_dlp():
    media = YoutubeDownloader.parse_input("https://b23.tv/abc123")

    assert media.platform == "bilibili"
    assert media.media_id == "https://b23.tv/abc123"
    assert media.url == "https://b23.tv/abc123"


def test_make_opts_uses_matching_platform_cookiefile(tmp_path: Path):
    youtube_cookie = tmp_path / "youtube.txt"
    youtube_cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tVISITOR_INFO1_LIVE\ttest\n",
        encoding="utf-8",
    )
    bilibili_cookie = tmp_path / "bilibili.txt"
    bilibili_cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t1893456000\tSESSDATA\ttest\n",
        encoding="utf-8",
    )

    dl = YoutubeDownloader(cookies_from_browser="")
    dl.set_cookiefile(youtube_cookie, persist=False)
    dl.set_cookiefile(bilibili_cookie, persist=False)

    media = YoutubeDownloader.parse_input("BV1GJ411x7h7")
    opts = dl._make_opts(use_cookies=True, media=media)

    assert opts["cookiefile"] == str(bilibili_cookie.resolve())
