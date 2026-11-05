"""发布一致性：VERSION / pyproject / README / CHANGELOG 必须对得上。

版本号不一致的后果很实际：
- `VERSION` 与 tag 不一致 → 应用内更新永远提示「有新版」或者永远收不到更新。
- README 里的下载链接指向旧 tag → 用户下到旧包。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", _version()), f"VERSION 不是 x.y.z: {_version()}"


def test_pyproject_matches_version_file() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml 里找不到 version"
    assert match.group(1) == _version(), "pyproject.toml 与 VERSION 不一致"


def test_changelog_has_current_version_section() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{_version()}]" in text, f"CHANGELOG.md 缺少 {_version()} 条目"


def test_readme_links_point_to_one_not_future_release() -> None:
    """The source version can lead the latest *published* install package.

    Pointing install links at a not-yet-built release is a broken download.
    During release preparation the README must explicitly disclose this gap.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    tags = set(re.findall(r"/releases/(?:tag|download)/v(\d+\.\d+\.\d+)", text))
    assert len(tags) == 1, f"README 下载链接版本不唯一: {sorted(tags)}"
    linked = tags.pop()
    current = _version()
    parts = lambda value: tuple(int(p) for p in value.split("."))
    assert parts(linked) <= parts(current), f"README 包版本不能超前源码: {linked} > {current}"
    if linked != current:
        assert f"v{linked}" in text and "最新**已发布安装包**" in text
        assert f"**v{current}**" in text and "尚未" in text, (
            "源码版本超前于发布包时，README 必须明确披露未发布状态"
        )


@pytest.mark.parametrize("name", ["YouTubeDownloader-Windows.exe", "YouTubeDownloader-macOS.zip"])
def test_readme_lists_each_asset(name: str) -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert name in text, f"README 没有列出 {name}"


def test_build_workflow_asset_names_match_updater() -> None:
    """build.yml 的产物名必须与 app_updater 里的常量一致，否则更新找不到包。"""
    import app_updater

    text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert app_updater.ASSET_WINDOWS in text
    assert app_updater.ASSET_MACOS in text


def test_build_workflow_publishes_checksums() -> None:
    import app_updater

    text = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert app_updater.ASSET_CHECKSUMS in text, "CI 没有生成校验清单"


def test_claude_md_points_to_agents_md() -> None:
    """agent 指南只维护一份，CLAUDE.md 不能重新长出一份副本。"""
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in text
    assert len(text.splitlines()) < 20, "CLAUDE.md 又变成完整副本了"
