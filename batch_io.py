"""批量输入发现、输出目录镜像与下载总览 —— 纯逻辑，不依赖 PySide6。

输出布局::

    输出根/<输入相对路径去扩展名>/<行号>_<媒体ID>.mp4
    输出根/下载总览.csv        （唯一的结果说明文件，跨批次持续更新）
"""

from __future__ import annotations

import csv
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from downloader import YoutubeDownloader

OVERVIEW_NAME = "下载总览.csv"
PASTE_SOURCE = "paste"
INPUT_EXTS = (".csv", ".tsv", ".txt")
LEGACY_RESULT_PREFIXES = ("batch_results_", "failed_videos_", "skipped_videos_")

STATUS_LABELS = {"success": "成功", "failed": "失败", "skipped": "跳过"}
_LABEL_TO_STATUS = {v: k for k, v in STATUS_LABELS.items()}

OVERVIEW_FIELDS = [
    "来源文件", "行号", "video_id", "平台", "链接", "标题",
    "状态", "错误类型", "错误信息", "输出文件", "更新时间",
]

_ROW_PREFIX_RE = re.compile(r"^(\d{4,})_(.+)$")
_PASTE_SPLIT_RE = re.compile(r"[\s,，;；、]+")


@dataclass
class InputFile:
    """一个输入表格：rel 为相对输入根、去掉扩展名的 posix 路径（如 ``舞蹈/擦边``）。"""

    path: Path
    rel: str
    rows: list[dict[str, str]] = field(default_factory=list)


def safe_segment(name: str) -> str:
    """目录/文件名片段安全化（保留中英文、数字和 ._- 空格）。"""
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5._ -]+", "_", name).strip(" .")
    return cleaned or "source"


def media_stem(media_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in media_id)


def media_id_from_stem(stem: str) -> str:
    """``0003_AbZ9o3oWYjo`` -> ``AbZ9o3oWYjo``；旧命名原样返回。"""
    m = _ROW_PREFIX_RE.match(stem)
    return m.group(2) if m else stem


def output_dir_for(out_root: Path, rel: str) -> Path:
    path = out_root
    for part in re.split(r"[\\/]+", rel):
        if part:
            path = path / safe_segment(part)
    return path


def _is_input_candidate(path: Path, root: Path) -> bool:
    if path.suffix.lower() not in INPUT_EXTS or not path.is_file():
        return False
    rel_parts = path.relative_to(root).parts
    if any(part.startswith(".") for part in rel_parts):
        return False
    name = path.name
    if name == OVERVIEW_NAME or name.startswith(LEGACY_RESULT_PREFIXES):
        return False
    return "cookie" not in name.lower()


def discover_inputs(path: Path) -> tuple[list[InputFile], list[str]]:
    """文件 -> 单个输入；文件夹 -> 递归收集表格文件。返回 (输入列表, 错误信息)。"""
    path = path.expanduser()
    if path.is_file():
        candidates = [(path, path.stem)]
    elif path.is_dir():
        candidates = [
            (p, p.relative_to(path).with_suffix("").as_posix())
            for p in sorted(path.rglob("*"))
            if _is_input_candidate(p, path)
        ]
    else:
        return [], [f"路径不存在: {path}"]

    inputs: list[InputFile] = []
    errors: list[str] = []
    for file_path, rel in candidates:
        try:
            rows = YoutubeDownloader.load_csv_rows(file_path)
        except (FileNotFoundError, ValueError) as exc:
            errors.append(f"{rel}: {exc}")
            continue
        if not rows:
            errors.append(f"{rel}: 未识别到有效视频 ID/链接")
            continue
        inputs.append(InputFile(path=file_path, rel=rel, rows=rows))
    if path.is_dir() and not candidates:
        errors.append(f"{path}: 未找到 {'/'.join(INPUT_EXTS)} 文件")
    return inputs, errors


def parse_pasted_text(text: str) -> tuple[list[str], list[str]]:
    """切分粘贴文本（换行、逗号、分号、空格混排），返回 (有效来源去重, 无效片段)。"""
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for token in _PASTE_SPLIT_RE.split(text or ""):
        token = token.strip()
        if not token:
            continue
        try:
            source = YoutubeDownloader._normalize_video_id(token)
        except ValueError:
            source = ""
        if not source or YoutubeDownloader._media_cell_score(token) <= 0:
            invalid.append(token)
            continue
        if source not in seen:
            seen.add(source)
            valid.append(source)
    return valid, invalid


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """跨进程独占锁（Windows msvcrt / POSIX flock），锁文件与 CSV 同目录。"""
    lock_path = path.with_name(f".{path.name}.lock")
    with open(lock_path, "a+b") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class OverviewStore:
    """输出根目录下的 ``下载总览.csv``：主键 (来源文件, video_id)，线程安全、原子写入。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / OVERVIEW_NAME
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict[str, str]] = {}
        self._touched: set[tuple[str, str]] = set()
        self._load()

    def _load(self) -> None:
        self._rows = self._read_rows(self.path)

    @staticmethod
    def _read_rows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
        rows: dict[tuple[str, str], dict[str, str]] = {}
        if not path.is_file():
            return rows
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    key = (row.get("来源文件", ""), row.get("video_id", ""))
                    if key[1]:
                        rows[key] = {k: row.get(k, "") or "" for k in OVERVIEW_FIELDS}
        except (OSError, csv.Error, UnicodeDecodeError):
            return {}
        return rows

    def upsert(
        self,
        *,
        source: str,
        row_no: int,
        media_id: str,
        platform: str = "",
        url: str = "",
        title: str = "",
        status: str,
        category: str = "",
        message: str = "",
        output_file: str | Path = "",
    ) -> None:
        output_rel = ""
        if output_file:
            out = Path(output_file)
            try:
                output_rel = out.resolve().relative_to(self.root.resolve()).as_posix()
            except ValueError:
                output_rel = str(out)
        with self._lock:
            key = (source, media_id)
            old = self._rows.get(key, {})
            # 旧文件还在时，不因本次失败/跳过把已有的成功记录降级
            if status != "success" and old.get("状态") == STATUS_LABELS["success"]:
                old_path = Path(old.get("输出文件", ""))
                if old_path and (old_path if old_path.is_absolute() else self.root / old_path).is_file():
                    return
            self._rows[key] = {
                "来源文件": source,
                "行号": str(row_no),
                "video_id": media_id,
                "平台": platform or old.get("平台", ""),
                "链接": url or old.get("链接", ""),
                "标题": title or old.get("标题", ""),
                "状态": STATUS_LABELS.get(status, status),
                "错误类型": "" if status == "success" else category,
                "错误信息": "" if status == "success" else message,
                "输出文件": output_rel if status == "success" else "",
                "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._touched.add(key)
            self._flush()

    def _flush(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.path):
            # 重新读盘再合并：别的进程新增的行不会被本进程的旧快照抹掉
            merged = self._read_rows(self.path)
            for key in self._touched:
                row = self._rows.get(key)
                if row:
                    merged[key] = row
            rows = sorted(
                merged.values(),
                key=lambda r: (r["来源文件"], int(r["行号"]) if r["行号"].isdigit() else 0),
            )
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=OVERVIEW_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp, self.path)
            self._rows = merged
            self._touched.clear()

    def status_of(self, source: str, media_id: str) -> str:
        row = self._rows.get((source, media_id))
        return _LABEL_TO_STATUS.get(row["状态"], row["状态"]) if row else ""

    def completed_ids(self, source: str | None = None) -> set[str]:
        """状态为成功、输出路径非空且文件仍存在的 video_id。"""
        done: set[str] = set()
        with self._lock:
            for (src, media_id), row in self._rows.items():
                if source is not None and src != source:
                    continue
                if row["状态"] != STATUS_LABELS["success"]:
                    continue
                out = row["输出文件"]
                if not out or not (self.root / out).is_file():
                    continue
                done.add(media_id)
        return done

    def rows(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(r) for r in self._rows.values()]
