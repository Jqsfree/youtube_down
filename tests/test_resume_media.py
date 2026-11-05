"""Resume must reject truncated, audio-only and invalid existing media."""
import json

import pytest

from media_probe import valid_resume_media


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"x" * 4096)
    return path


def fake_probe(payload):
    return lambda *_a, **_kw: (True, json.dumps(payload))


def test_valid_resume_media_requires_duration_and_video_stream(media):
    good = {
        "format": {"duration": "16.1"},
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
    }
    assert valid_resume_media(media, min_height=720, probe=fake_probe(good))
    assert not valid_resume_media(media, min_height=1440, probe=fake_probe(good))
    assert not valid_resume_media(media, probe=fake_probe({
        "format": {"duration": "0"}, "streams": good["streams"],
    }))
    assert not valid_resume_media(media, probe=fake_probe({
        "format": {"duration": "NaN"}, "streams": good["streams"],
    }))
    assert not valid_resume_media(media, probe=fake_probe({
        "format": {"duration": "16"}, "streams": [{"codec_type": "audio"}],
    }))


def test_valid_resume_audio_only_requires_audio(media):
    streams = [{"codec_type": "audio"}]
    assert valid_resume_media(
        media, audio_only=True,
        probe=fake_probe({"format": {"duration": "10"}, "streams": streams}),
    )
    assert not valid_resume_media(
        media, audio_only=False,
        probe=fake_probe({"format": {"duration": "10"}, "streams": streams}),
    )


def test_valid_resume_fails_closed_for_invalid_json_and_missing_ffprobe(media):
    assert not valid_resume_media(media, probe=lambda *a, **k: (False, ""))
    assert not valid_resume_media(media, probe=lambda *a, **k: (True, "garbage"))
    assert not valid_resume_media(media, probe=fake_probe({}))
    assert not valid_resume_media(media.with_name("missing.mp4"), probe=fake_probe({}))


def test_valid_resume_rejects_tiny_files(tmp_path):
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"x" * 10)
    assert not valid_resume_media(tiny, probe=fake_probe({
        "format": {"duration": "10"},
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
    }))
