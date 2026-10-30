"""下载工作线程 —— 桥接 YoutubeDownloader 与 GUI。

批量策略由 batch_runner 提供，Worker 在后台线程中调用，通过 Qt Signals 向 GUI 报告
进度、速度和状态。Worker 不包含任何 UI 代码。

批量下载采用两阶段策略：
  1. 默认无 Cookie（速度快，覆盖绝大多数公开视频）
  2. 仅对需要登录/年龄验证的错误启用 Cookie 重试
"""

from __future__ import annotations

import threading
import time
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterator

from PySide6.QtCore import QThread, Signal

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
from logger_utils import AppLogger
from pacing import AdaptivePacer
import batch_runner as batch_ops
from batch_runner import (BatchRunnerCore, CompletionPool, _media_stem, _existing_candidates,
                          _resume_min_height, _format_speed, _format_eta, _format_size,
                          _GUI_SLEEP_MIN, _GUI_SLEEP_MAX, AUTO_FORMAT_ID)

# DownloaderFacade is the architectural name; concrete class remains YoutubeDownloader.
DownloaderFacade = YoutubeDownloader



def _probe_short_side(path: Path) -> int | None:
    return batch_ops._probe_short_side(path, probe=_run_probe)


def _check_existing(video_id: str, output_dir: Path, min_height: int = 0, audio_only: bool = False) -> Path | None:
    return batch_ops._check_existing(video_id, output_dir, min_height, audio_only,
                                     probe=_run_probe, short_side=_probe_short_side)


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


class BatchDownloadWorker(QThread, BatchRunnerCore):
    """批量下载线程 —— 两阶段策略。

    Stage 1: 所有视频默认无 Cookie 下载（速度快）。
    Stage 2: 仅对 auth_required 类错误启用 Cookie 重试。

    Signals:
        all_progress_changed(int): 整体进度 0–100。
        video_started(int, int, str, bool): (index, total, video_id, cookie)。
        video_finished(int, str, bool, str): (index, path, cookie_used, title)。
        video_error(int, str, bool): (index, msg, cookie_used)。
        progress_changed(int): 当前视频下载百分比。
        speed_changed(str): 实时下载速度。
        eta_changed(str): 预计剩余时间。
        size_changed(str): 已下载/总大小。
        status_changed(str): 状态文字。
        total_changed(int): 运行中追加任务后新的总任务数。
        all_finished(int, int, int, str): (downloaded, fail, skipped, overview_path)。

    活动队列：任务列表排空后 worker 不会立即退出，而是在
    ``_idle_exit_seconds`` 内等待 ``enqueue()`` 追加的新任务（持续下载）。
    ``close_queue()``（取消/用户停止）会立即结束等待。
    """

    all_progress_changed = Signal(int)
    video_started = Signal(int, int, str, bool)
    video_finished = Signal(int, str, bool, str)
    video_error = Signal(int, str, bool)
    progress_changed = Signal(int)
    speed_changed = Signal(str)
    eta_changed = Signal(str)
    size_changed = Signal(str)
    status_changed = Signal(str)
    total_changed = Signal(int)
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
        audio_only: bool = False,
        audio_format: str = "m4a",
        parent: QThread | None = None,
    ) -> None:
        QThread.__init__(self, parent)
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
        )

    def _notify(self, name: str, *args) -> None:
        getattr(self, name).emit(*args)

    def _find_existing(self, *args, **kwargs):
        return _check_existing(*args, **kwargs)

    def _make_pacer(self):
        return AdaptivePacer(_GUI_SLEEP_MIN, _GUI_SLEEP_MAX, bot_pause_sec=30.0)

    def _make_pool(self, max_workers):
        return CompletionPool(max_workers, pool_factory=ThreadPoolExecutor, completed=as_completed)


    # ------------------------------------------------------------------
    # 活动队列（运行中持续追加）
    # ------------------------------------------------------------------


    def run(self) -> None:
        try:
            BatchRunnerCore.run(self)
        except Exception:
            # Core has already closed admission and reported the failure via signals.
            return


class PlaylistExpandWorker(QThread):
    """后台展开播放列表/频道为单条视频来源。"""

    expanded = Signal(list)
    failed = Signal(str)

    def __init__(
        self,
        downloader: YoutubeDownloader,
        url: str,
        limit: int | None = None,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._downloader = downloader
        self._url = url
        self._limit = limit

    def run(self) -> None:
        try:
            urls = self._downloader.expand_playlist(self._url, limit=self._limit)
        except Exception as exc:
            self.failed.emit(clean_error(exc))
            return
        self.expanded.emit(urls)


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


class AppUpdateWorker(QThread):
    """后台检查 / 下载应用更新包（GitHub Release），不在 GUI 线程访问网络。

    action:
        ``check``    → 只比较版本，结果放在 ``self.check`` / ``self.release``。
        ``download`` → 下载 ``release`` 对应平台的资产，结果路径放在 ``self.payload``。
    """

    check_done = Signal(bool, str, str, str, str)  # available, current, latest, notes, error
    progress = Signal(int)
    download_done = Signal(bool, str)  # ok, message-or-path

    def __init__(
        self,
        action: str = "check",
        release: Any = None,
        dest_dir: Path | None = None,
        parent: QThread | None = None,
    ) -> None:
        super().__init__(parent)
        self._action = action
        self._release = release
        self._dest_dir = dest_dir
        self.check: Any = None
        self.release: Any = None
        self.payload: Path | None = None

    def run(self) -> None:
        import app_updater as updater

        try:
            if self._action == "check":
                result = updater.check_for_app_update()
                self.check = result
                self.release = result.release
                notes = (result.release.notes if result.release else "") or ""
                self.check_done.emit(
                    result.available,
                    result.current,
                    result.latest,
                    notes,
                    result.error,
                )
                return

            release = self._release
            if release is None:
                self.download_done.emit(False, "没有可用的发布信息")
                return
            dest = self._dest_dir or (Path(tempfile.gettempdir()) / f"ytdown-update-{release.version}")
            # download_update 会在 release 带 SHA256SUMS.txt 时校验完整性
            payload = updater.download_update(
                release, dest, progress=self.progress.emit,
            )
            self.payload = payload
            self.download_done.emit(True, str(payload))
        except Exception as exc:
            if self._action == "check":
                self.check_done.emit(False, "", "", "", str(exc))
            else:
                self.download_done.emit(False, str(exc))

