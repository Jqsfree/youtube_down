# AGENTS.md

Single source of truth for agent guidance in this repository. `CLAUDE.md` only
points here — keep project docs in this file, not in per-agent copies.

## Commands

```bash
# Install locked development/build dependencies
uv sync --locked --group build

# Run the app
python main.py

# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_format_selection.py -v

# Build Windows EXE (local, Linux — validation only)
uv sync --locked --group build
uv run --no-sync pyinstaller --onefile --windowed --name "YouTubeDownloader" \
  --icon assets/icon.ico --add-data "assets:assets" --add-data "VERSION:." \
  --hidden-import PySide6.QtCore --hidden-import PySide6.QtWidgets \
  --hidden-import yt_dlp --hidden-import yt_dlp.extractor --hidden-import yt_dlp.downloader \
  main.py

# Regenerate icon assets (png / ico / icns)
python scripts/make_icon.py
```

## Architecture

**Strict layer separation** — enforced, not just convention:

```
gui.py + theme.py     ← Pure UI / QSS. Must NOT import yt_dlp.
    ↓ calls
worker.py             ← QThread subclasses, Qt Signals connect both sides.
    ↓ calls
platforms/ + downloader.py  ← Pure logic. Must NOT import PySide6.
  platforms/base.py         ← Platform enum, MediaInput, ABC
  platforms/{youtube,bilibili,tiktok}.py
  platforms/facade.py       ← routes by platform (YoutubeDownloader alias)
  downloader.py             ← download orchestration, CSV, legacy facade
  cookies.py                ← cookie copies, browser discovery, cookie file/config IO
  media_probe.py            ← tool lookup, ffprobe, file/duration/height validation
  download_errors.py        ← error categories and classification
  batch_io.py               ← input discovery, output mirroring, 下载总览.csv
  app_updater.py            ← GitHub Release check / download / self-replace
  yt_dlp_updater.py         ← yt-dlp overlay hot update (PyPI)
```

`downloader.py` / platform plugins are reusable by non-GUI consumers (CLI, web). Unit tests do not need a display server.

Theme: `theme.py` (Light/Dark/Nord + accent) via `QSettings`; applied in `main.py` with Fusion style.

## Two-Stage Cookie Strategy (batch download)

1. **Stage 1**: Download without cookies — works for 99% of public videos.
2. **Stage 2**: Retry with browser cookies — only for 3 error types: `BOT_VERIFICATION`, `AUTH_REQUIRED`, `PRIVATE_VIDEO`.

If a "copy chrome cookie" error occurs, `_cookie_broken` is set to `True` and all subsequent cookie attempts are skipped for the session.

Browser access is opt-in (GUI reload action or explicit CLI option). Cookie files passed to yt-dlp are per-operation, platform-filtered private copies; never overwrite or delete the imported original. `private_files.py` owns private directory permissions (POSIX 0700; Windows current-user ACL).

## Error Classification

`_CATEGORY_RULES` in `download_errors.py` (re-exported by `downloader.py`) defines 8 priority-ordered error categories with keyword matching: `CANCELLED`, `ENVIRONMENT` (missing ffprobe), `BOT_VERIFICATION`, `AUTH_REQUIRED`, `PRIVATE_VIDEO`, `RATE_LIMIT`, `VIDEO_UNAVAILABLE`, `NETWORK_ERROR`. Each category specifies `retry_cookie` (bool). `classify_error()` returns an `ErrorCategory` dataclass. All results are written to the output root's `下载总览.csv` (`错误类型` column).

## Download Validation

After download, four checks run in sequence:
1. File exists and size > 1KB
2. `ffprobe` can parse duration (valid media) — ffprobe is a fatal dependency, missing it raises `ENVIRONMENT`
3. Actual duration matches expected within 5% tolerance
4. If `min_height` is set, actual video height is verified via ffprobe

Audio-only mode (`audio_only=True`) replaces the video-track check with an audio-track check.

## Download Options (yt-dlp built-ins)

UI/CLI extras are passed through `YoutubeDownloader.set_ydl_opt_overrides(...)`:
subtitles (`FFmpegEmbedSubtitle`), metadata (`FFmpegMetadata`), thumbnail
(`EmbedThumbnail`), `proxy`, `ratelimit`. Postprocessor lists are merged with the
download-time audio converter rather than replaced. Playlist/channel URLs expand
one level via `expand_playlist` (`extract_flat`) before entering the queue.
`impersonate` (curl-cffi) applies to extraction only, never to downloads.

## Format Selection

`list_formats()` filters to `mp4`/`m4a` only, then deduplicates per resolution keeping Video Only > Video+Audio (higher quality). Batch mode auto-resolves format per video with progressive fallback: preferred → next best ≥ min_height → 720p → `best`.

## Batch Results

- Inputs: table files (`.csv/.tsv/.txt`), whole folders (recursive, via `batch_io.discover_inputs`), or pasted links (newline/comma/space separated, `parse_pasted_text`).
- Output mirrors input: `<out>/<input rel path without ext>/<media_id>.mp4`; pasted links go to `<out>/paste/`. Row number = data row in the original table (`_row`) and only appears in the overview.
- One overview per output root: `下载总览.csv` (utf-8-sig) with `来源文件, 行号, video_id, 平台, 链接, 标题, 状态, 错误类型, 错误信息, 输出文件, 更新时间`; keyed by (来源文件, video_id), upserted atomically. No per-batch `batch_results_* / failed_* / skipped_*` files are created anymore.
- Resume: overview rows with 状态=成功 whose file still exists, valid files named `{id}.ext` or `NNNN_{id}.ext`, and legacy `batch_results_*.csv` (read-only).

## Testing

Tests use `YoutubeDownloader.__new__()` to create instances without `__init__` (avoids browser detection / cookie setup). No Qt event loop needed for `downloader.py` tests. GUI tests require a display server.

## Live Download Queue

`BatchDownloadWorker` is a live queue, not a fixed list:

- `enable_live_queue()` (called by the GUI before `start()`) makes `run()` keep waiting for new items after the initial list drains.
- `enqueue(video_ids, row_numbers=None, cookie_overrides=None)` appends thread-safely and returns `False` once the worker has finished or was cancelled — the GUI then falls back to the next `_csv_queue` group.
- `close_queue()` ends the wait immediately (cancel path). Idle exit defaults to 3s (`_idle_exit_seconds`).
- `total_changed(int)` reports the new denominator so the progress bar grows instead of jumping.
- `_queue_open == False` keeps the legacy behavior, which is what direct `_run_sequential` / `_run_brutal_parallel` calls in tests rely on.
- GUI keeps「加入队列」/「粘贴」enabled while downloading; new IDs go to `_enqueue_live()` first.

## App Self-Update (GitHub Releases)

`app_updater.py` (no PySide6, no yt_dlp):

- `check_for_app_update()` compares `VERSION` with the latest GitHub release; prereleases are skipped.
- `download_release_asset()` streams to a temp dir and verifies the byte count against the API-reported size. `download_update()` requires a valid SHA256 entry before downloading the payload; missing/conflicting entries refuse the update.
- `install_and_restart()` writes a platform script (Windows `.ps1` retry-loop rename with JSON path data, macOS `.sh` ditto + `xattr -dr`) and spawns it detached, then the GUI quits.
- `cleanup_old()` removes leftover `*.old` on startup (`main.py`).
- Source-tree runs never self-replace — they open the Releases page instead.
- Asset names must stay in sync with `.github/workflows/build.yml`: `YouTubeDownloader-Windows.exe`, `YouTubeDownloader-macOS.zip`.
- GUI: 「设置 → 检查更新」button plus a silent 24h check (`QSettings` key `update/last_check`).

## Icons

- Source of truth: `assets/icon.svg`; committed outputs `icon.png` (1024), `icon.ico` (7 sizes), `icon.icns`.
- Regenerate with `python scripts/make_icon.py` (needs PySide6; `icns` only builds on macOS). `--check` validates existing assets.
- `theme.asset_path()` resolves `sys._MEIPASS/assets` for frozen builds; `theme.app_icon()` is applied in `main.py` and `MainWindow`.
- `build.yml` passes `--icon` and `--add-data "assets"` on both platforms.

## CI/CD

PR/main commits run `.github/workflows/ci.yml`: locked dependencies, generated-requirements consistency and offline tests. Network smoke is isolated in `smoke.yml` (daily/manual). `build.yml` runs for `v*` tags or manual builds: validate tag/VERSION/pyproject, locked tests/build, pinned tools, verify embedded tools, upload artifacts and build-info. Only the release job has write permission; tags create a new draft release, manual builds create no release unless `create_draft` is explicitly selected with an existing matching tag. Existing releases/drafts are never overwritten. Actual cross-platform upgrade checks are required before a maintainer publishes a draft. See BUILD.md for dependency/tool updates and release recovery.

## Logging

`logger_utils.AppLogger` owns redaction and rotating file handlers (2 MiB, 3 backups). GUI logs to Downloads/youtube_downloader.log; CLI logs under results-dir/output. `_EngineLogger` records local task IDs, platform, phases and error categories without raw engine headers/responses. Debug events are file-only. GUI/CLI messages use the same credential redaction policy; do not bypass it for new log sinks. Logging failures must not fail downloads. Existing overview CSV semantics remain unchanged.

## Extracted logic compatibility

Keep downloader function exports and YoutubeDownloader public methods as compatibility entry points. Cookie and media helpers are delegated to cookies.py and media_probe.py; ErrorCategory/classification exports refer to the same objects in download_errors.py. These modules must not import PySide6, gui, worker or downloader. Tool/config compatibility wrappers pass the existing configuration paths and probe functions; preserve missing-tool versus invalid-media outcomes and current size/duration/height tolerances. Register new runtime modules in pyproject.toml and verify both frozen builds.
