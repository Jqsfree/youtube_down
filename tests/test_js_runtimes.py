"""Tests for JS runtime configuration in yt-dlp opts."""

from __future__ import annotations

from unittest.mock import patch

import downloader


def test_configure_js_runtimes_adds_deno_path() -> None:
    opts: dict = {}
    with patch.object(downloader, "_resolve_tool", side_effect=lambda n: f"/bin/{n}" if n == "deno" else None):
        downloader._configure_js_runtimes(opts)
    assert opts["js_runtimes"]["deno"]["path"] == "/bin/deno"
