#!/usr/bin/env python3
"""Functional check for Windows-style cookie import (no GUI required).

Run on Windows or Linux::

    python scripts/test_cookie_win_import.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from downloader import YoutubeDownloader  # noqa: E402


def _write(path: Path, text: str, encoding: str) -> None:
    if encoding.startswith("utf-16"):
        path.write_bytes(text.encode(encoding))
    else:
        path.write_text(text, encoding=encoding)


def main() -> int:
    sample = (
        "# Netscape HTTP Cookie File\r\n"
        ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tVISITOR_INFO1_LIVE\ttest\r\n"
    )
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cookie_win_") as td:
        root = Path(td)
        cases = [
            ("utf-8", "utf-8"),
            ("utf-8-sig", "utf-8-sig"),
            ("utf-16", "utf-16"),
            ("utf-16-le", "utf-16-le"),
            ("gbk", "gbk"),
        ]
        for name, enc in cases:
            path = root / f"{name}.txt"
            _write(path, sample, enc)
            try:
                dl = YoutubeDownloader(cookies_from_browser="")
                dl.set_cookiefile(path, persist=False, preferred_platform="youtube")
                assert dl._cookiefile_platform == "youtube"
                print(f"OK   {name}: platform={dl._cookiefile_platform}")
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")

        # Wrong platform should fail clearly
        bad = root / "wrong_plat.txt"
        _write(bad, sample, "utf-8")
        try:
            YoutubeDownloader(cookies_from_browser="").set_cookiefile(
                bad, persist=False, preferred_platform="tiktok"
            )
            failures += 1
            print("FAIL preferred_platform: expected ValueError")
        except ValueError as exc:
            print(f"OK   preferred_platform reject: {str(exc).splitlines()[0]}")

    print("PASS" if failures == 0 else f"FAILED ({failures})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
