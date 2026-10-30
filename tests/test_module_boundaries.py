"""Contracts for extracted logic modules and the legacy downloader facade."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module', ['cookies', 'media_probe', 'download_errors'])
def test_extracted_logic_has_no_ui_or_reverse_import(module):
    tree = ast.parse((ROOT / f'{module}.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or '']
        else:
            continue
        assert not any(name.split('.')[0] in {'PySide6', 'gui', 'worker', 'downloader'} for name in names)


def test_cookie_module_decodes_and_validates_without_downloader(tmp_path):
    import cookies
    path = tmp_path / 'cookies.txt'
    text = '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tfake\n'
    path.write_bytes(text.encode('utf-16'))
    assert cookies.read_cookie_text(path) == text
    assert cookies.validate_cookie_file(path, 'youtube')[0]


def test_error_exports_keep_identity_and_results():
    import downloader
    import download_errors
    assert downloader.ErrorCategory is download_errors.ErrorCategory
    assert downloader.classify_error is download_errors.classify_error
    for message in ['ffmpeg not found', 'Sign in to confirm your age', 'HTTP Error 429: Too Many Requests', 'opaque']:
        assert downloader.classify_error(Exception(message)) == download_errors.classify_error(Exception(message))


def test_probe_module_preserves_missing_vs_invalid(monkeypatch):
    import media_probe
    monkeypatch.setattr(media_probe.subprocess, 'run', lambda *a, **k: type('R', (), {'stdout': '', 'returncode': 1})())
    assert media_probe.run_probe([], executable='fake-ffprobe') == (True, '')
    def fail(*a, **k): raise FileNotFoundError('ffprobe')
    monkeypatch.setattr(media_probe.subprocess, 'run', fail)
    assert media_probe.run_probe([], executable='fake-ffprobe') == (False, '')


def test_duration_and_portrait_height_keep_existing_tolerances(tmp_path):
    import media_probe
    import yt_dlp
    path = tmp_path / 'video.mp4'
    media_probe.verify_duration(path, 100, probe=lambda *a, **k: (True, '105'))
    with pytest.raises(yt_dlp.utils.DownloadError, match='时长异常'):
        media_probe.verify_duration(path, 100, probe=lambda *a, **k: (True, '106'))
    media_probe.verify_height(path, min_height=720, expected_height=None, probe=lambda *a, **k: (True, '720,1280'))
    with pytest.raises(yt_dlp.utils.DownloadError, match='目标档位'):
        media_probe.verify_height(path, min_height=0, expected_height=1080, probe=lambda *a, **k: (True, '720,1280'))


def test_file_size_threshold_is_unchanged(tmp_path):
    import media_probe
    import yt_dlp
    path = tmp_path / 'video.mp4'
    path.write_bytes(b'x' * 1024)
    with pytest.raises(yt_dlp.utils.DownloadError, match='文件过小'):
        media_probe.verify_file_size(path)
    path.write_bytes(b'x' * 1025)
    media_probe.verify_file_size(path)


def test_extracted_modules_are_in_package_manifest():
    import tomllib
    names = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['tool']['setuptools']['py-modules']
    assert {'cookies', 'media_probe', 'download_errors', 'batch_runner'} <= set(names)


def test_legacy_probe_lookup_failure_remains_unavailable(monkeypatch):
    import downloader
    def fail(name): raise OSError('tool lookup unavailable')
    monkeypatch.setattr(downloader, '_find_tool', fail)
    assert downloader._run_probe(['-version']) == (False, '')


@pytest.mark.parametrize('module', ['cookies', 'media_probe', 'download_errors'])
def test_logic_imports_independently_in_a_fresh_process(module):
    import subprocess
    import sys
    subprocess.run([
        sys.executable, '-c',
        "import importlib,sys;importlib.import_module(sys.argv[1]);assert 'downloader' not in sys.modules;assert not any(n.startswith('PySide6') for n in sys.modules)",
        module,
    ], cwd=ROOT, check=True, capture_output=True)
