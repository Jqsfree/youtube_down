"""Validate the opt-in live E2E harness without connecting to YouTube."""
from __future__ import annotations

from pathlib import Path

import pytest

from batch_io import OverviewStore
from scripts import real_download_smoke


def test_live_harness_requires_valid_media_and_resumes(tmp_path, monkeypatch):
    runs = []
    media = tmp_path / "paste" / "demo.mp4"

    def fake_cli(args):
        runs.append(args)
        if len(runs) == 1:
            media.parent.mkdir(parents=True)
            media.write_bytes(b"media" * 1000)
            OverviewStore(tmp_path).upsert(
                source="paste", row_no=1, media_id="demo",
                status="success", output_file=media,
            )
        return 0

    monkeypatch.setattr(real_download_smoke, "cli_main", fake_cli)
    monkeypatch.setattr(real_download_smoke, "run_probe", lambda *_a, **_k: (True, "12.0"))
    real_download_smoke.verify("https://example.com/demo.mp4", tmp_path)
    assert len(runs) == 2


def test_live_harness_rejects_invalid_media(tmp_path, monkeypatch):
    media = tmp_path / "paste" / "demo.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"media" * 1000)
    OverviewStore(tmp_path).upsert(
        source="paste", row_no=1, media_id="demo",
        status="success", output_file=media,
    )
    monkeypatch.setattr(real_download_smoke, "cli_main", lambda *_a: 0)
    monkeypatch.setattr(real_download_smoke, "run_probe", lambda *_a, **_k: (False, ""))
    with pytest.raises(AssertionError, match="Invalid media"):
        real_download_smoke.verify("https://example.com/demo.mp4", tmp_path)
