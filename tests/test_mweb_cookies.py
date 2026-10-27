"""Cookie 路径不默认强制 mweb（避免格式过少）。"""

from pathlib import Path

from downloader import YoutubeDownloader


def test_make_opts_cookiefile_does_not_force_mweb(tmp_path: Path) -> None:
    cookie = tmp_path / "c.txt"
    cookie.write_text("# Netscape\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tx\n")
    dl = YoutubeDownloader.__new__(YoutubeDownloader)
    dl._cookiefile_path = cookie
    dl._cookiefile_platform = "youtube"
    dl._platform_cookiefiles = {}
    dl._cookies_spec = None
    dl._browser_cookie_broken = False
    dl._ydl_opt_overrides = {}

    opts = dl._make_opts(use_cookies=True)
    assert opts.get("cookiefile") == str(cookie)
    yt = (opts.get("extractor_args") or {}).get("youtube") or {}
    assert "player_client" not in yt


def test_make_opts_pot_override_sets_mweb(tmp_path: Path) -> None:
    cookie = tmp_path / "c.txt"
    cookie.write_text("# Netscape\n.youtube.com\tTRUE\t/\tFALSE\t0\tSID\tx\n")
    dl = YoutubeDownloader.__new__(YoutubeDownloader)
    dl._cookiefile_path = cookie
    dl._cookiefile_platform = "youtube"
    dl._platform_cookiefiles = {}
    dl._cookies_spec = None
    dl._browser_cookie_broken = False
    dl._ydl_opt_overrides = {
        "extractor_args": {"youtube": {"player_client": ["mweb"]}},
    }

    opts = dl._make_opts(use_cookies=True)
    assert opts["extractor_args"]["youtube"]["player_client"] == ["mweb"]
