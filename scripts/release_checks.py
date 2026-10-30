"""Offline release validation and a fail-closed GitHub release preflight."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tomllib
import urllib.request

ASSETS = {'Windows': 'YouTubeDownloader-Windows.exe', 'macOS': 'YouTubeDownloader-macOS.zip'}


def validate_version(root: Path, tag: str = '') -> str:
    version = (root / 'VERSION').read_text(encoding="utf-8").strip()
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding="utf-8"))['project']['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or version != project:
        raise ValueError('VERSION 与 pyproject 版本不一致或格式错误')
    if tag and tag != f'v{version}':
        raise ValueError('tag 与 VERSION 不一致')
    return version


def ensure_absent(repository: str, tag: str, token: str) -> None:
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository):
        raise ValueError('invalid repository')
    # List also includes authenticated drafts; API/network failures must stop release.
    for page in range(1, 1000):
        req = urllib.request.Request(
            f'https://api.github.com/repos/{repository}/releases?per_page=100&page={page}',
            headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            releases = json.load(response)
        if not isinstance(releases, list):
            raise ValueError('invalid releases response')
        if any(item['tag_name'] == tag for item in releases):
            raise ValueError(f'版本 {tag} 已存在，禁止修改或覆盖资产')
        if len(releases) < 100:
            return
    raise ValueError('release pagination limit exceeded')


def stage_assets(inputs: Path, destination: Path, version: str) -> None:
    verified = []
    for platform, name in ASSETS.items():
        matches = list(inputs.rglob(name))
        if len(matches) != 1:
            raise ValueError(f'资产缺失或重复：{name}')
        asset = matches[0]
        lines = (asset.parent / 'SHA256SUMS.txt').read_text(encoding="utf-8").splitlines()
        expected = [line.split() for line in lines]
        if len(expected) != 1 or len(expected[0]) != 2 or expected[0][1] != name:
            raise ValueError(f'校验清单不完整：{name}')
        with asset.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != expected[0][0]:
            raise ValueError(f'资产摘要不符：{name}')
        info = asset.parent / f'build-info-{platform}.json'
        data = json.loads(info.read_text(encoding="utf-8"))
        if data.get('asset_sha256') != digest:
            raise ValueError('构建记录资产摘要不符')
        if data['version'] != version or data['platform'] != platform:
            raise ValueError('构建记录版本或平台不符')
        verified.append((asset, info, digest))
    destination.mkdir(parents=True, exist_ok=False)
    checksums = []
    for asset, info, digest in verified:
        shutil.copy2(asset, destination / asset.name)
        shutil.copy2(info, destination / info.name)
        checksums.append(f'{digest}  {asset.name}\n')
    (destination / 'SHA256SUMS.txt').write_text(''.join(checksums))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['version', 'ensure-absent', 'stage', 'notes'])
    parser.add_argument('--tag', default='')
    args = parser.parse_args()
    root = Path.cwd()
    version = validate_version(root, args.tag)
    if args.command == 'version':
        print(version)
    elif args.command == 'ensure-absent':
        if not args.tag:
            raise ValueError('release tag required')
        ensure_absent(os.environ['GITHUB_REPOSITORY'], args.tag, os.environ['GH_TOKEN'])
    elif args.command == 'stage':
        stage_assets(root / 'release-assets', root / 'staged', version)
    else:
        lines = (root / 'CHANGELOG.md').read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith(f'## [{version}]'))
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## [')), len(lines))
        body = '\n'.join(lines[start:end]).strip()
        body += '\n\nWindows x64: YouTubeDownloader-Windows.exe\n\nmacOS Apple Silicon: YouTubeDownloader-macOS.zip\n\n校验：SHA256SUMS.txt；构建来源：build-info-*.json。\n'
        (root / 'staged/RELEASE_NOTES.md').write_text(body, encoding='utf-8')


if __name__ == '__main__':
    main()
