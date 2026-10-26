"""下载工作线程 —— 桥接 YoutubeDownloader 与 GUI。

Worker 在后台线程中运行下载逻辑，通过 Qt Signals 向 GUI 报告
进度、速度和状态。Worker 不包含任何 UI 代码。

批量下载采用两阶段策略：
  1. 默认无 Cookie（速度快，覆盖绝大多数公开视频）
  2. 仅对需要登录/年龄验证的错误启用 Cookie 重试
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from PySide6.QtCore import QThread, Signal

from batch_io import (
    OverviewStore,
    filename_stem,
    media_id_from_stem,
)
from downloader import (
    BRUTAL_EXTRA_RETRIES,
    BRUTAL_MAX_WORKERS,
    ErrorCategory,
    YoutubeDownloader,
    _find_tool,
    classify_error,
    clean_error,
    is_brutal_retryable,
)
from logger_utils import AppLogger

# DownloaderFacade is the architectural name; concrete class remains YoutubeDownloader.
DownloaderFacade = YoutubeDownloader

AUTO_FORMAT_ID = "auto"


def _media_stem(source: str) -> str:
    try:
        media = YoutubeDownloader.parse_input(source)
        stem = media.media_id
    except Exception:
        stem = source
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)


_VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".flv")


def _existing_candidates(stem: str, output_dir: Path) -> list[Path]:
    """匹配 ``{id}.ext``（旧命名）与 ``NNNN_{id}.ext``（行号命名）。"""
    if not output_dir.is_dir():
        return []
    return sorted(
        p for p in output_dir.iterdir()
        if p.suffix.lower() in _VIDEO_EXTS
        and (p.stem == stem or media_id_from_stem(p.stem) == stem)
    )


def _check_existing(video_id: str, output_dir: Path) -> Path | None:
    """检查输出目录是否已有此视频的有效文件（用于去重）。"""
    import subprocess as _sp
    stem = _media_stem(video_id)
    for candidate in _existing_candidates(stem, output_dir):
        if not candidate.is_file() or candidate.stat().st_size <= 1024:
            continue
        try:
            r = _sp.run(
                [_find_tool("ffprobe"), "-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(candidate)],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip() == "video":
                r2 = _sp.run(
                    [_find_tool("ffprobe"), "-v", "quiet", "-show_entries",
                     "format=duration", "-of", "csv=p=0", str(candidate)],
                    capture_output=True, text=True, timeout=10,
                )
                if r2.returncode == 0 and r2.stdout.strip():
                    return candidate
        except Exception:
            pass
    return None


class DownloadWorker(QThread):
    """后台下载线程（单视频）。

    Signals:
        progress_changed(int): 下载百分比 0–100。
        status_changed(str): 状态文字描述。
        speed_changed(str): 实时下载速度，如 "8.5MB/s"。
        eta_changed(str): 预计剩余时间，如 "2:30"。
        size_changed(str): 已下载/总大小，如 "45MB / 83MB"。
        finished(str): 下载完成，携带文件路径。
        error(str): 下载失败，携带错误信息。
    """

    progress_changed = Signal(int)
    status_changed = Signal(str)
    speed_changed = Signal(str)
    eta_changed = Signal(str)
    size_changed = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        downloader: YoutubeDownloader,
        video_id: str,
        format_id: str,
        output_dir: Path,
        min_height: int = 720,
        needs_audio_merge: bool = True,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._downloader = downloader
        self._video_id = video_id
        self._format_id = format_id
        self._output_dir = output_dir
        self._min_height = min_height
        self._needs_audio_merge = needs_audio_merge

    def run(self) -> None:
        """在后台线程中执行下载。"""
        # 去重检查
        existing = _check_existing(self._video_id, self._output_dir)
        if existing:
            self.finished.emit(str(existing))
            return

        try:

            def on_progress(d: dict[str, Any]) -> None:
                status = d.get("status", "")
                if status == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes", 0)
                    if total > 0:
                        pct = int(downloaded / total * 100)
                        self.progress_changed.emit(pct)

                    speed = d.get("speed") or ""
                    if speed and isinstance(speed, (int, float)):
                        speed = self._format_speed(speed)
                    self.speed_changed.emit(str(speed) if speed else "—")

                    eta = d.get("eta") or ""
                    if eta and isinstance(eta, (int, float)):
                        eta = self._format_eta(int(eta))
                    self.eta_changed.emit(str(eta) if eta else "—")

                    size_text = (
                        f"{self._format_size(downloaded)} / {self._format_size(total)}"
                    )
                    self.size_changed.emit(size_text)

                elif status == "finished":
                    self.status_changed.emit("处理中...")

            result_path = self._downloader.download(
                video_id=self._video_id,
                format_id=self._format_id,
                output_dir=self._output_dir,
                progress_callback=on_progress,
                needs_audio_merge=self._needs_audio_merge,
                min_height=self._min_height,
            )
            self.finished.emit(str(result_path))

        except Exception as exc:
            AppLogger.log_exception(exc, f"单视频下载失败: {self._video_id}")
            self.error.emit(str(exc))

    @staticmethod
    def _format_speed(speed: float) -> str:
        if speed < 1024:
            return f"{speed:.0f}B/s"
        if speed < 1024 * 1024:
            return f"{speed / 1024:.1f}KB/s"
        if speed < 1024 * 1024 * 1024:
            return f"{speed / (1024 * 1024):.1f}MB/s"
        return f"{speed / (1024 * 1024 * 1024):.2f}GB/s"

    @staticmethod
    def _format_eta(seconds: int) -> str:
        if seconds < 0:
            return "—"
        minutes, secs = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    @staticmethod
    def _format_size(size_bytes: int | float) -> str:
        if size_bytes is None or size_bytes <= 0:
            return "—"
        size_bytes = int(size_bytes)
        if size_bytes < 1024:
            return f"{size_bytes}B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"


class FetchInfoWorker(QThread):
    """后台获取视频信息（单视频），避免主线程阻塞 GUI。"""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        downloader: YoutubeDownloader,
        video_id: str,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._downloader = downloader
        self._video_id = video_id

    def run(self) -> None:
        try:
            info = self._downloader.get_info(
                self._video_id,
                use_cookies=self._downloader.prefers_cookies_for_fetch(),
            )
            self.finished.emit(info)
        except Exception as exc:
            self.error.emit(clean_error(exc))


class ValidateCookieWorker(QThread):
    """后台验证 Cookie（网络请求），避免导入时阻塞 GUI。"""

    finished = Signal(bool, str)

    def __init__(
        self,
        downloader: YoutubeDownloader,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._downloader = downloader

    def run(self) -> None:
        ok, msg = self._downloader.validate_cookies()
        self.finished.emit(ok, msg)



class BatchDownloadWorker(QThread):
    """批量下载线程 —— 两阶段策略。

    Stage 1: 所有视频默认无 Cookie 下载（速度快）。
    Stage 2: 仅对 auth_required 类错误启用 Cookie 重试。

    Signals:
        all_progress_changed(int): 整体进度 0–100。
        video_started(int, int, str, bool): (index, total, video_id, cookie)。
        video_finished(int, str, bool): (index, path, cookie_used)。
        video_error(int, str, bool): (index, msg, cookie_used)。
        progress_changed(int): 当前视频下载百分比。
        speed_changed(str): 实时下载速度。
        eta_changed(str): 预计剩余时间。
        size_changed(str): 已下载/总大小。
        status_changed(str): 状态文字。
        all_finished(int, int, int, str): (downloaded, fail, skipped, overview_path)。
    """

    all_progress_changed = Signal(int)
    video_started = Signal(int, int, str, bool)
    video_finished = Signal(int, str, bool)
    video_error = Signal(int, str, bool)
    progress_changed = Signal(int)
    speed_changed = Signal(str)
    eta_changed = Signal(str)
    size_changed = Signal(str)
    status_changed = Signal(str)
    all_finished = Signal(int, int, int, str)  # (downloaded, fail, skipped, overview_path)

    def __init__(
        self,
        downloader: YoutubeDownloader,
        video_ids: list[str],
        format_id: str,
        output_dir: Path,
        min_height: int = 720,
        strict_quality: bool = True,
        results_dir: Path | None = None,
        cookie_overrides: dict[str, dict[str, str]] | None = None,
        brutal_mode: bool = False,
        overview: OverviewStore | None = None,
        source_rel: str = "",
        row_numbers: dict[str, int] | None = None,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._downloader = downloader
        self._video_ids = video_ids
        self._format_id = format_id
        self._output_dir = output_dir
        self._results_dir = results_dir or output_dir
        self._min_height = min_height
        self._strict_quality = strict_quality
        self._brutal_mode = brutal_mode
        self._cookie_overrides = cookie_overrides or {}
        self._overview = overview or OverviewStore(self._results_dir)
        self._source_rel = source_rel or self._default_source_rel()
        given_rows = row_numbers or {}
        self._row_numbers = {
            vid: given_rows.get(vid) or index + 1
            for index, vid in enumerate(video_ids)
        }

    @property
    def overview_path(self) -> Path:
        return self._overview.path

    def _default_source_rel(self) -> str:
        try:
            rel = self._output_dir.resolve().relative_to(self._results_dir.resolve())
        except ValueError:
            return self._output_dir.name
        return rel.as_posix() if rel.parts else self._output_dir.name

    def _filename_stem(self, vid: str) -> str:
        return filename_stem(self._row_numbers.get(vid, 0), _media_stem(vid))

    def _record(
        self, vid: str, title: str, status: str,
        category: str = "", message: str = "", output_file: str = "",
    ) -> None:
        self._overview.upsert(
            source=self._source_rel,
            row_no=self._row_numbers.get(vid, 0),
            media_id=_media_stem(vid),
            platform=self._platform_for_source(vid),
            url=self._url_for_source(vid),
            title=title,
            status=status,
            category=category,
            message=message,
            output_file=output_file,
        )

    def _is_cancelled(self) -> bool:
        dl = self._downloader
        if hasattr(dl, "_is_cancelled"):
            return dl._is_cancelled()
        return bool(getattr(dl, "_cancelled", False))

    def run(self) -> None:
        """按顺序或暴力并行下载所有 video_id。增量写入 + 续传。"""
        total = len(self._video_ids)
        overview_done = self._overview.completed_ids(self._source_rel)
        legacy_done = self._load_completed_ids()

        results: list[dict[str, str]] = []
        downloaded = 0
        resumed = 0
        fail_count = 0
        skip_count = 0
        pending: list[tuple[int, str]] = []

        for i, vid in enumerate(self._video_ids):
            if self._is_cancelled():
                break
            stem = _media_stem(vid)
            if stem in overview_done:
                self._log_resume_skip(i, total, vid)
                resumed += 1
                continue
            if vid in legacy_done or stem in legacy_done:
                self._log_resume_skip(i, total, vid)
                self._record(vid, "", "success")
                resumed += 1
                continue
            pending.append((i, vid))

        if self._brutal_mode and pending:
            d, f, s, results = self._run_brutal_parallel(pending, total, results)
            downloaded, fail_count, skip_count = d, f, s
        else:
            for i, vid in pending:
                if self._is_cancelled():
                    break
                row = self._process_one_video(i, vid, total)
                results.append(row)
                if row["status"] == "success":
                    downloaded += 1
                elif row["status"] == "skipped":
                    skip_count += 1
                else:
                    fail_count += 1

        overview_path = str(self._overview.path)
        self.all_progress_changed.emit(100)
        self.all_finished.emit(downloaded + resumed, fail_count, skip_count, overview_path)
        self.status_changed.emit(f"批量下载完成，总览: {overview_path}")

    def _run_brutal_parallel(
        self,
        pending: list[tuple[int, str]],
        total: int,
        results: list[dict[str, str]],
    ) -> tuple[int, int, int, list[dict[str, str]]]:
        max_workers = self._brutal_max_workers()
        if max_workers < BRUTAL_MAX_WORKERS:
            self.status_changed.emit(
                "浏览器 Cookie 不支持多线程，已改为单线程暴力下载"
            )

        stats_lock = threading.Lock()
        results_lock = threading.Lock()
        stats = {"completed": 0, "downloaded": 0, "fail": 0, "skip": 0}

        def _job(index: int, vid: str) -> dict[str, str]:
            row = self._process_one_video_brutal(index, vid, total)
            with stats_lock:
                stats["completed"] += 1
                pct = int(stats["completed"] / len(pending) * 100)
                status = row["status"]
                if status == "success":
                    stats["downloaded"] += 1
                elif status == "skipped":
                    stats["skip"] += 1
                else:
                    stats["fail"] += 1
            self.all_progress_changed.emit(pct)
            self._record(
                vid, row.get("title", ""), status, row["error_category"],
                row["error_message"], row.get("output_file", ""),
            )
            with results_lock:
                results.append(row)
            return row

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_job, index, vid): (index, vid)
                for index, vid in pending
            }
            for fut in as_completed(futures):
                if self._is_cancelled():
                    for pending_fut in futures:
                        pending_fut.cancel()
                    break
                try:
                    fut.result()
                except Exception as exc:
                    index, vid = futures[fut]
                    AppLogger.log_exception(exc, f"暴力下载任务异常: {vid}")
                    row = self._result_row(
                        vid, "failed", "UNKNOWN", clean_error(exc), False, "",
                    )
                    self._record(vid, "", "failed", "UNKNOWN", clean_error(exc))
                    with results_lock:
                        results.append(row)
                    with stats_lock:
                        stats["fail"] += 1

        return stats["downloaded"], stats["fail"], stats["skip"], results

    def _brutal_max_workers(self) -> int:
        if self._uses_browser_cookies_only():
            return 1
        return BRUTAL_MAX_WORKERS

    def _uses_browser_cookies_only(self) -> bool:
        for args in self._cookie_overrides.values():
            if args.get("cookiefile"):
                return False
            if args.get("cookies_from_browser"):
                return True
        return self._downloader.uses_browser_cookies_only()

    def _result_row(
        self,
        vid: str,
        status: str,
        category: str,
        error_msg: str,
        cookie_used: bool,
        title: str,
    ) -> dict[str, str]:
        success = status == "success"
        return {
            "video_id": vid,
            "platform": self._platform_for_source(vid),
            "source_url": self._url_for_source(vid),
            "title": title,
            "status": status,
            "error_category": category,
            "error_message": "" if success else error_msg,
            "cookie_used": str(cookie_used).lower(),
            "output_file": error_msg if success else "",
        }

    def _finish_row(
        self, vid: str, status: str, category: ErrorCategory,
        error_or_path: str, cookie_used: bool, title: str,
    ) -> dict[str, str]:
        if status not in ("success", "skipped"):
            status = "failed"
        detail = error_or_path if status == "success" else clean_error(Exception(error_or_path))
        return self._result_row(vid, status, category.code, detail, cookie_used, title)

    def _process_one_video(self, index: int, vid: str, total: int) -> dict[str, str]:
        status, cookie_used, category, error_msg, title = self._try_download(
            vid, index, total, use_cookies=False,
        )
        if status == "failed" and (
            self._should_retry_with_cookie(category, error_msg)
            and self._has_cookie_for_source(vid)
        ):
            status, cookie_used, category, error_msg, title = self._try_download(
                vid, index, total, use_cookies=True,
            )
        row = self._finish_row(vid, status, category, error_msg, cookie_used, title)
        self._record(
            vid, title, row["status"], row["error_category"],
            row["error_message"], row["output_file"],
        )
        return row

    def _process_one_video_brutal(self, index: int, vid: str, total: int) -> dict[str, str]:
        dl = self._downloader.clone_for_worker(brutal=True)
        status, cookie_used, category, error_msg, title = self._try_download(
            vid, index, total, use_cookies=False, downloader=dl, brutal=True,
        )
        if status == "failed":
            if self._should_retry_with_cookie(category, error_msg) and self._has_cookie_for_source(vid):
                status, cookie_used, category, error_msg, title = self._try_download(
                    vid, index, total, use_cookies=True, downloader=dl, brutal=True,
                )
        if status == "failed" and is_brutal_retryable(category):
            for attempt in range(BRUTAL_EXTRA_RETRIES):
                if self._is_cancelled():
                    break
                wait = 2 ** (attempt + 1)
                self.status_changed.emit(f"暴力重试 {vid}，{wait}s 后…")
                time.sleep(wait)
                status, cookie_used, category, error_msg, title = self._try_download(
                    vid, index, total, use_cookies=cookie_used, downloader=dl, brutal=True,
                )
                if status != "failed":
                    break
        return self._finish_row(vid, status, category, error_msg, cookie_used, title)

    def _load_completed_ids(self) -> set[str]:
        """旧版 batch_results_*.csv 中已成功的 video_id（只读兼容）。"""
        import csv as _csv
        completed: set[str] = set()
        for csv_file in self._results_dir.glob("batch_results_*.csv"):
            try:
                with open(csv_file, "r", encoding="utf-8") as f:
                    for row in _csv.DictReader(f):
                        if row.get("status") == "success":
                            completed.add(row["video_id"])
            except Exception:
                pass
        return completed

    @staticmethod
    def _platform_for_source(source: str) -> str:
        try:
            return YoutubeDownloader.parse_input(source).platform
        except Exception:
            return ""

    @staticmethod
    def _url_for_source(source: str) -> str:
        try:
            return YoutubeDownloader.parse_input(source).url
        except Exception:
            return source

    def _cookie_args_for_source(self, source: str) -> dict[str, str]:
        return self._cookie_overrides.get(source, {})

    def _has_cookie_for_source(self, source: str) -> bool:
        args = self._cookie_args_for_source(source)
        if args.get("cookiefile") or args.get("cookies_from_browser"):
            return True
        return self._downloader.has_cookie_for_source(source)

    @staticmethod
    def _should_retry_with_cookie(category: ErrorCategory, error_msg: str) -> bool:
        if category.retry_cookie:
            return True
        lowered = (error_msg or "").lower()
        return any(
            marker in lowered
            for marker in (
                "login",
                "premium",
                "member",
                "会员",
                "大会员",
                "format(s)",
                "use --cookies",
                "cookies-from-browser",
            )
        )

    def _log_resume_skip(self, index: int, total: int, video_id: str) -> None:
        """续传跳过日志。"""
        self.video_started.emit(index, total, video_id, False)
        self.video_finished.emit(index, "续传跳过（已完成）", False)
        self.status_changed.emit(f"[{index + 1}/{total}] 续传跳过: {video_id}")

    def _resolve_format(self, info: dict[str, Any]) -> tuple[str | None, bool, int | None]:
        """为当前视频匹配最佳 format_id + 是否需要合并音频。

        Returns:
            (format_id, needs_audio_merge, expected_height)
            format_id 为 None 表示低于阈值应跳过。
        """
        preferred = self._format_id
        formats = self._downloader.list_formats(info=info)
        available_ids = {f["format_id"] for f in formats}

        def _needs_merge(fmt: dict) -> bool:
            return fmt.get("type") == "Video Only"

        def _expected_height(fmt: dict | None) -> int | None:
            if fmt is None:
                return None
            height = YoutubeDownloader.format_height(fmt)
            return height or None

        if preferred in (AUTO_FORMAT_ID, "", None):
            if not self._strict_quality:
                return ("best", False, None)

            result = self._downloader.resolve_format_id(
                formats,
                target_height=self._min_height,
                strict=True,
            )
            if result is None:
                return (None, False, None)
            result_fmt = next((f for f in formats if f["format_id"] == result), None)
            return (
                result,
                _needs_merge(result_fmt) if result_fmt else False,
                _expected_height(result_fmt),
            )

        if preferred not in (AUTO_FORMAT_ID, "", None) and preferred in available_ids:
            try:
                preferred_fmt = next(f for f in formats if f["format_id"] == preferred)
                preferred_height = YoutubeDownloader.format_height(preferred_fmt)
                if (
                    not self._strict_quality
                    or preferred_height == self._min_height
                    or preferred_height == 0
                ):
                    if preferred_fmt.get("type") in {"Video+Audio", "Video Only"}:
                        return (
                            preferred,
                            _needs_merge(preferred_fmt),
                            _expected_height(preferred_fmt),
                        )
            except StopIteration:
                pass

        result = self._downloader.resolve_format_id(
            formats,
            target_height=self._min_height,
            strict=self._strict_quality,
        )
        if result is None:
            return (None, False, None)
        result_fmt = next((f for f in formats if f["format_id"] == result), None)
        return (
            result,
            _needs_merge(result_fmt) if result_fmt else False,
            _expected_height(result_fmt),
        )

    def _make_progress_cb(self, index: int, total: int) -> Callable[[dict[str, Any]], None]:
        """创建进度回调（闭包捕获 index/total）。"""
        def on_progress(d: dict[str, Any]) -> None:
            status = d.get("status", "")
            if status == "downloading":
                total_b = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total_b > 0:
                    cur_pct = int(downloaded / total_b * 100)
                    self.progress_changed.emit(cur_pct)
                    all_pct = int((index + cur_pct / 100) / total * 100)
                    self.all_progress_changed.emit(all_pct)

                speed = d.get("speed") or ""
                if speed and isinstance(speed, (int, float)):
                    speed = DownloadWorker._format_speed(speed)
                self.speed_changed.emit(str(speed) if speed else "—")

                eta = d.get("eta") or ""
                if eta and isinstance(eta, (int, float)):
                    eta = DownloadWorker._format_eta(int(eta))
                self.eta_changed.emit(str(eta) if eta else "—")

                size_text = (
                    f"{DownloadWorker._format_size(downloaded)}"
                    f" / {DownloadWorker._format_size(total_b)}"
                )
                self.size_changed.emit(size_text)
            elif status == "finished":
                self.status_changed.emit("处理中...")
        return on_progress

    def _try_download(
        self,
        video_id: str,
        index: int,
        total: int,
        use_cookies: bool = False,
        downloader: YoutubeDownloader | None = None,
        brutal: bool = False,
    ) -> tuple[str, bool, ErrorCategory, str, str]:
        """尝试获取信息 + 下载。默认不使用 Cookie。

        Returns:
            (status, cookie_used, category, error_or_path, title)
        """
        dl = downloader or self._downloader
        self.video_started.emit(index, total, video_id, use_cookies)
        on_progress = self._make_progress_cb(index, total)

        title = ""
        try:
            existing = _check_existing(video_id, self._output_dir)
            if existing:
                self.video_finished.emit(index, str(existing), use_cookies)
                return ("success", use_cookies, ErrorCategory("SUCCESS", False, ""), str(existing), "")

            cookie_args = self._cookie_args_for_source(video_id) if use_cookies else {}
            info = dl.get_info(
                video_id,
                use_cookies=use_cookies,
                cookiefile_override=cookie_args.get("cookiefile"),
                cookies_from_browser_override=cookie_args.get("cookies_from_browser"),
            )
            title = info.get("title", "") or ""
            fmt, merge, expected_height = self._resolve_format(info)

            if fmt is None:
                msg = f"低于 {self._min_height}p，跳过下载: {video_id}"
                self.video_error.emit(index, msg, use_cookies)
                return ("skipped", use_cookies, ErrorCategory("SKIPPED", False, msg), msg, title)

            path = dl.download(
                video_id=video_id, format_id=fmt,
                output_dir=self._output_dir,
                filename_stem=self._filename_stem(video_id),
                progress_callback=on_progress, use_cookies=use_cookies,
                needs_audio_merge=merge,
                min_height=0 if expected_height is not None else self._min_height,
                expected_height=expected_height,
                strict_quality=self._strict_quality,
                cookiefile_override=cookie_args.get("cookiefile"),
                cookies_from_browser_override=cookie_args.get("cookies_from_browser"),
                brutal=brutal,
            )
            self.video_finished.emit(index, str(path), use_cookies)
            return ("success", use_cookies, ErrorCategory("SUCCESS", False, ""), str(path), title)

        except Exception as exc:
            cat = classify_error(exc)
            if cat.code == "RATE_LIMIT":
                for attempt in range(2):
                    wait = 2 ** (attempt + 2)
                    self.status_changed.emit(f"限流，{wait}s 后重试...")
                    time.sleep(wait)
                    try:
                        fmt, merge, expected_height = self._resolve_format(
                            dl.get_info(video_id, use_cookies=False)
                        )
                        if fmt is None:
                            return ("skipped", use_cookies, ErrorCategory("SKIPPED", False, ""), "", "")
                        path = dl.download(
                            video_id=video_id, format_id=fmt,
                            output_dir=self._output_dir,
                            filename_stem=self._filename_stem(video_id),
                            progress_callback=on_progress,
                            use_cookies=False,
                            needs_audio_merge=merge,
                            min_height=0 if expected_height is not None else self._min_height,
                            expected_height=expected_height,
                            strict_quality=self._strict_quality,
                            brutal=brutal,
                        )
                        self.video_finished.emit(index, str(path), use_cookies)
                        return ("success", use_cookies, ErrorCategory("SUCCESS", False, ""), str(path), title)
                    except Exception:
                        continue

            AppLogger.log_exception(exc, f"批量下载失败: {video_id}")
            self.video_error.emit(index, clean_error(exc), use_cookies)
            return ("failed", use_cookies, cat, str(exc), title)


class YtdlpUpdateWorker(QThread):
    """后台检查 / 下载 yt-dlp overlay，不在 GUI 线程访问网络。"""

    check_done = Signal(bool, str, str, str)
    apply_done = Signal(bool, str)

    def __init__(self, action: str = "check", parent: QThread | None = None) -> None:
        super().__init__(parent)
        self._action = action

    def run(self) -> None:
        import yt_dlp_updater as updater

        try:
            if self._action == "check":
                result = updater.check_for_updates()
                if not result.error:
                    updater.mark_checked()
                self.check_done.emit(
                    result.available,
                    result.current_yt_dlp,
                    result.latest_yt_dlp,
                    result.error,
                )
                return
            message = updater.perform_update()
            self.apply_done.emit(True, message)
        except Exception as exc:
            if self._action == "check":
                self.check_done.emit(False, "", "", str(exc))
            else:
                self.apply_done.emit(False, str(exc))
