"""应用自更新：版本比较、Release 解析、资产选择、URL 白名单与安装分支。"""

from __future__ import annotations

import hashlib
import urllib.error
import os
from pathlib import Path

import pytest

import app_updater


def _release_payload(version: str = "v1.3.0", assets: list[dict] | None = None) -> dict:
    return {
        "tag_name": version,
        "html_url": f"https://github.com/Jqsfree/youtube_down/releases/tag/{version}",
        "body": "- 修复若干问题\n- 新增活动队列",
        "assets": assets
        if assets is not None
        else [
            {
                "name": app_updater.ASSET_WINDOWS,
                "browser_download_url": "https://github.com/x/y/releases/download/v1.3.0/a.exe",
                "size": 123,
            },
            {
                "name": app_updater.ASSET_MACOS,
                "browser_download_url": "https://github.com/x/y/releases/download/v1.3.0/a.zip",
                "size": 456,
            },
        ],
    }


def test_parse_release_strips_v_prefix() -> None:
    release = app_updater.parse_release(_release_payload())
    assert release.version == "1.3.0"
    assert release.tag == "v1.3.0"
    assert len(release.assets) == 2
    assert "活动队列" in release.notes


def test_parse_release_without_assets() -> None:
    release = app_updater.parse_release({"tag_name": "1.4.0"})
    assert release.version == "1.4.0"
    assert release.assets == []
    assert release.page_url == app_updater.RELEASES_PAGE


def test_asset_for_platform() -> None:
    release = app_updater.parse_release(_release_payload())
    win = release.asset_for("win32")
    mac = release.asset_for("darwin")
    assert win is not None and win.name == app_updater.ASSET_WINDOWS
    assert win.size == 123
    assert mac is not None and mac.name == app_updater.ASSET_MACOS
    assert release.asset_for("linux") is None


def test_check_reports_newer_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app_updater, "fetch_latest_release",
        lambda timeout=20: app_updater.parse_release(_release_payload("v9.9.9")),
    )
    result = app_updater.check_for_app_update(current="1.2.0")
    assert result.available is True
    assert result.latest == "9.9.9"
    assert result.error == ""


def test_check_reports_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app_updater, "fetch_latest_release",
        lambda timeout=20: app_updater.parse_release(_release_payload("v1.2.0")),
    )
    result = app_updater.check_for_app_update(current="1.2.0")
    assert result.available is False
    assert result.latest == "1.2.0"


def test_check_skips_prerelease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app_updater, "fetch_latest_release",
        lambda timeout=20: app_updater.parse_release(_release_payload("v2.0.0-beta")),
    )
    result = app_updater.check_for_app_update(current="1.2.0")
    assert result.available is False


def test_check_surfaces_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(timeout: float = 20):
        raise OSError("no network")

    monkeypatch.setattr(app_updater, "fetch_latest_release", boom)
    result = app_updater.check_for_app_update(current="1.2.0")
    assert result.available is False
    assert "no network" in result.error


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/x",
        "https://evil.example.com/a.exe",
        "https://api.github.com.evil.com/a",
    ],
)
def test_assert_allowed_url_rejects(url: str) -> None:
    with pytest.raises(ValueError):
        app_updater.assert_allowed_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repos/a/b/releases/latest",
        "https://github.com/a/b/releases/download/v1/a.exe",
        "https://objects.githubusercontent.com/foo",
        # release 资产的真实落点（实测 v1.2.0 下载跳到这里）
        "https://release-assets.githubusercontent.com/github-production-release-asset/x",
        "https://github-releases.githubusercontent.com/foo",
    ],
)
def test_assert_allowed_url_accepts(url: str) -> None:
    app_updater.assert_allowed_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://evilusercontent.com/a.exe",
        "https://githubusercontent.com.evil.com/a",
        "https://notgithubusercontent.com/a",
    ],
)
def test_assert_allowed_url_rejects_fake_cdn(url: str) -> None:
    with pytest.raises(ValueError, match="disallowed host"):
        app_updater.assert_allowed_url(url)


def test_download_release_asset_checks_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        headers = {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n: int = -1) -> bytes:
            return b"" if getattr(self, "_done", False) else self._mark()

        def _mark(self) -> bytes:
            self._done = True
            return b"abcd"

    monkeypatch.setattr(app_updater, "_urlopen", lambda url, timeout=60: _Resp())
    asset = app_updater.Asset("a.exe", "https://github.com/a/b", size=4)
    seen: list[int] = []
    path = app_updater.download_release_asset(asset, tmp_path, progress=seen.append)
    assert path.read_bytes() == b"abcd"
    assert seen[-1] == 100

    bad = app_updater.Asset("b.exe", "https://github.com/a/b", size=999)
    with pytest.raises(ValueError):
        app_updater.download_release_asset(bad, tmp_path)


def test_install_and_restart_on_source_tree(tmp_path: Path) -> None:
    """未打包时只提示手动下载，不写脚本。"""
    payload = tmp_path / "a.zip"
    payload.write_bytes(b"x")
    message = app_updater.install_and_restart(payload)
    assert "手动下载" in message
    assert app_updater.RELEASES_PAGE in message


def test_install_and_restart_without_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_updater, "is_frozen", lambda: True)
    monkeypatch.setattr(app_updater, "install_root", lambda: tmp_path / "app.exe")
    assert "安装包缺失" in app_updater.install_and_restart(None)


def test_windows_script_paths_are_data(tmp_path):
    import json
    payload = tmp_path / "new %PATH% ! & ' 中.exe"
    target = tmp_path / "app %PATH% ! & ' 中.exe"
    script = app_updater._write_windows_script(payload, target)
    text = script.read_text(encoding="utf-8-sig")
    assert script.suffix == ".ps1"
    assert str(payload) not in text
    assert str(target) not in text
    data = json.loads(script.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["payload"] == str(payload)
    assert data["target"] == str(target)
    assert "-LiteralPath" in text
    assert "if ($moved)" in text
    assert "Move-Item -LiteralPath $old -Destination $target" in text


def test_macos_script_rolls_back_on_failed_copy(tmp_path: Path) -> None:
    """覆盖失败必须把旧 .app 搬回原位，否则用户两头落空。"""
    script = app_updater._write_macos_script(  # noqa: SLF001
        tmp_path / "new.zip", tmp_path / "Foo.app",
    )
    text = script.read_text(encoding="utf-8")
    assert 'mv "$TARGET" "$OLD"' in text
    assert 'if ! cp -R "$APP" "$TARGET"' in text
    # 回滚分支：先删掉半成品再搬回备份
    assert 'rm -rf "$TARGET"' in text
    assert 'mv "$OLD" "$TARGET"' in text
    assert 'xattr -dr com.apple.quarantine' in text
    script.unlink()


def test_redirect_target_must_be_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """urllib 会自动跳转，落点域名同样要校验。"""
    class _Redirected:
        def geturl(self) -> str:
            return "https://evil.example.com/payload.exe"

    def fake_open(request, timeout=30):
        return _Redirected()

    monkeypatch.setattr(app_updater.urllib.request, "urlopen", fake_open)
    with pytest.raises(ValueError, match="disallowed host"):
        app_updater._urlopen("https://api.github.com/repos/a/b/releases/latest")  # noqa: SLF001


def test_install_refuses_unwritable_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有写入权限时提前拒绝，而不是让脚本静默失败。"""
    payload = tmp_path / "a.exe"
    payload.write_bytes(b"x")
    monkeypatch.setattr(app_updater, "is_frozen", lambda: True)
    monkeypatch.setattr(app_updater, "install_root", lambda: tmp_path / "sub" / "app.exe")
    monkeypatch.setattr(app_updater.os, "access", lambda *a, **k: False)
    message = app_updater.install_and_restart(payload)
    assert "权限" in message


def test_spawn_detached_is_not_called_without_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = tmp_path / "a.exe"
    payload.write_bytes(b"x")
    monkeypatch.setattr(app_updater, "is_frozen", lambda: True)
    monkeypatch.setattr(app_updater, "install_root", lambda: tmp_path / "sub" / "app.exe")
    monkeypatch.setattr(app_updater.os, "access", lambda *a, **k: False)
    called: list[Path] = []
    monkeypatch.setattr(app_updater, "_spawn_detached", lambda s, a=None: called.append(s))
    app_updater.install_and_restart(payload)
    assert called == []


def test_cleanup_old_on_source_tree_is_noop() -> None:
    app_updater.cleanup_old()  # 未打包 → 直接返回，不抛错


def test_cleanup_removes_stale_update_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """更新包缓存要定期清掉，否则临时目录会一直涨。"""
    import os as _os
    import time as _time

    monkeypatch.setattr(app_updater.tempfile, "gettempdir", lambda: str(tmp_path))
    stale = tmp_path / f"{app_updater.DOWNLOAD_CACHE_PREFIX}1.0.0"
    stale.mkdir()
    (stale / "a.exe").write_bytes(b"x")
    fresh = tmp_path / f"{app_updater.DOWNLOAD_CACHE_PREFIX}2.0.0"
    fresh.mkdir()

    old = _time.time() - 10 * 24 * 3600
    _os.utime(stale, (old, old))

    app_updater._cleanup_stale_downloads()  # noqa: SLF001

    assert not stale.exists()
    assert fresh.exists()


def test_should_auto_check_throttles() -> None:
    assert app_updater.should_auto_check(0, now=10) is True
    assert app_updater.should_auto_check(1000, now=1010) is False
    assert app_updater.should_auto_check(0, now=app_updater.CHECK_INTERVAL_SECONDS) is True


def test_current_version_reads_version_file() -> None:
    assert app_updater.current_version().count(".") >= 1 or app_updater.current_version() == ""


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (
            urllib.error.HTTPError(
                "https://api.github.com/x", 403, "rate limit exceeded", {}, None,
            ),
            "过于频繁",
        ),
        (urllib.error.HTTPError("https://api.github.com/x", 404, "Not Found", {}, None),
         "还没有发布过正式版本"),
        (urllib.error.HTTPError("https://api.github.com/x", 500, "boom", {}, None),
         "HTTP 500"),
        (urllib.error.URLError("timed out"), "网络连接失败"),
        (TimeoutError("slow"), "超时"),
        (ValueError("disallowed host: evil"), "disallowed host"),
    ],
)
def test_friendly_error_is_actionable(exc: Exception, needle: str) -> None:
    """未认证 API 是 60 次/小时，撞上 403 时要给出人话而不是 HTTP Error。"""
    assert needle in app_updater._friendly_error(exc)  # noqa: SLF001


def test_is_prerelease_only_checks_suffix() -> None:
    assert app_updater.is_prerelease("1.3.0-rc1") is True
    assert app_updater.is_prerelease("v1.3.0-beta") is True
    assert app_updater.is_prerelease("1.3.0") is False
    # 旧实现用子串匹配，1.2.0 这类正常版本也会被误判
    assert app_updater.is_prerelease("1.2.0") is False
    assert app_updater.is_prerelease("") is False


def test_parse_checksums_ignores_garbage() -> None:
    text = (
        "# comment\n"
        "  \n"
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  a.exe\n"
        "not-a-hash  b.exe\n"
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef *c.zip\n"
    )
    parsed = app_updater.parse_checksums(text)
    assert set(parsed) == {"a.exe", "c.zip"}


def _release_with_checksums() -> "app_updater.Release":
    return app_updater.Release(
        version="9.9.9", tag="v9.9.9", page_url="x",
        assets=[
            app_updater.Asset(app_updater.ASSET_WINDOWS, "https://github.com/a"),
            app_updater.Asset(app_updater.ASSET_CHECKSUMS, "https://github.com/c"),
        ],
    )


def test_download_update_verifies_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """release 带清单时校验通过就正常返回。"""
    payload_bytes = b"binary"
    good = hashlib.sha256(payload_bytes).hexdigest()

    def fake_download(asset, dest_dir, progress=None, chunk_size=1 << 20):
        path = Path(dest_dir) / asset.name
        if asset.name == app_updater.ASSET_CHECKSUMS:
            path.write_text(
                f"{good}  {app_updater.ASSET_WINDOWS}\n", encoding="utf-8",
            )
            return path
        path.write_bytes(payload_bytes)
        return path

    monkeypatch.setattr(app_updater, "download_release_asset", fake_download)
    monkeypatch.setattr(app_updater, "asset_name_for", lambda p=None: app_updater.ASSET_WINDOWS)
    path = app_updater.download_update(_release_with_checksums(), tmp_path)
    assert path.read_bytes() == payload_bytes


def test_download_update_rejects_bad_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = "0" * 64

    def fake_download(asset, dest_dir, progress=None, chunk_size=1 << 20):
        path = Path(dest_dir) / asset.name
        if asset.name == app_updater.ASSET_CHECKSUMS:
            path.write_text(
                f"{bad}  {app_updater.ASSET_WINDOWS}\n", encoding="utf-8",
            )
            return path
        path.write_bytes(b"evil")
        return path

    monkeypatch.setattr(app_updater, "download_release_asset", fake_download)
    monkeypatch.setattr(app_updater, "asset_name_for", lambda p=None: app_updater.ASSET_WINDOWS)
    with pytest.raises(ValueError, match="校验和不匹配"):
        app_updater.download_update(_release_with_checksums(), tmp_path)
    assert not (tmp_path / app_updater.ASSET_WINDOWS).exists(), "坏包必须删除"


def test_download_update_rejects_missing_checksums(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有清单时不得下载未经校验的安装包。"""
    def fake_download(asset, dest_dir, progress=None, chunk_size=1 << 20):
        path = Path(dest_dir) / asset.name
        path.write_bytes(b"data")
        return path

    monkeypatch.setattr(app_updater, "download_release_asset", fake_download)
    monkeypatch.setattr(app_updater, "asset_name_for", lambda p=None: app_updater.ASSET_WINDOWS)
    release = app_updater.Release(
        version="1.0.0", tag="v1.0.0", page_url="x",
        assets=[app_updater.Asset(app_updater.ASSET_WINDOWS, "https://github.com/a")],
    )
    with pytest.raises(ValueError, match="校验"):
        app_updater.download_update(release, tmp_path)
    assert not (tmp_path / app_updater.ASSET_WINDOWS).exists()


def test_download_update_without_platform_asset_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_updater, "asset_name_for", lambda p=None: "Other.exe")
    with pytest.raises(ValueError, match="没有当前平台的安装包"):
        app_updater.download_update(_release_with_checksums(), tmp_path)


@pytest.mark.parametrize("text", ["", "invalid  app.exe", "0" * 64 + "  other.exe"])
def test_missing_checksum_entry_prevents_payload_download(tmp_path, monkeypatch, text):
    calls = []
    def download(asset, dest_dir, *args):
        calls.append(asset.name)
        path = dest_dir / asset.name
        path.write_text(text)
        return path
    monkeypatch.setattr(app_updater, "asset_name_for", lambda p=None: app_updater.ASSET_WINDOWS)
    monkeypatch.setattr(app_updater, "download_release_asset", download)
    with pytest.raises(ValueError, match="校验"):
        app_updater.download_update(_release_with_checksums(), tmp_path)
    assert calls == [app_updater.ASSET_CHECKSUMS]


def test_conflicting_checksums_rejected():
    with pytest.raises(ValueError, match="冲突"):
        app_updater.parse_checksums("0" * 64 + "  a.exe\n" + "1" * 64 + "  a.exe\n")


def test_macos_script_quotes_paths(tmp_path):
    import shlex
    payload = tmp_path / "new ' $(touch INJECTED).zip"
    target = tmp_path / 'app `touch INJECTED` 中.app'
    script = app_updater._write_macos_script(payload, target)
    text = script.read_text()
    assert "NEW=" + shlex.quote(str(payload)) in text
    assert "TARGET=" + shlex.quote(str(target)) in text
    if os.name != "nt":
        assert script.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("copy_fails", [False, True])
def test_macos_installer_executes_special_paths_safely(tmp_path, copy_fails):
    import os
    import subprocess
    import sys
    if sys.platform != "darwin":
        pytest.skip("macOS shell installation simulation")
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, body in {
        "sleep": "exit 0",
        "xattr": "exit 0",
        "open": "exit 0",
        "ditto": 'mkdir -p "$4/New.app"; printf new > "$4/New.app/marker"',
        **({"cp": "exit 1"} if copy_fails else {}),
    }.items():
        command = commands / name
        command.write_text("#!/bin/sh\n" + body + "\n")
        command.chmod(0o700)
    target = tmp_path / "应用 ' $(touch INJECTED) `touch INJECTED`.app"
    target.mkdir()
    (target / "marker").write_text("old")
    payload = tmp_path / "新包 ' $(touch INJECTED).zip"
    payload.write_text("fake zip")
    script = app_updater._write_macos_script(payload, target)
    result = subprocess.run(["/bin/sh", str(script)], cwd=tmp_path,
                            env={**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"]},
                            capture_output=True)
    assert result.returncode == (1 if copy_fails else 0), result.stderr
    assert (target / "marker").read_text() == ("old" if copy_fails else "new")
    assert not (tmp_path / "INJECTED").exists()


@pytest.mark.parametrize("system,cpu", [("darwin", "x86_64"), ("win32", "ARM64")])
def test_unsupported_runtime_architecture_has_no_asset(monkeypatch, system, cpu):
    monkeypatch.setattr(app_updater.sys, "platform", system)
    monkeypatch.setattr(app_updater.host_platform, "machine", lambda: cpu)
    assert app_updater.asset_name_for() == ""
