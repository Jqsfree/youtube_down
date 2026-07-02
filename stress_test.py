"""压力测试 —— 批量下载、错误恢复、内存观察。

用法:
    PYTHONPATH=. .venv/bin/python3 stress_test.py
"""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path

from downloader import YoutubeDownloader, classify_error, clean_error

# 20 个已知可用视频 ID
TEST_IDS: list[str] = [
    "dQw4w9WgXcQ", "jNQXAC9IVRw", "9bZkp7q19f0",
    "kJQP7kiw5Fk", "JGwWNGJdvx8", "C0DPdy98e4c",
    "uelHwf8o7_U", "CevxZvSJLk8", "YQHsXMglC9A",
    "lp-EO5I60KA", "nfs8NYg7yQM", "kffacxfA7G4",
    "lWA2pjMjpBs", "e-ORhEE9VVg", "SDTZ7iX4vTQ",
    "PtJ6yAGjsIs", "NUsoVlDFqZg", "j5-yKhDd64s",
    "INVALID_ID_XYZ", "ALSO_FAKE_999",
]


def test_info_extraction(dl: YoutubeDownloader) -> tuple[int, int]:
    """批量 get_info，统计成功/失败。"""
    ok = fail = 0
    t0 = time.monotonic()
    for vid in TEST_IDS:
        try:
            info = dl.get_info(vid, use_cookies=False)
            ok += 1
        except Exception as exc:
            cat = classify_error(exc)
            if cat.retry_cookie:
                try:
                    info = dl.get_info(vid, use_cookies=True)
                    ok += 1
                    continue
                except Exception:
                    pass
            fail += 1
    elapsed = time.monotonic() - t0
    print(f"  get_info: {ok}/{len(TEST_IDS)} ok, {fail} fail  ({elapsed:.1f}s)")
    return ok, fail


def test_format_listing(dl: YoutubeDownloader) -> None:
    """测试 list_formats 在不同 min_height 下的行为。"""
    info = dl.get_info("dQw4w9WgXcQ")
    for h in (0, 720, 1080, 1440, 2160):
        fmts = dl.list_formats(info=info, min_height=h)
        print(f"  list_formats(min_height={h:4d}): {len(fmts):2d} formats")


def test_download_single(dl: YoutubeDownloader) -> None:
    """下载一个视频验证完整链路。"""
    out = Path("/tmp/stress_test_dl")
    out.mkdir(parents=True, exist_ok=True)
    info = dl.get_info("dQw4w9WgXcQ")
    fmts = dl.list_formats(info=info, min_height=720)
    if not fmts:
        print("  download: 无可下载格式")
        return
    fmt = fmts[0]
    t0 = time.monotonic()
    try:
        path = dl.download("dQw4w9WgXcQ", fmt["format_id"], out)
        elapsed = time.monotonic() - t0
        print(f"  download: {path.stat().st_size / 1024 / 1024:.1f}MB"
              f"  {elapsed:.1f}s  ({fmt['resolution']} {fmt['container']})")
    except Exception as exc:
        print(f"  download FAIL: {clean_error(exc)}")


def test_csv_load() -> None:
    """测试 CSV 加载（多编码、URL 提取）。"""
    import tempfile
    tmp = Path(tempfile.mktemp(suffix=".csv"))
    # URL 格式
    tmp.write_text("video_id\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\njNQXAC9IVRw\n")
    ids = YoutubeDownloader.load_csv(tmp)
    print(f"  csv(utf-8, url): {ids}")
    # TSV 格式
    tmp2 = Path(tempfile.mktemp(suffix=".tsv"))
    tmp2.write_text("video_id\n9bZkp7q19f0\nkJQP7kiw5Fk\n")
    rows = YoutubeDownloader.load_csv_rows(tmp2, column="video_id")
    ids2 = [r["video_id"] for r in rows]
    print(f"  csv(tsv): {ids2}")
    tmp.unlink(missing_ok=True)
    tmp2.unlink(missing_ok=True)


def main() -> None:
    print("=" * 50)
    print("YouTube Downloader 压力测试")
    print("=" * 50)

    # 内存追踪
    tracemalloc.start()
    dl = YoutubeDownloader()
    print(f"\nCookie: {dl._cookies_spec}")

    # ── 信息提取 ──
    print("\n[1] 批量 get_info (20 IDs)")
    ok, fail = test_info_extraction(dl)

    # ── 格式列表 ──
    print("\n[2] list_formats 分辨率过滤")
    test_format_listing(dl)

    # ── 下载 ──
    print("\n[3] 单视频完整下载")
    test_download_single(dl)

    # ── CSV ──
    print("\n[4] CSV 加载（多编码 + URL 提取）")
    test_csv_load()

    # ── 错误分类 ──
    print("\n[5] 错误分类")
    from downloader import _CATEGORY_RULES
    for code, retry, _ in _CATEGORY_RULES:
        print(f"  {code:20s}  retry_cookie={retry}")

    # ── 内存 ──
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"\n[6] 内存: current={current / 1024:.0f}KB  peak={peak / 1024:.0f}KB")

    print(f"\n{'=' * 50}")
    print(f"结果: get_info={ok}/{len(TEST_IDS)} ok 内存峰值={peak / 1024:.0f}KB")
    print("压力测试完成" if ok >= len(TEST_IDS) - 2 else "有异常，请检查")


if __name__ == "__main__":
    main()
