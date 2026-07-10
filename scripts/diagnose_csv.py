#!/usr/bin/env python3
"""诊断 CSV 导入：打印编码、分隔符、识别列与 video_id 列表。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from downloader import YoutubeDownloader  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断 CSV/TSV/TXT 视频列表导入")
    parser.add_argument("csv_path", type=Path, help="CSV 文件路径")
    args = parser.parse_args()

    path = args.csv_path.expanduser()
    if not path.is_file():
        print(f"文件不存在: {path}")
        return 1

    print(f"文件: {path}")
    print(f"大小: {path.stat().st_size} bytes")

    try:
        rows = YoutubeDownloader.load_csv_rows(path)
    except Exception as exc:
        print(f"解析失败: {exc}")
        return 2

    if not rows:
        print("未识别到有效 video_id / 链接")
        return 3

    column = rows[0].get("_import_column", "(自动扫描)")
    print(f"识别列: {column}")
    print(f"视频数: {len(rows)}")
    print("前 10 条:")
    for i, row in enumerate(rows[:10], 1):
        print(f"  {i}. {row['video_id']}")
    if len(rows) > 10:
        print(f"  ... 另有 {len(rows) - 10} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
