"""下载工作线程 —— 桥接 YoutubeDownloader 与 GUI。

Worker 在后台线程中运行下载逻辑，通过 Qt Signals 向 GUI 报告
进度、速度和状态。Worker 不包含任何 UI 代码。

批量下载采用两阶段策略：
  1. 默认无 Cookie（速度快，覆盖绝大多数公开视频）
  2. 仅对需要登录/年龄验证的错误启用 Cookie 重试
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import time

import yt_dlp
from PySide6.QtCore import QThread, Signal

from downloader import ErrorCategory, YoutubeDownloader, _find_tool, classify_error, clean_error
from logger_utils import AppLogger


def _media_stem(source: str) -> str:
    try:
        media = YoutubeDownloader.parse_input(source)
        stem = media.media_id
    except Exception:
        stem = source
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)


def _check_existing(video_id: str, output_dir: Path) -> Path | None:
    """检查输出目录是否已有此视频的有效文件（用于去重）。"""
    import subprocess as _sp
    stem = _media_stem(video_id)
    for ext in (".mp4", ".webm", ".mkv", ".m4a"):
        candidate = output_dir / f"{stem}{ext}"
        if not candidate.is_file() or candidate.stat().st_size <= 1024:
            continue
        try:
            r = _sp.run(
                [_find_tool("ffprobe"), "-v", "quiet", "-show_entries",
                 "format=duration", "-of", "csv=p=0", str(candidate)],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
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
            info = self._downloader.get_info(self._video_id)
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
        all_finished(int, int, int, str): (downloaded, fail, skipped, csv_path)。
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
    all_finished = Signal(int, int, int, str)  # (downloaded, fail, skipped, csv_path)

    def __init__(
        self,
        downloader: YoutubeDownloader,
        video_ids: list[str],
        format_id: str,
        output_dir: Path,
        min_height: int = 720,
        results_dir: Path | None = None,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._downloader = downloader
        self._video_ids = video_ids
        self._format_id = format_id
        self._output_dir = output_dir
        self._results_dir = results_dir or output_dir
        self._min_height = min_height
        self._last_results_csv = ""
        self._last_skipped_csv = ""
        self._last_failed_csv = ""

    def run(self) -> None:
        """按顺序下载所有 video_id，两阶段策略。增量写入 + 续传。"""
        total = len(self._video_ids)
        results: list[dict[str, str]] = []
        downloaded = 0
        resumed = 0
        fail_count = 0
        skip_count = 0

        # ── 续传：读取上次结果中已完成的 ID ──
        completed_ids = self._load_completed_ids()

        # ── 打开增量写入的 CSV ──
        import csv as _csv
        from datetime import datetime as _dt
        timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
        csv_path = str(self._results_dir / f"batch_results_{timestamp}.csv")
        self._last_results_csv = csv_path
        _csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        _csv_writer = _csv.DictWriter(_csv_file, fieldnames=[
            "video_id", "platform", "source_url", "title", "status",
            "error_category", "error_message", "cookie_used", "output_dir",
        ])
        _csv_writer.writeheader()

        try:
            for i, vid in enumerate(self._video_ids):
                if self._downloader._cancelled:  # noqa: SLF001
                    break

                # 续传：跳过已完成的视频
                if vid in completed_ids:
                    self._log_resume_skip(i, total, vid)
                    resumed += 1
                    continue

                # ── 下载（默认 Cookie）─────────────────────
                status, cookie_used, category, error_msg, title = self._try_download(
                    vid, i, total
                )

                if status == "success":
                    downloaded += 1
                    self._append_csv(_csv_writer, _csv_file, vid, title, "success", "SUCCESS", "", cookie_used)
                elif status == "skipped":
                    skip_count += 1
                    self._append_csv(_csv_writer, _csv_file, vid, title, "skipped", "SKIPPED",
                                     clean_error(Exception(error_msg)), cookie_used)
                else:
                    if self._downloader.redetect_browser():
                        status, cookie_used, category, error_msg, title = self._try_download(
                            vid, i, total
                        )
                    if status == "success":
                        downloaded += 1
                    elif status == "skipped":
                        skip_count += 1
                    else:
                        fail_count += 1
                    self._append_csv(_csv_writer, _csv_file, vid, title, status,
                                     category.code, clean_error(Exception(error_msg)), cookie_used)

                results.append({
                    "video_id": vid,
                    "platform": self._platform_for_source(vid),
                    "source_url": self._url_for_source(vid),
                    "status": status,
                    "error_category": category.code,
                    "error_message": clean_error(Exception(error_msg)),
                    "cookie_used": str(cookie_used).lower(),
                    "output_dir": str(self._output_dir),
                })
        finally:
            _csv_file.close()

        skipped_path = self._write_skipped_csv(results)
        failed_path = self._write_failed_csv(results)
        self._last_skipped_csv = skipped_path
        self._last_failed_csv = failed_path

        self.all_progress_changed.emit(100)
        self.all_finished.emit(downloaded + resumed, fail_count, skip_count, csv_path)
        self._log_summary(csv_path, skipped_path, failed_path)

    def _load_completed_ids(self) -> set[str]:
        """合并所有历史结果 CSV 中已成功的 video_id（跨批次去重）。"""
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

    def _append_csv(
        self, writer: Any, f: Any, vid: str, title: str, status: str,
        category: str, error_msg: str, cookie_used: bool,
    ) -> None:
        """增量写入一行结果到 CSV。"""
        writer.writerow({
            "video_id": vid,
            "platform": self._platform_for_source(vid),
            "source_url": self._url_for_source(vid),
            "title": title,
            "status": status,
            "error_category": category, "error_message": error_msg,
            "cookie_used": str(cookie_used).lower(),
            "output_dir": str(self._output_dir),
        })
        f.flush()

    def _log_resume_skip(self, index: int, total: int, video_id: str) -> None:
        """续传跳过日志。"""
        self.video_started.emit(index, total, video_id, False)
        self.video_finished.emit(index, "续传跳过（已完成）", False)
        self.status_changed.emit(f"[{index + 1}/{total}] 续传跳过: {video_id}")

    def _resolve_format(self, info: dict[str, Any]) -> tuple[str | None, bool]:
        """为当前视频匹配最佳 format_id + 是否需要合并音频。

        Returns:
            (format_id, needs_audio_merge)
            format_id 为 None 表示低于阈值应跳过。
        """
        preferred = self._format_id
        formats = self._downloader.list_formats(
            info=info,
            min_height=self._min_height,
        )
        available_ids = {f["format_id"] for f in formats}

        def _needs_merge(fmt: dict) -> bool:
            return fmt.get("type") == "Video Only"

        if preferred in available_ids:
            try:
                preferred_fmt = next(f for f in formats if f["format_id"] == preferred)
                if preferred_fmt.get("container") == "mp4" and preferred_fmt.get("type") in {"Video+Audio", "Video Only"}:
                    return (preferred, _needs_merge(preferred_fmt))
            except StopIteration:
                pass

        result = self._downloader.resolve_format_id(formats, min_height=self._min_height)
        if result is None:
            return (None, False)
        result_fmt = next((f for f in formats if f["format_id"] == result), None)
        return (result, _needs_merge(result_fmt) if result_fmt else False)

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
        self, video_id: str, index: int, total: int, use_cookies: bool = True
    ) -> tuple[str, bool, ErrorCategory, str, str]:
        """尝试获取信息 + 下载。默认使用 Cookie。

        Returns:
            (status, cookie_used, category, error_or_path, title)
        """
        self.video_started.emit(index, total, video_id, use_cookies)
        on_progress = self._make_progress_cb(index, total)

        title = ""
        try:
            existing = _check_existing(video_id, self._output_dir)
            if existing:
                self.video_finished.emit(index, str(existing), use_cookies)
                return ("success", use_cookies, ErrorCategory("SUCCESS", False, ""), str(existing), "")

            info = self._downloader.get_info(video_id, use_cookies=use_cookies)
            title = info.get("title", "") or ""
            if use_cookies:
                fmt, merge = "best", False
            else:
                fmt, merge = self._resolve_format(info)

            if fmt is None:
                msg = f"低于 {self._min_height}p，跳过下载: {video_id}"
                self.video_error.emit(index, msg, use_cookies)
                return ("skipped", use_cookies, ErrorCategory("SKIPPED", False, msg), msg, title)

            path = self._downloader.download(
                video_id=video_id, format_id=fmt,
                output_dir=self._output_dir,
                progress_callback=on_progress, use_cookies=use_cookies,
                needs_audio_merge=merge,
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
                        fmt, merge = self._resolve_format(
                            self._downloader.get_info(video_id)
                        )
                        if fmt is None:
                            return ("skipped", use_cookies, ErrorCategory("SKIPPED", False, ""), "", "")
                        path = self._downloader.download(
                            video_id=video_id, format_id=fmt,
                            output_dir=self._output_dir,
                            progress_callback=on_progress,
                            needs_audio_merge=merge,
                        )
                        self.video_finished.emit(index, str(path), use_cookies)
                        return ("success", use_cookies, ErrorCategory("SUCCESS", False, ""), str(path), title)
                    except Exception:
                        continue

            AppLogger.log_exception(exc, f"批量下载失败: {video_id}")
            self.video_error.emit(index, clean_error(exc), use_cookies)
            return ("failed", use_cookies, cat, str(exc), title)

    def _write_results(self, results: list[dict[str, str]]) -> str:
        """将批量下载结果写入 CSV 文件。"""
        import csv as csv_module
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = str(self._results_dir / f"batch_results_{timestamp}.csv")

        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv_module.DictWriter(
                    f, fieldnames=[
                        "video_id", "platform", "source_url", "title", "status",
                        "error_category", "error_message", "cookie_used", "output_dir",
                    ]
                )
                writer.writeheader()
                writer.writerows(results)
        except OSError:
            try:
                fallback = str(Path.home() / f"batch_results_{timestamp}.csv")
                with open(fallback, "w", newline="", encoding="utf-8") as f:
                    writer = csv_module.DictWriter(
                        f, fieldnames=[
                            "video_id", "platform", "source_url", "status",
                            "error_category", "error_message", "cookie_used", "output_dir",
                        ]
                    )
                    writer.writeheader()
                    writer.writerows(results)
                csv_path = fallback
            except OSError:
                csv_path = "（写入失败）"

        return csv_path

    def _write_skipped_csv(self, results: list[dict[str, str]]) -> str:
        """导出低于阈值被跳过的视频 ID。"""
        skipped = [
            {
                "video_id": item["video_id"],
                "platform": item.get("platform", ""),
                "source_url": item.get("source_url", ""),
                "reason": item["error_message"],
            }
            for item in results if item.get("status") == "skipped"
        ]
        if not skipped:
            return ""

        import csv as csv_module
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(self._results_dir / f"skipped_videos_{timestamp}.csv")
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv_module.DictWriter(f, fieldnames=["video_id", "platform", "source_url", "reason"])
                writer.writeheader()
                writer.writerows(skipped)
        except OSError:
            return ""
        return path

    def _write_failed_csv(self, results: list[dict[str, str]]) -> str:
        """导出下载失败的视频 ID，便于重新下载。"""
        failed = [
            {
                "video_id": item["video_id"],
                "platform": item.get("platform", ""),
                "source_url": item.get("source_url", ""),
                "reason": item["error_message"],
            }
            for item in results if item.get("status") == "failed"
        ]
        if not failed:
            return ""

        import csv as csv_module
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(self._results_dir / f"failed_videos_{timestamp}.csv")
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv_module.DictWriter(f, fieldnames=["video_id", "platform", "source_url", "reason"])
                writer.writeheader()
                writer.writerows(failed)
        except OSError:
            return ""
        return path

    def _log_summary(self, results_path: str, skipped_path: str, failed_path: str) -> None:
        """记录结果文件路径。"""
        self.status_changed.emit("批量下载完成")
        if skipped_path:
            self.status_changed.emit(f"跳过列表: {skipped_path}")
        if failed_path:
            self.status_changed.emit(f"失败重试列表: {failed_path}")
