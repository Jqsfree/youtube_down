# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Commands

```bash
# Run the app
python main.py

# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_format_selection.py -v

# Build Windows EXE (local, Linux — validation only)
pip install pyinstaller
pyinstaller --onefile --windowed --name "YouTubeDownloader" \
  --hidden-import PySide6.QtCore --hidden-import PySide6.QtWidgets \
  --hidden-import yt_dlp --hidden-import yt_dlp.extractor --hidden-import yt_dlp.downloader \
  main.py
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
  downloader.py             ← shared yt-dlp ops, validation, CSV, cookies
```

`downloader.py` / platform plugins are reusable by non-GUI consumers (CLI, web). Unit tests do not need a display server.

Theme: `theme.py` (Light/Dark/Nord + accent) via `QSettings`; applied in `main.py` with Fusion style.

## Two-Stage Cookie Strategy (batch download)

1. **Stage 1**: Download without cookies — works for 99% of public videos.
2. **Stage 2**: Retry with browser cookies — only for 3 error types: `BOT_VERIFICATION`, `AUTH_REQUIRED`, `PRIVATE_VIDEO`.

If a "copy chrome cookie" error occurs, `_cookie_broken` is set to `True` and all subsequent cookie attempts are skipped for the session.

## Error Classification

`_CATEGORY_RULES` in `downloader.py` defines 9 error categories with priority-ordered keyword matching. Each category specifies `retry_cookie` (bool). `classify_error()` returns an `ErrorCategory` dataclass. All results are written to `batch_results_*.csv` with `error_category` and `cookie_used` fields.

## Download Validation

After download, three checks run in sequence:
1. File exists and size > 1KB
2. `ffprobe` can parse duration (valid media)
3. Actual duration matches expected within 5% tolerance
4. If `min_height` is set, actual video height is verified via ffprobe

## Format Selection

`list_formats()` filters to `mp4`/`m4a` only, then deduplicates per resolution keeping Video Only > Video+Audio (higher quality). Batch mode auto-resolves format per video with progressive fallback: preferred → next best ≥ min_height → 720p → `best`.

## Batch Results

- `batch_results_*.csv` — full results with `video_id, title, status, error_category, error_message, cookie_used, output_dir`
- `skipped_videos_*.csv` — videos below resolution threshold (auto-exported)
- `failed_videos_*.csv` — failed videos for retry (auto-exported)
- Resume support: reads all prior `batch_results_*.csv` to skip already-completed video IDs

## Testing

Tests use `YoutubeDownloader.__new__()` to create instances without `__init__` (avoids browser detection / cookie setup). No Qt event loop needed for `downloader.py` tests. GUI tests require a display server.

## CI/CD

Push to `main` triggers `.github/workflows/build.yml`: smoke test (`smoke_test.py`) → PyInstaller single-file EXE → upload artifact. The smoke test validates env detection and `get_info()` on a known public video.
