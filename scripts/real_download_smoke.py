"""Opt-in real YouTube end-to-end acceptance test.

Not part of routine CI: public site limits and auth challenges are external.
Checks actual transfer, verified media, overview persistence and resume.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from batch_io import OverviewStore
from download_cli import main as cli_main
from media_probe import run_probe


def verify(url: str, root: Path) -> None:
    params = [
        url, "-o", str(root), "--workers", "1", "--min-height", "0",
        "--proxy-check-url", "", "--no-auto-recover",
    ]
    if cli_main(params) != 0:
        raise RuntimeError("First live download failed; inspect youtube_downloader.log")
    rows = OverviewStore(root).rows()
    successes = [r for r in rows if r["状态"] == "成功"]
    if not successes:
        raise AssertionError("No persisted success row after real download")
    for row in successes:
        media = root / row["输出文件"]
        if not media.is_file() or media.stat().st_size <= 1024:
            raise AssertionError(f"Missing or tiny downloaded file: {media}")
        ok, duration = run_probe([
            "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(media),
        ])
        if not ok or float(duration.strip()) <= 0:
            raise AssertionError(f"Invalid media: {media}")
    snapshot = {r["输出文件"]: (root / r["输出文件"]).stat().st_mtime_ns for r in successes}
    if cli_main(params) != 0:
        raise RuntimeError("Resume pass failed")
    if any((root / p).stat().st_mtime_ns != mtime for p, mtime in snapshot.items()):
        raise AssertionError("Resume unexpectedly re-downloaded a completed file")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Public, short, permitted YouTube test video URL")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    if args.output_dir is None:
        with tempfile.TemporaryDirectory(prefix="youtube-down-e2e-") as tmp:
            verify(args.url, Path(tmp))
    else:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        verify(args.url, args.output_dir)
    print("PASS: transfer, media verification, overview and resume")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
