"""Install verified build tools; verify the copies actually embedded by PyInstaller."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def extract_tools(data: bytes, spec: dict, destination: Path) -> None:
    if hashlib.sha256(data).hexdigest() != spec['sha256']:
        raise ValueError('tool archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in spec['binaries']:
            members = [item for item in archive.namelist() if item.split('/')[-1] == name]
            if len(members) != 1:
                raise ValueError(f'missing or duplicate binary: {name}')
            # Extract only the specified basename, never paths supplied by the archive.
            target = destination / name
            target.write_bytes(archive.read(members[0]))
            target.chmod(0o755)


def output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, encoding='utf-8', errors='replace').strip()


def check_version(name: str, text: str, expected: str) -> str:
    line = text.splitlines()[0]
    parts = line.split()
    index = 1 if name == 'deno' else 2
    if len(parts) <= index or not (parts[index] == expected or parts[index].startswith(expected + '-')):
        raise ValueError(f'{name} version drift: {line}')
    return line


def install(system: str, specs: dict) -> None:
    if system == 'macOS' and platform.machine().lower() not in {'arm64', 'aarch64'}:
        raise ValueError('macOS release requires Apple Silicon')
    for name, spec in specs.items():
        if spec.get('provider') == 'homebrew':
            subprocess.run(['brew', 'install', 'ffmpeg'], check=True)
            actual = output(['brew', 'list', '--versions', 'ffmpeg']).split()[1:]
            if actual != [spec['formula_version']]:
                raise ValueError(f'Homebrew ffmpeg version drift: {actual}')
            prefix = output(['brew', '--prefix', 'ffmpeg'])
            for binary in ('ffmpeg', 'ffprobe'):
                shutil.copy2(Path(prefix) / 'bin' / binary, ROOT / binary)
            (ROOT / 'brew-build-info.json').write_text(output(['brew', 'info', '--json=v2', '--installed']), encoding='utf-8')
        else:
            with urllib.request.urlopen(spec['url'], timeout=120) as response:
                extract_tools(response.read(), spec, ROOT)
    # Validate downloaded executables now, separately from bundled-copy checks below.
    for name in ('ffmpeg', 'ffprobe', 'deno'):
        binary = ROOT / (name + ('.exe' if system == 'Windows' else ''))
        version = output([str(binary), '--version' if name == 'deno' else '-version'])
        expected = specs['deno' if name == 'deno' else 'ffmpeg']['version']
        check_version(name, version, expected)


def verify(system: str, bundle: Path, asset: Path, specs: dict) -> None:
    tools = {}
    if system == 'macOS':
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    with tempfile.TemporaryDirectory(prefix='ytdown-bundle-check-') as folder:
        for name in ('ffmpeg', 'ffprobe', 'deno'):
            filename = name + ('.exe' if system == 'Windows' else '')
            if system == 'Windows':
                from PyInstaller.archive.readers import CArchiveReader
                archive = CArchiveReader(str(bundle))
                names = [entry for entry in archive.toc if Path(entry).name == filename]
                if len(names) != 1:
                    raise ValueError(f'missing embedded binary: {filename}')
                binary = Path(folder) / filename
                binary.write_bytes(archive.extract(names[0]))
            else:
                matches = {path.resolve() for path in bundle.rglob(filename) if path.is_file()}
                if len(matches) != 1:
                    raise ValueError(f'missing or duplicate bundled binary: {filename}')
                binary = matches.pop()
                if not binary.is_relative_to(bundle.resolve()):
                    raise ValueError('bundled tool points outside app')
            # PyInstaller rewrites Mach-O dependencies and signs the bundled copy.
            if system == 'Windows' and digest(binary) != digest(ROOT / filename):
                raise ValueError(f'bundled tool differs: {filename}')
            version = check_version(name, output([str(binary), '--version' if name == 'deno' else '-version']),
                                    specs['deno' if name == 'deno' else 'ffmpeg']['version'])
            tools[name] = {'sha256': digest(binary), 'source_sha256': digest(ROOT / filename), 'version': version}
    record = {
        'version': (ROOT / 'VERSION').read_text(encoding="utf-8").strip(), 'platform': system,
        'commit': output(['git', 'rev-parse', 'HEAD']), 'uv_lock_sha256': digest(ROOT / 'uv.lock'),
        'tool_sources': specs, 'tools': tools, 'asset_sha256': digest(asset),
        'python_packages': {item.metadata['Name']: item.version for item in importlib.metadata.distributions()},
    }
    if system == 'macOS':
        record['homebrew'] = json.loads((ROOT / 'brew-build-info.json').read_text(encoding="utf-8"))
    (asset.parent / f'build-info-{system}.json').write_text(json.dumps(record, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['install', 'verify'])
    parser.add_argument('--platform', choices=['Windows', 'macOS'], required=True)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--asset', type=Path)
    args = parser.parse_args()
    specs = json.loads((ROOT / 'scripts/build-tools.json').read_text(encoding="utf-8"))[args.platform]
    if args.command == 'install':
        install(args.platform, specs)
    else:
        if args.bundle is None or args.asset is None:
            parser.error('verify requires --bundle and --asset')
        verify(args.platform, args.bundle, args.asset, specs)
