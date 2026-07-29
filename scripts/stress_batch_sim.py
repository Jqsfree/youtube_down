#!/usr/bin/env python3
"""Simulate long / large batch downloads without hitting real networks.

Usage::

    python scripts/stress_batch_sim.py
    python scripts/stress_batch_sim.py --count 500 --delay-ms 5 --groups 3

Exercises BatchDownloadWorker with mixed YouTube/Bilibili/TikTok IDs,
cookie retries, quality skips, hard failures, resume, and multi-group queues.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import yt_dlp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.disable(logging.ERROR)

from worker import AUTO_FORMAT_ID, BatchDownloadWorker  # noqa: E402


class StressDownloader:
    """Fake downloader with deterministic outcomes + artificial latency."""

    def __init__(self, delay_s: float = 0.005) -> None:
        self._cancelled = False
        self.delay_s = delay_s
        self.calls = 0
        self.download_calls = 0
        self.get_info_calls = 0
        self.cookie_retries = 0
        self.t0 = time.perf_counter()

    def cancel(self) -> None:
        self._cancelled = True

    @staticmethod
    def _tag(source: str) -> str:
        """Extract scenario tag embedded as __TAG__ in the id/url."""
        for tag in ("AUTH", "FAIL", "LOW", "OK"):
            if f"__{tag}__" in source:
                return tag
        return "OK"

    def has_cookie_for_source(self, source: str) -> bool:
        tag = self._tag(source)
        if tag == "AUTH":
            return True
        return hash(source) % 7 == 0

    def get_info(self, source: str, use_cookies: bool = True, **kwargs):
        self.calls += 1
        self.get_info_calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        if use_cookies:
            self.cookie_retries += 1

        tag = self._tag(source)
        if tag == "FAIL":
            raise yt_dlp.utils.DownloadError("Network error: connection reset")
        if tag == "AUTH" and not use_cookies:
            raise yt_dlp.utils.DownloadError("Sign in to confirm you're not a bot")
        if tag == "LOW":
            return {
                "title": f"low-{source}",
                "duration": 30,
                "formats": [
                    {
                        "format_id": "18",
                        "resolution": "360p",
                        "height": 360,
                        "ext": "mp4",
                        "vcodec": "avc1",
                        "acodec": "mp4a",
                        "filesize": 1024,
                    }
                ],
            }
        return {
            "title": f"ok-{source[:24]}",
            "duration": 120,
            "formats": [
                {
                    "format_id": "137",
                    "resolution": "1080p",
                    "height": 1080,
                    "ext": "mp4",
                    "vcodec": "avc1",
                    "acodec": "none",
                    "filesize": 5_000_000,
                },
                {
                    "format_id": "140",
                    "resolution": "audio only",
                    "height": 0,
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "mp4a",
                    "filesize": 200_000,
                },
                {
                    "format_id": "22",
                    "resolution": "720p",
                    "height": 720,
                    "ext": "mp4",
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                    "filesize": 2_000_000,
                },
            ],
        }

    def list_formats(self, info, min_height=None):
        out = []
        for f in info.get("formats", []):
            height = int(f.get("height") or 0)
            vcodec = f.get("vcodec") or "none"
            acodec = f.get("acodec") or "none"
            if vcodec == "none" and acodec != "none":
                ftype = "Audio Only"
            elif acodec == "none":
                ftype = "Video Only"
            else:
                ftype = "Video+Audio"
            out.append(
                {
                    "format_id": f["format_id"],
                    "resolution": f.get("resolution") or f"{height}p",
                    "container": f.get("ext", "mp4"),
                    "type": ftype,
                    "height": height,
                }
            )
        return out

    def resolve_format_id(self, formats, target_height=720, strict=True, **kwargs):
        exact = [f for f in formats if f.get("height") == target_height and f["type"] != "Audio Only"]
        if exact:
            vo = [f for f in exact if f["type"] == "Video Only"]
            return (vo or exact)[0]["format_id"]
        if strict and target_height == 1080:
            p720 = [f for f in formats if f.get("height") == 720 and f["type"] != "Audio Only"]
            if p720:
                return p720[0]["format_id"]
        if strict:
            return None
        best = max(
            (f for f in formats if f["type"] != "Audio Only"),
            key=lambda f: f.get("height") or 0,
            default=None,
        )
        return best["format_id"] if best else None

    def download(self, video_id: str, format_id: str, output_dir: Path, use_cookies: bool = True, **kwargs):
        self.calls += 1
        self.download_calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        path = output_dir / f"{video_id.replace('/', '_')}.mp4"
        path.write_bytes(b"\x00" * 2048)
        return path


def _yt_id(i: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    base = f"yt{i:08d}"
    return (base + alphabet)[:11]


def _make_ids(count: int, group: int = 0) -> list[str]:
    """Build mixed-platform IDs with embedded __TAG__ markers for scenarios."""
    ids: list[str] = []
    for i in range(count):
        kind = i % 10
        if kind == 0:
            ids.append(f"https://www.youtube.com/watch?v={_yt_id(i)}&x=__AUTH__&g={group}")
        elif kind == 1:
            ids.append(f"https://www.youtube.com/watch?v={_yt_id(i)}&x=__FAIL__&g={group}")
        elif kind == 2:
            ids.append(f"https://www.youtube.com/watch?v={_yt_id(i)}&x=__LOW__&g={group}")
        elif kind == 3:
            ids.append(f"BV1GJ{group}11x{i % 10}h{(i + group) % 10}")
        elif kind == 4:
            ids.append(
                f"https://www.tiktok.com/@u/video/{7_100_000_000_000_000_000 + group * 100_000 + i}"
            )
        else:
            ids.append(_yt_id(i + group * 10_000))
    return ids


def _read_csv_stats(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            st = row.get("status") or "?"
            counts[st] = counts.get(st, 0) + 1
            plat = row.get("platform") or ""
            key = f"plat:{plat or 'empty'}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def run_batch(
    label: str,
    ids: list[str],
    out: Path,
    results_dir: Path,
    delay_s: float,
) -> tuple[int, int, int, str, StressDownloader]:
    dl = StressDownloader(delay_s=delay_s)
    finished: list[tuple[int, int, int, str]] = []

    worker = BatchDownloadWorker(
        downloader=dl,  # type: ignore[arg-type]
        video_ids=ids,
        format_id=AUTO_FORMAT_ID,
        output_dir=out,
        min_height=1080,
        strict_quality=True,
        results_dir=results_dir,
    )

    def on_finished(ok: int, fail: int, skipped: int, csv_path: str) -> None:
        finished.append((ok, fail, skipped, csv_path))

    worker.all_finished.connect(on_finished)
    t0 = time.perf_counter()
    worker.run()
    elapsed = time.perf_counter() - t0

    assert finished, f"{label}: all_finished never emitted"
    ok, fail, skipped, csv_path = finished[0]
    stats = _read_csv_stats(Path(csv_path)) if Path(csv_path).exists() else {}

    print(
        f"[{label}] n={len(ids)} elapsed={elapsed:.2f}s "
        f"ok={ok} fail={fail} skip={skipped} "
        f"info={dl.get_info_calls} dl={dl.download_calls} cookie={dl.cookie_retries}"
    )
    print(f"  csv={csv_path}")
    print(f"  stats={stats}")
    return ok, fail, skipped, csv_path, dl


def main() -> int:
    parser = argparse.ArgumentParser(description="Large-batch download stress simulation")
    parser.add_argument("--count", type=int, default=300, help="videos per group")
    parser.add_argument("--groups", type=int, default=3, help="sequential queue groups")
    parser.add_argument("--delay-ms", type=float, default=3.0, help="fake latency per get_info/download")
    args = parser.parse_args()
    delay_s = max(0.0, args.delay_ms / 1000.0)

    with TemporaryDirectory(prefix="vd_stress_") as tmp:
        root = Path(tmp)
        results_dir = root / "results"
        results_dir.mkdir()

        all_ids: list[list[str]] = [_make_ids(args.count, group=g) for g in range(args.groups)]

        totals = {"ok": 0, "fail": 0, "skip": 0}
        t_all = time.perf_counter()

        for g, ids in enumerate(all_ids):
            out = root / f"group_{g}"
            out.mkdir()
            ok, fail, skipped, _csv_path, _ = run_batch(
                f"group{g}", ids, out, results_dir, delay_s,
            )
            totals["ok"] += ok
            totals["fail"] += fail
            totals["skip"] += skipped

        resume_ids = all_ids[0]
        out0 = root / "group_0"
        ok2, fail2, skip2, _csv2, dl2 = run_batch(
            "resume_g0", resume_ids, out0, results_dir, delay_s,
        )

        auth_ids = [x for x in all_ids[0] if "__AUTH__" in x][:40]
        dl_a = None
        if auth_ids:
            auth_out = root / "auth"
            auth_out.mkdir()
            auth_results = root / "auth_results"
            auth_results.mkdir()
            _ok_a, _fail_a, _skip_a, _csv_a, dl_a = run_batch(
                "auth_cookie", auth_ids, auth_out, auth_results, delay_s,
            )

        elapsed_all = time.perf_counter() - t_all

        print("\n=== SUMMARY ===")
        print(f"planned={args.count * args.groups} groups={args.groups} elapsed={elapsed_all:.2f}s")
        print(f"pass1 totals={totals}")
        print(f"resume ok={ok2} fail={fail2} skip={skip2} new_downloads={dl2.download_calls}")

        errors: list[str] = []
        if totals["ok"] <= 0:
            errors.append("no successes in pass1")
        success_ratio = totals["ok"] / max(1, args.count * args.groups)
        if success_ratio < 0.3:
            errors.append(f"success ratio too low: {success_ratio:.2f}")
        if dl2.download_calls > max(5, int(args.count * 0.45)):
            errors.append(
                f"resume re-downloaded too many: downloads={dl2.download_calls} (count={args.count})"
            )
        result_files = list(results_dir.glob("batch_results_*.csv"))
        if len(result_files) < args.groups + 1:
            errors.append(f"expected >= {args.groups + 1} result CSVs, got {len(result_files)}")
        if auth_ids and dl_a is not None and dl_a.cookie_retries < len(auth_ids):
            errors.append(
                f"cookie retries {dl_a.cookie_retries} < auth ids {len(auth_ids)}"
            )

        if errors:
            for e in errors:
                print(f"FAIL: {e}")
            return 1
        print("PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
