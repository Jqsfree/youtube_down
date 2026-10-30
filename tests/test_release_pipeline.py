from pathlib import Path
import hashlib
import json

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def test_ordinary_commits_cannot_publish():
    ci = workflow('ci.yml')
    assert 'pull_request' in ci['on'] and 'main' in ci['on']['push']['branches']
    assert ci['permissions'] == {'contents': 'read'}
    assert all('smoke_test.py' not in str(job) for job in ci['jobs'].values())
    build = workflow('build.yml')
    assert 'branches' not in build['on']['push']
    assert build['on']['push']['tags'] == ['v*']
    assert build['on']['workflow_dispatch']['inputs']['create_draft']['default'] == 'false'
    assert build['permissions'] == {'contents': 'read'}
    assert build['jobs']['release']['permissions'] == {'contents': 'write'}
    assert 'create_draft' in build['jobs']['release']['if']
    assert 'smoke_test.py' not in str(build['jobs'])


def test_network_smoke_is_separate():
    smoke = workflow('smoke.yml')
    assert set(smoke['on']) == {'schedule', 'workflow_dispatch'}
    assert 'smoke_test.py' in str(smoke['jobs'])
    assert smoke['permissions'] == {'contents': 'read'}


def test_release_never_updates_existing_assets():
    build = workflow('build.yml')
    release = str(build['jobs']['release'])
    assert 'ensure-absent' in release
    assert 'gh release create' in release and '--draft' in release and '--verify-tag' in release
    assert '--clobber' not in release and 'overwrite_files' not in release
    assert 'uv sync --locked --group build' in str(build['jobs']['build'])
    assert 'pip install -U' not in str(build)


@pytest.mark.parametrize('tag', ['v9.9.9', '1.3.1', 'v1.3.1;echo bad'])
def test_wrong_tag_is_rejected(tmp_path, tag):
    from scripts.release_checks import validate_version
    (tmp_path / 'VERSION').write_text('1.3.1')
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion="1.3.1"\n')
    with pytest.raises(ValueError):
        validate_version(tmp_path, tag)


def test_version_mismatch_rejected(tmp_path):
    from scripts.release_checks import validate_version
    (tmp_path / 'VERSION').write_text('1.3.1')
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion="0.1.0"\n')
    with pytest.raises(ValueError):
        validate_version(tmp_path)


def test_release_stage_checks_both_payloads(tmp_path):
    from scripts.release_checks import stage_assets
    for platform, name in [('Windows', 'YouTubeDownloader-Windows.exe'), ('macOS', 'YouTubeDownloader-macOS.zip')]:
        folder = tmp_path / 'input' / platform
        folder.mkdir(parents=True)
        (folder / name).write_bytes(platform.encode())
        digest = hashlib.sha256(platform.encode()).hexdigest()
        (folder / 'SHA256SUMS.txt').write_text(f'{digest}  {name}\n')
        (folder / f'build-info-{platform}.json').write_text(json.dumps({'platform': platform, 'version': '1.3.1', 'asset_sha256': digest}))
    stage_assets(tmp_path / 'input', tmp_path / 'staged', '1.3.1')
    assert len((tmp_path / 'staged/SHA256SUMS.txt').read_text().splitlines()) == 2
    (tmp_path / 'input/Windows/YouTubeDownloader-Windows.exe').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='摘要'):
        stage_assets(tmp_path / 'input', tmp_path / 'bad', '1.3.1')


def test_existing_release_refused_and_api_failure_not_ignored(monkeypatch):
    from scripts import release_checks
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'[{"tag_name":"v1.3.1","draft":true}]'
    monkeypatch.setattr(release_checks.urllib.request, 'urlopen', lambda *a, **k: Response())
    with pytest.raises(ValueError, match='已存在'):
        release_checks.ensure_absent('owner/repo', 'v1.3.1', 'fake-token')
    def fail(*a, **k): raise OSError('offline')
    monkeypatch.setattr(release_checks.urllib.request, 'urlopen', fail)
    with pytest.raises(OSError):
        release_checks.ensure_absent('owner/repo', 'v1.3.1', 'fake-token')


def test_tool_archive_checksum_checked_before_extraction(tmp_path):
    from scripts.build_tools import extract_tools
    with pytest.raises(ValueError, match='checksum'):
        extract_tools(b'bad archive', {'sha256': '0' * 64, 'binaries': ['ffmpeg.exe']}, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_tool_extraction_ignores_archive_paths(tmp_path):
    import io
    import zipfile
    from scripts.build_tools import extract_tools
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('../../outside/ffmpeg.exe', b'fake binary')
        archive.writestr('../../other.txt', b'ignored')
    data = buffer.getvalue()
    extract_tools(data, {'sha256': hashlib.sha256(data).hexdigest(), 'binaries': ['ffmpeg.exe']}, tmp_path)
    assert (tmp_path / 'ffmpeg.exe').read_bytes() == b'fake binary'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['ffmpeg.exe']


def test_macos_packaging_may_rewrite_binary_but_must_keep_version(tmp_path, monkeypatch):
    from scripts import build_tools
    monkeypatch.setattr(build_tools, 'ROOT', tmp_path)
    (tmp_path / 'VERSION').write_text('1.3.1')
    (tmp_path / 'uv.lock').write_text('locked')
    (tmp_path / 'brew-build-info.json').write_text('{}')
    asset = tmp_path / 'app.zip'
    asset.write_bytes(b'packaged app')
    bundle = tmp_path / 'app.app'
    bundle.mkdir()
    for name in ('ffmpeg', 'ffprobe', 'deno'):
        (tmp_path / name).write_bytes(b'source Mach-O')
        (bundle / name).write_bytes(b'rewritten and signed Mach-O')
    monkeypatch.setattr(build_tools.subprocess, 'run', lambda *a, **k: None)
    def version(command):
        name = Path(command[0]).name
        return 'abc123' if name == 'git' else ('deno 2.9.8' if name == 'deno' else f'{name} version 9.0.2')
    monkeypatch.setattr(build_tools, 'output', version)
    specs = {'ffmpeg': {'version': '9.0.2'}, 'deno': {'version': '2.9.8'}}
    build_tools.verify('macOS', bundle, asset, specs)
    data = json.loads((tmp_path / 'build-info-macOS.json').read_text())
    assert data['tools']['ffmpeg']['sha256'] != data['tools']['ffmpeg']['source_sha256']
    monkeypatch.setattr(build_tools, 'output', lambda c: 'deno 0.0.0' if Path(c[0]).name == 'deno' else version(c))
    with pytest.raises(ValueError, match='version'):
        build_tools.verify('macOS', bundle, asset, specs)
