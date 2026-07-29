#!/usr/bin/env python3
"""Multi-aspect cookie import harness (Linux host, Windows encodings simulated).

Run::

    python scripts/cookie_multi_aspect.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from downloader import YoutubeDownloader  # noqa: E402
from platforms.base import Platform  # noqa: E402


def _yt(crlf: bool = True) -> str:
    nl = "\r\n" if crlf else "\n"
    return (
        f"# Netscape HTTP Cookie File{nl}"
        f".youtube.com\tTRUE\t/\tTRUE\t1893456000\tVISITOR_INFO1_LIVE\ttest{nl}"
    )


def _bili() -> str:
    return (
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t1893456000\tSESSDATA\ttest\n"
    )


def _tt() -> str:
    return (
        "# Netscape HTTP Cookie File\n"
        ".tiktok.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\ttest\n"
    )


def _write(path: Path, text: str, encoding: str) -> None:
    if encoding.startswith("utf-16"):
        path.write_bytes(text.encode(encoding))
    else:
        path.write_text(text, encoding=encoding)


def main() -> int:
    results: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="cookie_multi_") as td:
        root = Path(td)

        for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "gbk", "cp936"):
            p = root / f"yt_{enc}.txt"
            _write(p, _yt(True), enc)
            try:
                dl = YoutubeDownloader(cookies_from_browser="")
                dl.set_cookiefile(p, persist=False, preferred_platform="youtube")
                results.append({"case": f"enc:{enc}", "ok": True, "plat": dl._cookiefile_platform})
            except Exception as exc:
                results.append({"case": f"enc:{enc}", "ok": False, "err": str(exc)[:120]})

        httponly = root / "httponly.txt"
        httponly.write_text(
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tx\n",
            encoding="utf-8",
        )
        try:
            YoutubeDownloader(cookies_from_browser="").set_cookiefile(
                httponly, persist=False, preferred_platform="youtube"
            )
            results.append({"case": "httponly", "ok": True})
        except Exception as exc:
            results.append({"case": "httponly", "ok": False, "err": str(exc)[:120]})

        bad_json = root / "bad.json.txt"
        bad_json.write_text('{"cookies":[]}', encoding="utf-8")
        ok_j, msg_j = YoutubeDownloader.validate_cookie_file(bad_json)
        results.append({"case": "reject_json", "ok": (not ok_j), "msg": msg_j[:80]})

        wrong = root / "wrong.txt"
        _write(wrong, _yt(False), "utf-8")
        try:
            YoutubeDownloader(cookies_from_browser="").set_cookiefile(
                wrong, persist=False, preferred_platform="tiktok"
            )
            results.append({"case": "mismatch_should_fail", "ok": False, "err": "did_not_raise"})
        except ValueError as exc:
            results.append({"case": "mismatch_should_fail", "ok": True, "err": str(exc)[:80]})

        cfg = root / "cfg"
        cfg.mkdir()
        import downloader as dlmod

        old_configs = dict(dlmod._PLATFORM_COOKIE_CONFIGS)
        old_legacy = dlmod._COOKIE_CONFIG
        try:
            dlmod._PLATFORM_COOKIE_CONFIGS = {
                "youtube": cfg / "youtube_cookiefile.txt",
                "bilibili": cfg / "bilibili_cookiefile.txt",
                "tiktok": cfg / "tiktok_cookiefile.txt",
            }
            dlmod._COOKIE_CONFIG = cfg / "cookiefile.txt"
            yt_p = root / "slot_yt.txt"
            bi_p = root / "slot_bi.txt"
            _write(yt_p, _yt(False), "utf-8")
            _write(bi_p, _bili(), "utf-8")
            dl = YoutubeDownloader(cookies_from_browser="")
            dl.set_cookiefile(yt_p, persist=True, preferred_platform="youtube")
            dl.set_cookiefile(bi_p, persist=True, preferred_platform="bilibili")
            slots = sorted(dl._platform_cookiefiles.keys())
            results.append({
                "case": "multi_slot",
                "ok": slots == ["bilibili", "youtube"],
                "slots": slots,
                "cfg_yt": (cfg / "youtube_cookiefile.txt").is_file(),
                "cfg_bi": (cfg / "bilibili_cookiefile.txt").is_file(),
            })
        finally:
            dlmod._PLATFORM_COOKIE_CONFIGS = old_configs
            dlmod._COOKIE_CONFIG = old_legacy

        from gui import MainWindow

        gui_path = root / "gui_utf16.txt"
        _write(gui_path, _yt(True), "utf-16")
        window = MainWindow.__new__(MainWindow)
        window._downloader = YoutubeDownloader(cookies_from_browser="")
        window._selected_platform = Platform.YOUTUBE
        window._remember_cookie_checkbox = type("C", (), {"isChecked": lambda self: False})()
        window._cookie_persisted = False
        window._validate_cookie_worker = None
        window._worker = None
        window._import_cookie_btn = type("B", (), {"setEnabled": lambda *a, **k: None})()
        window._clear_cookie_btn = type("B", (), {"setEnabled": lambda *a, **k: None})()
        window._status_label = type("L", (), {"setText": lambda *a, **k: None})()
        window._cookie_labels = {}
        window._base_title = "t"
        window.setWindowTitle = lambda *a, **k: None
        window._log = lambda *a, **k: None
        window._refresh_cookie_ui = lambda: None
        started: list[bool] = []
        window._start_cookie_validation = lambda: started.append(True)
        window._apply_cookie_file(str(gui_path))
        results.append({
            "case": "gui_apply",
            "ok": window._downloader._cookiefile_platform == "youtube" and started == [True],
            "stored": window._downloader._cookiefile_platform,
            "started": started,
        })

        tt_p = root / "tt_utf16.txt"
        _write(tt_p, _tt(), "utf-16")
        try:
            dl = YoutubeDownloader(cookies_from_browser="")
            dl.set_cookiefile(tt_p, persist=False, preferred_platform="tiktok")
            results.append({"case": "tiktok_utf16", "ok": dl._cookiefile_platform == "tiktok"})
        except Exception as exc:
            results.append({"case": "tiktok_utf16", "ok": False, "err": str(exc)[:120]})

    failed = [r for r in results if not r.get("ok")]
    print(json.dumps({"total": len(results), "failed": len(failed), "results": results}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
