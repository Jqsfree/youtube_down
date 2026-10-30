"""Qt-free batch policies, task execution, queue controls and completion handling."""
from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterator


from batch_io import (
    OverviewStore,
    media_id_from_stem,
)
from downloader import (
    AUDIO_EXTS,
    BOT_STREAK_LIMIT,
    BRUTAL_EXTRA_RETRIES,
    BRUTAL_MAX_WORKERS,
    VIDEO_EXTS,
    ErrorCategory,
    YoutubeDownloader,
    _run_probe,
    classify_error,
    clean_error,
    is_brutal_retryable,
)
from logger_utils import AppLogger, redact
from pacing import AdaptivePacer

# GUI 顺序批量默认间隔（与 CLI --sleep-min/max 默认对齐）
_GUI_SLEEP_MIN = 2.0
_GUI_SLEEP_MAX = 4.0

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


def _existing_candidates(
    stem: str, output_dir: Path, exts: tuple[str, ...] = VIDEO_EXTS,
) -> list[Path]:
    """匹配 ``{id}.ext``（旧命名）与 ``NNNN_{id}.ext``（行号命名）。"""
    if not output_dir.is_dir():
        return []
    return sorted(
        p for p in output_dir.iterdir()
        if p.suffix.lower() in exts
        and (p.stem == stem or media_id_from_stem(p.stem) == stem)
    )


def _probe_short_side(path: Path, probe: Callable | None = None) -> int | None:
    """ffprobe 视频短边；无法解析时返回 None。"""
    probe = probe or _run_probe
    ok, out = probe(
        ["-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        timeout=10,
    )
    if not ok:
        return None
    try:
        parts = [p.strip() for p in out.strip().split(",") if p.strip()]
        dims = [int(p) for p in parts[:2]]
        if len(dims) >= 2:
            return min(dims)
        return dims[0] if dims else None
    except ValueError:
        return None


def _resume_min_height(video_id: str, min_height: int) -> int:
    """直链没有分辨率元数据，续传时不能按清晰度档位过滤。"""
    try:
        if YoutubeDownloader.parse_input(video_id).platform == "generic":
            return 0
    except ValueError:
        pass
    return min_height


def _check_existing(
    video_id: str, output_dir: Path, min_height: int = 0, audio_only: bool = False,
    probe: Callable | None = None, short_side: Callable | None = None,
) -> Path | None:
    """检查输出目录是否已有此视频的有效文件（用于去重）。"""
    probe = probe or _run_probe
    short_side = short_side or _probe_short_side
    stem = _media_stem(video_id)
    for candidate in _existing_candidates(
        stem, output_dir, AUDIO_EXTS if audio_only else VIDEO_EXTS,
    ):
        if not candidate.is_file() or candidate.stat().st_size <= 1024:
            continue
        if not audio_only:
            ok, out = probe(
                ["-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(candidate)],
                timeout=10,
            )
            if not ok or out.strip() != "video":
                continue
        ok, out = probe(
            ["-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(candidate)],
            timeout=10,
        )
        if not ok or not out.strip():
            continue
        if min_height > 0 and not audio_only:
            short = short_side(candidate)
            if short is None or short < min_height:
                continue
        return candidate
    return None


def _format_speed(speed: float) -> str:
    if speed < 1024:
        return f"{speed:.0f}B/s"
    if speed < 1024 * 1024:
        return f"{speed / 1024:.1f}KB/s"
    if speed < 1024 * 1024 * 1024:
        return f"{speed / (1024 * 1024):.1f}MB/s"
    return f"{speed / (1024 * 1024 * 1024):.2f}GB/s"


def _format_eta(seconds: int) -> str:
    if seconds < 0:
        return "—"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


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


def parallel_workers(requested: int, browser_only: bool = False) -> int:
    return 1 if browser_only else max(1, requested)


class CompletionPool:
    """Own bounded execution and completion metadata for both entry points."""
    def __init__(self, max_workers: int, pool_factory=None, completed=None):
        self._factory = pool_factory or ThreadPoolExecutor
        self._completed = completed or as_completed
        self.max_workers = parallel_workers(max_workers)
        self.inflight: dict = {}

    def __enter__(self):
        self._context = self._factory(max_workers=self.max_workers)
        self._executor = self._context.__enter__()
        return self

    def submit(self, item, operation, *args):
        future = self._executor.submit(operation, *args)
        self.inflight[future] = item
        return future

    def completed(self):
        return self._completed(self.inflight)

    def pop(self, future):
        return self.inflight.pop(future)

    def cancel_pending(self):
        cancelled = []
        for future, item in list(self.inflight.items()):
            if future.cancel():
                cancelled.append(item)
        self.inflight.clear()
        return cancelled

    def __exit__(self, *args):
        return self._context.__exit__(*args)


class BatchRunnerCore:
    """Business methods shared with the Qt adapter, without a Qt constructor."""
    def _notify(self, name: str, *args) -> None:
        callback = getattr(self, '_callbacks', {}).get(name)
        if callback is not None:
            try:
                callback(*args)
            except Exception as exc:
                AppLogger.log_exception(exc, '批量回调失败')

    def _find_existing(self, *args, **kwargs):
        return _check_existing(*args, **kwargs)

    def _make_pacer(self):
        return AdaptivePacer(_GUI_SLEEP_MIN, _GUI_SLEEP_MAX, bot_pause_sec=30.0)

    def _make_pool(self, max_workers):
        return CompletionPool(max_workers)

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
        # A live item can add a browser source after the pool was created.
        browser_only = self._source_uses_browser_cookies(vid)
        if browser_only:
            with self._browser_cookie_lock:
                if self._record_error is not None:
                    raise self._record_error
                if self._is_cancelled():
                    return self._result_row(vid, 'failed', 'CANCELLED', '下载已取消', False, '')
                return self._process_one_video_brutal_unlocked(index, vid, total)
        return self._process_one_video_brutal_unlocked(index, vid, total)

    def _process_one_video_brutal_unlocked(self, index: int, vid: str, total: int) -> dict[str, str]:
        dl = self._downloader.clone_for_worker(brutal=True)
        try:
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
                    self._notify("status_changed", f"暴力重试 {vid}，{wait}s 后…")
                    time.sleep(wait)
                    if self._is_cancelled():
                        break
                    status, cookie_used, category, error_msg, title = self._try_download(
                        vid, index, total, use_cookies=cookie_used, downloader=dl, brutal=True,
                    )
                    if status != "failed":
                        break
            return self._finish_row(vid, status, category, error_msg, cookie_used, title)
        finally:
            if getattr(dl, '_browser_cookie_broken', False):
                self._downloader._browser_cookie_broken = True

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

    def _resolve_format(self, info: dict[str, Any]) -> tuple[str | None, bool, int | None]:
        """为当前视频匹配最佳 format_id + 是否需要合并音频。

        Returns:
            (format_id, needs_audio_merge, expected_height)
            format_id 为 None 表示低于阈值应跳过。
        """
        preferred = self._format_id
        if getattr(self, "_audio_only", False):
            return ("bestaudio/best", False, None)
        formats = self._downloader.list_formats(info=info)
        if not formats and info.get("_platform") == "generic":
            # 直链没有 height，无法按档位筛选；取 best 并不做分辨率校验（0 = 未指定）
            return ("best", False, 0)
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
                    self._notify("progress_changed", cur_pct)
                    # 整体进度按「已完成条数 + 当前视频内部进度」折算，
                    # index/total 只用于信号展示，不参与这里的分母。
                    self._notify("all_progress_changed", self._overall_pct(cur_pct))

                speed = d.get("speed") or ""
                if speed and isinstance(speed, (int, float)):
                    speed = _format_speed(speed)
                self._notify("speed_changed", str(speed) if speed else "—")

                eta = d.get("eta") or ""
                if eta and isinstance(eta, (int, float)):
                    eta = _format_eta(int(eta))
                self._notify("eta_changed", str(eta) if eta else "—")

                size_text = (
                    f"{_format_size(downloaded)}"
                    f" / {_format_size(total_b)}"
                )
                self._notify("size_changed", size_text)
            elif status == "finished":
                self._notify("status_changed", "处理中...")
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
        self._notify("video_started", index, total, video_id, use_cookies)
        on_progress = self._make_progress_cb(index, total)

        title = ""
        try:
            existing = self._find_existing(
                video_id, self._output_dir,
                min_height=_resume_min_height(video_id, self._min_height),
                audio_only=self._audio_only,
            )
            if existing:
                self._notify("video_finished", index, str(existing), use_cookies, "")
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
                self._notify("video_error", index, msg, use_cookies)
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
                audio_only=self._audio_only,
                audio_format=self._audio_format,
            )
            self._notify("video_finished", index, str(path), use_cookies, title)
            return ("success", use_cookies, ErrorCategory("SUCCESS", False, ""), str(path), title)

        except Exception as exc:
            cat = classify_error(exc)
            if cat.code == "RATE_LIMIT":
                for attempt in range(2):
                    wait = 2 ** (attempt + 2)
                    self._notify("status_changed", f"限流，{wait}s 后重试...")
                    time.sleep(wait)
                    if self._is_cancelled():
                        break
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
                            audio_only=self._audio_only,
                            audio_format=self._audio_format,
                        )
                        self._notify("video_finished", index, str(path), use_cookies, title)
                        return ("success", use_cookies, ErrorCategory("SUCCESS", False, ""), str(path), title)
                    except Exception:
                        continue

            AppLogger.log_exception(exc, f"批量下载失败: {video_id}")
            self._notify("video_error", index, clean_error(exc), use_cookies)
            return ("failed", use_cookies, cat, str(exc), title)


    def _initialize(
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
        audio_only: bool = False,
        audio_format: str = "m4a",
        callbacks: dict[str, Callable] | None = None,
    ) -> None:
        self._downloader = downloader
        self._video_ids = video_ids
        self._format_id = format_id
        self._output_dir = output_dir
        self._results_dir = results_dir or output_dir
        self._min_height = min_height
        self._strict_quality = strict_quality
        self._brutal_mode = brutal_mode
        self._audio_only = audio_only
        self._audio_format = audio_format
        self._cookie_overrides = cookie_overrides or {}
        self._overview = overview or OverviewStore(self._results_dir)
        self._source_rel = source_rel or self._default_source_rel()
        given_rows = row_numbers or {}
        self._row_numbers = {
            vid: given_rows.get(vid) or index + 1
            for index, vid in enumerate(video_ids)
        }
        self._pause_event = threading.Event()
        self._pause_event.set()

        # 活动队列：run() 期间可继续 enqueue 新任务
        self._live_cond = threading.Condition()
        self._live: deque[tuple[int, str]] = deque()
        self._seen_ids: set[str] = set(video_ids)
        self._next_index = len(video_ids)
        self._expected = len(video_ids)
        self._accepting = False
        self._queue_open = False
        self._idle_exit_seconds = 3.0
        # 整体进度：已完成条数 / 期望总数（含续传跳过项）
        self._progress_done = 0
        self._callbacks = callbacks or {}
        self._fatal_error = ""
        self._browser_cookie_lock = threading.Lock()
        self._failed_records: set[str] = set()
        self._record_error: Exception | None = None
        self._indices = {vid: index for index, vid in enumerate(video_ids)}

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
        return _media_stem(vid)

    def _record(
        self, vid: str, title: str, status: str,
        category: str = "", message: str = "", output_file: str = "",
    ) -> None:
        try:
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
        except Exception as exc:
            with self._live_cond:
                self._failed_records.add(vid)
                if self._record_error is None:
                    self._record_error = exc
            self.close_queue()
            self._notify('video_error', self._indices.get(vid, 0),
                         f"无法写入下载总览，已下载文件保留：{clean_error(exc)}", False)
            raise


    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def _wait_if_paused(self) -> None:
        """暂停只发生在两个视频之间；取消会结束等待。"""
        while not self._pause_event.is_set() and not self._is_cancelled():
            self._pause_event.wait(0.2)

    def _is_cancelled(self) -> bool:
        dl = self._downloader
        if hasattr(dl, "_is_cancelled"):
            return dl._is_cancelled()
        return bool(getattr(dl, "_cancelled", False))

    def _denominator(self, total: int) -> int:
        """进度分母：活动队列模式下取当前期望总数。"""
        if not self._queue_open:
            return max(1, total)
        with self._live_cond:
            return max(1, self._expected)

    def _advance_progress(self, count: int = 1) -> None:
        """推进整体进度：分子含续传跳过项，分母是期望总数。

        旧实现用「本轮待下载条数」当分母，一旦有续传跳过就永远到不了 100%。
        """
        if count <= 0:
            return
        with self._live_cond:
            self._progress_done += count
            done = self._progress_done
            total = max(1, self._expected)
        self._notify("all_progress_changed", min(100, int(done / total * 100)))

    def _overall_pct(self, current_pct: float = 0.0) -> int:
        """当前视频内部进度折算成整体百分比。"""
        with self._live_cond:
            done = self._progress_done + current_pct / 100
            total = max(1, self._expected)
        return min(100, int(done / total * 100))

    def run(self) -> None:
        self._fatal_error = ""
        self._failed_records = set()
        self._record_error = None
        self._batch_results = []
        self._resumed = 0
        try:
            self._run_batch()
        except Exception as exc:
            self.close_queue()
            self._fatal_error = redact(f"批量执行失败，已停止；已下载文件保留：{clean_error(exc)}")
            AppLogger.log_exception(exc, "批量执行失败")
            rows = self._batch_results
            success = self._resumed + sum(row['status'] == 'success' for row in rows)
            failed = sum(row['status'] == 'failed' for row in rows)
            failed += len(self._failed_records) or int(self._expected > len(rows) + self._resumed)
            skipped = sum(row['status'] == 'skipped' for row in rows)
            self._notify('status_changed', self._fatal_error)
            self._notify('all_finished', success, failed, skipped, str(self._overview.path))
            raise
        finally:
            self.close_queue()

    def _run_batch(self) -> None:
        """按顺序或暴力并行下载所有 video_id。增量写入 + 续传。"""
        if hasattr(self._downloader, "clear_cancel"):
            self._downloader.clear_cancel()
        with self._live_cond:
            self._expected = max(self._expected, self._next_index)
            if self._queue_open:
                self._accepting = True
        total = len(self._video_ids)
        overview_done = self._overview.completed_ids(self._source_rel)
        legacy_done = self._load_completed_ids()

        results = self._batch_results
        downloaded = 0
        resumed = 0
        fail_count = 0
        skip_count = 0
        pending: list[tuple[int, str]] = []

        for i, vid in enumerate(self._video_ids):
            if self._is_cancelled():
                break
            stem = _media_stem(vid)
            # 总览只说明「曾成功」；文件被删/损坏时必须重下（与 CLI 一致）
            if stem in overview_done:
                existing = self._find_existing(
                    vid, self._output_dir,
                    min_height=_resume_min_height(vid, self._min_height),
                    audio_only=self._audio_only,
                )
                if existing:
                    self._log_resume_skip(i, total, vid, existing)
                    resumed += 1
                    self._resumed = resumed
                    continue
            elif vid in legacy_done or stem in legacy_done:
                existing = self._find_existing(
                    vid, self._output_dir,
                    min_height=_resume_min_height(vid, self._min_height),
                    audio_only=self._audio_only,
                )
                if existing:
                    self._log_resume_skip(i, total, vid, existing)
                    self._record(vid, "", "success", output_file=str(existing))
                    resumed += 1
                    self._resumed = resumed
                    continue
            pending.append((i, vid))

        # 续传跳过也是「已完成」，否则分母含它而分子不含，进度到不了 100%
        if resumed:
            self._advance_progress(resumed)

        if self._brutal_mode and (pending or self._queue_open):
            d, f, s, results = self._run_brutal_parallel(pending, total, results)
            downloaded, fail_count, skip_count = d, f, s
        else:
            downloaded, fail_count, skip_count, results = self._run_sequential(
                pending, total, results,
            )

        self.close_queue()
        overview_path = str(self._overview.path)
        cancelled = self._is_cancelled()
        if not cancelled:
            self._notify("all_progress_changed", 100)
        self._notify("all_finished", downloaded + resumed, fail_count, skip_count, overview_path)
        self._notify("status_changed",
            "已取消" if cancelled else f"批量下载完成，总览: {overview_path}"
        )

    @staticmethod
    def _is_bot_or_rate(row: dict[str, str]) -> bool:
        cat = row.get("error_category") or ""
        if cat in {"BOT_VERIFICATION", "RATE_LIMIT"}:
            return True
        err = (row.get("error_message") or "").lower()
        return "not a bot" in err or "captcha" in err

    def _run_sequential(
        self,
        pending: list[tuple[int, str]],
        total: int,
        results: list[dict[str, str]],
    ) -> tuple[int, int, int, list[dict[str, str]]]:
        """顺序批量：AdaptivePacer 间隔 + 连续 bot/429 熔断。"""
        pacer = self._make_pacer()
        bot_streak = 0
        downloaded = fail_count = skip_count = 0
        work = self._iter_work(pending)
        idx = -1
        while True:
            self._wait_if_paused()
            if self._is_cancelled():
                break
            item = next(work, None)
            if item is None:
                break
            idx += 1
            i, vid = item
            if bot_streak >= BOT_STREAK_LIMIT:
                self._notify("status_changed",
                    "连续触发机器人验证/限流，已停止本批剩余任务"
                )
                # Stop admission without discarding the accepted live backlog.
                with self._live_cond:
                    self._accepting = False
                    self._live_cond.notify_all()
                leftovers = [vid]
                while True:
                    nxt = next(work, None)
                    if nxt is None:
                        break
                    leftovers.append(nxt[1])
                self.close_queue()
                for left_vid in leftovers:
                    row = self._result_row(
                        left_vid, "skipped", "BOT_VERIFICATION",
                        "风控熔断：连续 bot/限流，已跳过", False, "",
                    )
                    results.append(row)
                    self._record(
                        left_vid, "", "skipped", "BOT_VERIFICATION",
                        row["error_message"], "",
                    )
                    skip_count += 1
                break

            row = self._process_one_video(i, vid, self._denominator(total))
            results.append(row)
            if row["status"] == "success":
                downloaded += 1
                bot_streak = 0
                pacer.on_success()
            elif row["status"] == "skipped":
                skip_count += 1
                bot_streak = 0
                pacer.on_skip()
            else:
                fail_count += 1
                if self._is_bot_or_rate(row):
                    bot_streak += 1
                    self._notify("status_changed",
                        f"触发限流/bot，退避后继续（连续 {bot_streak}/{BOT_STREAK_LIMIT}）"
                    )
                    pacer.on_bot_or_rate_limit()
                else:
                    bot_streak = 0

            if not self._is_cancelled():
                # 活动队列模式：只有确实还有待跑任务时才节流
                more = self._live_pending() if self._queue_open else idx < len(pending) - 1
                if more:
                    pacer.wait()
            self._advance_progress()

        return downloaded, fail_count, skip_count, results

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

    def _brutal_max_workers(self) -> int:
        return parallel_workers(BRUTAL_MAX_WORKERS, self._uses_browser_cookies_only())

    def _source_uses_browser_cookies(self, source: str) -> bool:
        args = self._cookie_args_for_source(source)
        if args.get('cookiefile'):
            return False
        file_for = getattr(self._downloader, '_cookiefile_for_platform', None)
        if file_for is not None and file_for(self._platform_for_source(source)):
            return False
        if args.get('cookies_from_browser'):
            return True
        if getattr(self._downloader, '_browser_cookie_broken', False):
            return False
        return bool(getattr(self._downloader, '_cookies_spec', None)) or self._downloader.uses_browser_cookies_only()

    def _uses_browser_cookies_only(self) -> bool:
        sources = set(self._video_ids) | self._cookie_overrides.keys()
        if any(self._source_uses_browser_cookies(source) for source in sources):
            return True
        browser_default = bool(getattr(self._downloader, '_cookies_spec', None)) and not getattr(self._downloader, '_browser_cookie_broken', False)
        return self._queue_open and (browser_default or self._downloader.uses_browser_cookies_only())

    def _log_resume_skip(
        self, index: int, total: int, video_id: str, path: Path,
    ) -> None:
        """续传跳过日志。"""
        self._notify("video_started", index, total, video_id, False)
        self._notify("video_finished", index, str(path), False, "")
        self._notify("status_changed", f"[{index + 1}/{total}] 续传跳过: {video_id}")

    def enable_live_queue(self) -> None:
        """开启活动队列：run() 排空后等待 enqueue() 追加的新任务。"""
        with self._live_cond:
            self._queue_open = True
            self._accepting = True

    def enqueue(
        self,
        video_ids: list[str],
        row_numbers: dict[str, int] | None = None,
        cookie_overrides: dict[str, dict[str, str]] | None = None,
    ) -> bool:
        """运行中追加任务；worker 已收尾或已取消时返回 False。"""
        given = row_numbers or {}
        added = 0
        with self._live_cond:
            if not (self._queue_open and self._accepting) or self._is_cancelled():
                return False
            for vid in video_ids:
                if not vid or vid in self._seen_ids:
                    continue
                self._seen_ids.add(vid)
                index = self._next_index
                self._next_index += 1
                self._row_numbers[vid] = given.get(vid) or index + 1
                self._indices[vid] = index
                self._live.append((index, vid))
                added += 1
            if not added:
                return True
            if cookie_overrides:
                # 整体替换而非原地 update：别的线程可能在遍历 .values()
                self._cookie_overrides = {**self._cookie_overrides, **cookie_overrides}
            self._expected = self._next_index
            self._live_cond.notify_all()
        self._notify("total_changed", self._expected)
        return True

    def close_queue(self) -> None:
        """停止等待新任务并丢弃尚未开始的任务（取消时调用）。"""
        with self._live_cond:
            self._accepting = False
            self._live.clear()
            self._live_cond.notify_all()

    def _live_open(self) -> bool:
        return self._queue_open and self._accepting

    def _live_pending(self) -> bool:
        with self._live_cond:
            return bool(self._live)

    def _wait_for_live_work(self) -> bool:
        """阻塞等待新任务；返回 False 表示队列关闭 / 取消 / 空闲超时。"""
        deadline: float | None = None
        while True:
            with self._live_cond:
                if self._live:
                    return True
                if not self._accepting or self._is_cancelled():
                    self._accepting = False
                    return False
                if not self._pause_event.is_set():
                    # 暂停期间不计空闲超时
                    deadline = None
                    self._live_cond.wait(0.2)
                    continue
                now = time.monotonic()
                if deadline is None:
                    deadline = now + self._idle_exit_seconds
                elif now >= deadline:
                    self._accepting = False
                    self._live_cond.notify_all()
                    return False
                self._live_cond.wait(0.2)

    def _next_live_item(self, block: bool = True) -> tuple[int, str] | None:
        """取下一个追加任务；队列关闭 / 空闲超时返回 None。"""
        deadline: float | None = None
        while True:
            with self._live_cond:
                if self._live:
                    return self._live.popleft()
                if not self._accepting or self._is_cancelled():
                    self._accepting = False
                    return None
                if not block:
                    return None
                if not self._pause_event.is_set():
                    deadline = None
                    self._live_cond.wait(0.2)
                    continue
                now = time.monotonic()
                if deadline is None:
                    deadline = now + self._idle_exit_seconds
                elif now >= deadline:
                    self._accepting = False
                    self._live_cond.notify_all()
                    return None
                self._live_cond.wait(0.2)

    def _iter_work(
        self, pending: list[tuple[int, str]],
    ) -> Iterator[tuple[int, str]]:
        """先跑初始列表；随后进入活动队列（仅 _queue_open 时等待新任务）。"""
        yield from pending
        if not self._queue_open:
            return
        while True:
            item = self._next_live_item()
            if item is None:
                return
            yield item

    def _run_brutal_parallel(
        self,
        pending: list[tuple[int, str]],
        total: int,
        results: list[dict[str, str]],
    ) -> tuple[int, int, int, list[dict[str, str]]]:
        max_workers = self._brutal_max_workers()
        if max_workers < BRUTAL_MAX_WORKERS:
            self._notify("status_changed",
                "浏览器 Cookie 不支持多线程，已改为单线程暴力下载"
            )

        stats_lock = threading.Lock()
        results_lock = threading.Lock()
        stats = {"completed": 0, "downloaded": 0, "fail": 0, "skip": 0, "bot_streak": 0}
        stop_bot = threading.Event()
        pending_iter = iter(pending)

        def _take(block: bool = False) -> tuple[int, str] | None:
            try:
                return next(pending_iter)
            except StopIteration:
                pass
            if not self._queue_open:
                return None
            return self._next_live_item(block=block)

        def _job(index: int, vid: str) -> dict[str, str]:
            if self._record_error is not None:
                raise self._record_error
            row = self._process_one_video_brutal(
                index, vid, self._denominator(total),
            )
            status = row["status"]
            self._record(
                vid, row.get("title", ""), status, row["error_category"],
                row["error_message"], row.get("output_file", ""),
            )
            with stats_lock:
                stats["completed"] += 1
                if status == "success":
                    stats["downloaded"] += 1
                    stats["bot_streak"] = 0
                elif status == "skipped":
                    stats["skip"] += 1
                    stats["bot_streak"] = 0
                else:
                    stats["fail"] += 1
                    if self._is_bot_or_rate(row):
                        stats["bot_streak"] += 1
                        if stats["bot_streak"] >= BOT_STREAK_LIMIT:
                            stop_bot.set()
                    else:
                        stats["bot_streak"] = 0
            if not self._is_cancelled():
                self._advance_progress()
            with results_lock:
                results.append(row)
            return row

        cancelled_pending = []
        with self._make_pool(max_workers) as pool:
            inflight = pool.inflight

            def _submit(block: bool = False) -> bool:
                self._wait_if_paused()
                if stop_bot.is_set() or self._is_cancelled() or self._record_error is not None:
                    return False
                item = _take(block)
                if item is None:
                    return False
                index, vid = item
                pool.submit((index, vid), _job, index, vid)
                return True

            for _ in range(max_workers):
                if not _submit():
                    break

            while inflight or self._live_open():
                if self._record_error is not None:
                    pool.cancel_pending()
                    raise self._record_error
                if stop_bot.is_set() or self._is_cancelled():
                    cancelled_pending.extend(pool.cancel_pending())
                    break
                if not inflight:
                    # 活动队列暂时为空：等新任务，避免忙等
                    if not self._wait_for_live_work():
                        break
                    for _ in range(max_workers):
                        if not _submit():
                            break
                    continue
                for fut in pool.completed():
                    index, vid = pool.pop(fut)
                    if self._is_cancelled() or stop_bot.is_set():
                        cancelled_pending.extend(pool.cancel_pending())
                        break
                    try:
                        fut.result()
                    except Exception as exc:
                        if vid in self._failed_records:
                            pool.cancel_pending()
                            raise
                        AppLogger.log_exception(exc, f"暴力下载任务异常: {vid}")
                        row = self._result_row(
                            vid, "failed", "UNKNOWN", clean_error(exc), False, "",
                        )
                        self._record(vid, "", "failed", "UNKNOWN", clean_error(exc))
                        with results_lock:
                            results.append(row)
                        with stats_lock:
                            stats["fail"] += 1
                    if stop_bot.is_set():
                        self._notify("status_changed",
                            "暴力模式连续触发机器人验证/限流，已停止提交新任务"
                        )
                        cancelled_pending.extend(pool.cancel_pending())
                        break
                    _submit()
                    break  # 重新 as_completed，纳入新提交的 future

            # Atomically stop admission, then account for every accepted unstarted item.
            if stop_bot.is_set():
                with self._live_cond:
                    self._accepting = False
                    leftovers = cancelled_pending + list(pending_iter) + list(self._live)
                    self._live.clear()
                    self._live_cond.notify_all()
                self.close_queue()
                for index, vid in leftovers:
                    row = self._result_row(
                        vid, "skipped", "BOT_VERIFICATION",
                        "风控熔断：连续 bot/限流，已跳过", False, "",
                    )
                    self._record(
                        vid, "", "skipped", "BOT_VERIFICATION",
                        row["error_message"], "",
                    )
                    with results_lock:
                        results.append(row)
                    with stats_lock:
                        stats["skip"] += 1

        if self._record_error is not None:
            raise self._record_error
        return stats["downloaded"], stats["fail"], stats["skip"], results


class BatchRunner(BatchRunnerCore):
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
        audio_only: bool = False,
        audio_format: str = "m4a",
        callbacks: dict[str, Callable] | None = None,
    ) -> None:
        self._initialize(
            downloader=downloader,
            video_ids=video_ids,
            format_id=format_id,
            output_dir=output_dir,
            min_height=min_height,
            strict_quality=strict_quality,
            results_dir=results_dir,
            cookie_overrides=cookie_overrides,
            brutal_mode=brutal_mode,
            overview=overview,
            source_rel=source_rel,
            row_numbers=row_numbers,
            audio_only=audio_only,
            audio_format=audio_format,
            callbacks=callbacks,
        )


def is_generic_source(source: str) -> bool:
    """直链没有分辨率元数据，不能按清晰度档位筛选或校验。"""
    try:
        return YoutubeDownloader.parse_input(source).platform == "generic"
    except Exception:
        return False


def direct_result(
    video_id: str,
    output_dir: Path,
    status: str,
    category: str,
    msg: str,
) -> dict[str, str]:
    return {
        "video_id": video_id,
        "status": status,
        "error_category": category,
        "error_message": msg,
        "cookie_used": "true",
        "output_dir": str(output_dir),
        "title": "",
        "path": "",
    }


def download_direct_item(
    dl: YoutubeDownloader, video_id: str, output_dir: Path, min_height: int,
    *, brutal: bool = False, stem: str | None = None,
    audio_only: bool = False, audio_format: str = "m4a",
) -> dict[str, str]:
    """Direct/best-format policy used by CLI; explicit cookies and retry timing stay intact."""
    effective_min = 0 if is_generic_source(video_id) else min_height
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            path = dl.download(
                video_id=video_id,
                format_id="best",
                output_dir=output_dir,
                filename_stem=stem,
                use_cookies=True,
                needs_audio_merge=True,
                min_height=effective_min,
                expected_height=effective_min if effective_min > 0 else None,
                strict_quality=True,
                brutal=brutal,
                audio_only=audio_only,
                audio_format=audio_format,
            )
            return {
                "video_id": video_id,
                "status": "success",
                "error_category": "SUCCESS",
                "error_message": "",
                "cookie_used": "true",
                "output_dir": str(output_dir),
                "title": "",
                "path": str(path),
            }
        except Exception as exc:
            last_exc = exc
            cat = classify_error(exc)
            msg = clean_error(exc)
            if "低于最低要求" in msg or ("低于" in msg and str(effective_min) in msg):
                return direct_result(video_id, output_dir, "skipped", "SKIPPED", msg)
            if "requested format is not available" in msg.lower():
                return direct_result(video_id, output_dir, "skipped", "SKIPPED", msg)
            if cat.code == "RATE_LIMIT" and attempt == 0:
                time.sleep(10)
                continue
            return direct_result(
                video_id, output_dir,
                "failed" if cat.code != "SKIPPED" else "skipped",
                cat.code, msg,
            )
    assert last_exc is not None
    cat = classify_error(last_exc)
    return direct_result(video_id, output_dir, "failed", cat.code, clean_error(last_exc))
