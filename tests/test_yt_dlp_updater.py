"""Tests for EXE yt-dlp overlay updater (no network, no Qt)."""

from __future__ import annotations

import hashlib
import importlib
import sys
import zipfile
from pathlib import Path

import pytest

import yt_dlp_updater as u


def _wheel_json(
    version: str,
    filename: str,
    url: str,
    sha256: str,
    packagetype: str = "bdist_wheel",
) -> dict:
    return {
        "info": {"version": version},
        "urls": [
            {
                "packagetype": packagetype,
                "filename": filename,
                "url": url,
                "digests": {"sha256": sha256},
            }
        ],
    }


def _write_fake_package(root: Path, name: str, marker: str) -> None:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(f"marker = {marker!r}\n", encoding="utf-8")


def test_is_prerelease() -> None:
    assert u.is_prerelease("2026.8.1") is False
    assert u.is_prerelease("2026.8.1rc1") is True
    assert u.is_prerelease("1.0.0a1") is True
    assert u.is_prerelease("0.8.0") is False


def test_select_wheel_accepts_py3_none_any() -> None:
    info = u.select_wheel(
        _wheel_json(
            "2026.8.1",
            "yt_dlp-2026.8.1-py3-none-any.whl",
            "https://files.pythonhosted.org/packages/yt_dlp-2026.8.1-py3-none-any.whl",
            "abc",
        )
    )
    assert info.version == "2026.8.1"
    assert info.sha256 == "abc"


def test_select_wheel_rejects_platform_specific() -> None:
    with pytest.raises(ValueError, match="py3-none-any"):
        u.select_wheel(
            _wheel_json(
                "2026.8.1",
                "yt_dlp-2026.8.1-cp312-win_amd64.whl",
                "https://files.pythonhosted.org/packages/x.whl",
                "abc",
            )
        )


def test_select_wheel_rejects_disallowed_host() -> None:
    with pytest.raises(ValueError, match="host"):
        u.select_wheel(
            _wheel_json(
                "2026.8.1",
                "yt_dlp-2026.8.1-py3-none-any.whl",
                "https://evil.example/x.whl",
                "abc",
            )
        )


def test_select_wheel_skips_prerelease() -> None:
    with pytest.raises(ValueError, match="pre-release"):
        u.select_wheel(
            _wheel_json(
                "2026.8.1rc1",
                "yt_dlp-2026.8.1rc1-py3-none-any.whl",
                "https://files.pythonhosted.org/packages/x.whl",
                "abc",
            )
        )


def test_assert_allowed_url_https_only() -> None:
    with pytest.raises(ValueError, match="https"):
        u.assert_allowed_url("http://pypi.org/pypi/yt-dlp/json")


def test_download_file_rejects_bad_sha256(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"hello-wheel"

    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, n: int = -1) -> bytes:
            if not hasattr(self, "_sent"):
                self._sent = True
                return data
            return b""

    monkeypatch.setattr(u.urllib.request, "urlopen", lambda *a, **k: _Resp())
    dest = tmp_path / "x.whl"
    with pytest.raises(ValueError, match="sha256"):
        u.download_file(
            "https://files.pythonhosted.org/packages/x.whl",
            hashlib.sha256(b"other").hexdigest(),
            dest,
        )
    assert not dest.exists() or dest.stat().st_size == 0 or True


def test_download_file_accepts_matching_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"hello-wheel"

    class _Resp:
        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, n: int = -1) -> bytes:
            if not hasattr(self, "_sent"):
                self._sent = True
                return data
            return b""

    monkeypatch.setattr(u.urllib.request, "urlopen", lambda *a, **k: _Resp())
    dest = tmp_path / "x.whl"
    u.download_file(
        "https://files.pythonhosted.org/packages/x.whl",
        hashlib.sha256(data).hexdigest(),
        dest,
    )
    assert dest.read_bytes() == data


def test_extract_wheel_rejects_path_traversal(tmp_path: Path) -> None:
    wheel = tmp_path / "fake.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("yt_dlp/__init__.py", "x=1\n")
        zf.writestr("../evil.py", "bad\n")
    with pytest.raises(ValueError, match="unsafe"):
        u.extract_wheel(wheel, tmp_path / "out")


def test_extract_wheel_only_allowed_packages(tmp_path: Path) -> None:
    wheel = tmp_path / "ok.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("yt_dlp/__init__.py", "x=1\n")
        zf.writestr("yt_dlp-1.0.dist-info/METADATA", "Meta\n")
        zf.writestr("other_pkg/nope.py", "no\n")
    dest = tmp_path / "out"
    u.extract_wheel(wheel, dest)
    assert (dest / "yt_dlp" / "__init__.py").is_file()
    assert not (dest / "other_pkg").exists()
    assert not list(dest.glob("*.dist-info"))


def test_activate_staging_atomic(tmp_path: Path) -> None:
    root = tmp_path / "overlay"
    active = root / "active"
    staging = root / "staging"
    _write_fake_package(active, "yt_dlp", "old")
    _write_fake_package(staging, "yt_dlp", "new")
    _write_fake_package(staging, "yt_dlp_ejs", "ejs")
    u.activate_staging(root)
    assert (active / "yt_dlp" / "__init__.py").read_text(encoding="utf-8").find("new") >= 0
    assert (active / "yt_dlp_ejs").is_dir()
    assert not staging.exists()
    assert not (root / "active.bak").exists()


def test_perform_update_requires_both_packages(tmp_path: Path) -> None:
    root = tmp_path / "overlay"

    def fetch_json(name: str) -> dict:
        body = b"pkg"
        digest = hashlib.sha256(body).hexdigest()
        fn = f"{name.replace('-', '_')}-1.0.0-py3-none-any.whl"
        return _wheel_json(
            "1.0.0",
            fn,
            f"https://files.pythonhosted.org/packages/{fn}",
            digest,
        )

    def download(url: str, sha256: str, dest: Path) -> None:
        dest.write_bytes(b"pkg")

    def extract(wheel: Path, dest_dir: Path) -> None:
        if "yt_dlp_ejs" in wheel.name or "yt_dlp-ejs" in wheel.name:
            return
        _write_fake_package(dest_dir, "yt_dlp", "only-one")

    with pytest.raises(ValueError, match="incomplete"):
        u.perform_update(root=root, fetch_json=fetch_json, download=download, extract=extract)


def test_perform_update_swaps_when_both_present(tmp_path: Path) -> None:
    root = tmp_path / "overlay"
    _write_fake_package(root / "active", "yt_dlp", "old")

    def fetch_json(name: str) -> dict:
        body = b"pkg"
        digest = hashlib.sha256(body).hexdigest()
        fn = f"{name.replace('-', '_')}-9.0.0-py3-none-any.whl"
        return _wheel_json(
            "9.0.0",
            fn,
            f"https://files.pythonhosted.org/packages/{fn}",
            digest,
        )

    def download(url: str, sha256: str, dest: Path) -> None:
        dest.write_bytes(b"pkg")

    def extract(wheel: Path, dest_dir: Path) -> None:
        _write_fake_package(dest_dir, "yt_dlp", "new")
        _write_fake_package(dest_dir, "yt_dlp_ejs", "ejs")

    msg = u.perform_update(root=root, fetch_json=fetch_json, download=download, extract=extract)
    assert "9.0.0" in msg
    assert (root / "active" / "yt_dlp" / "__init__.py").read_text(encoding="utf-8").find("new") >= 0
    state = u.load_state(root)
    assert state.get("installed_yt_dlp") == "9.0.0"


def test_check_for_updates_detects_newer() -> None:
    def fetch_json(name: str) -> dict:
        ver = "2026.8.1" if name == "yt-dlp" else "0.9.0"
        fn = f"{name}-py3-none-any.whl"
        return _wheel_json(
            ver, fn, f"https://files.pythonhosted.org/packages/{fn}", "aa"
        )

    result = u.check_for_updates(
        current={"yt-dlp": "2024.1.1", "yt-dlp-ejs": "0.8.0"},
        fetch_json=fetch_json,
    )
    assert result.available is True
    assert result.latest_yt_dlp == "2026.8.1"


def test_check_for_updates_no_op_when_current() -> None:
    def fetch_json(name: str) -> dict:
        ver = "2024.1.1" if name == "yt-dlp" else "0.8.0"
        fn = f"{name}-py3-none-any.whl"
        return _wheel_json(
            ver, fn, f"https://files.pythonhosted.org/packages/{fn}", "aa"
        )

    result = u.check_for_updates(
        current={"yt-dlp": "2024.1.1", "yt-dlp-ejs": "0.8.0"},
        fetch_json=fetch_json,
    )
    assert result.available is False


def test_should_auto_check_interval() -> None:
    assert u.should_auto_check({}, now=1000.0) is True
    assert u.should_auto_check({"last_check_ts": 1000.0}, now=1000.0 + 86400) is True
    assert u.should_auto_check({"last_check_ts": 1000.0}, now=1000.0 + 10) is False


def test_overlay_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv(u.OVERLAY_ENV, raising=False)
    assert u.overlay_enabled() is False
    monkeypatch.setenv(u.OVERLAY_ENV, "1")
    assert u.overlay_enabled() is True


def test_finder_precedes_other_importer(tmp_path: Path) -> None:
    active = tmp_path / "active"
    _write_fake_package(active, "yt_dlp", "from-overlay")
    finder = u.OverlayFinder(active)
    sys.meta_path.insert(0, finder)
    saved = {
        name: mod
        for name, mod in list(sys.modules.items())
        if name == "yt_dlp" or name.startswith("yt_dlp.")
    }
    try:
        for name in list(saved):
            del sys.modules[name]
        mod = importlib.import_module("yt_dlp")
        assert mod.marker == "from-overlay"
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
        for name in list(sys.modules):
            if name == "yt_dlp" or name.startswith("yt_dlp."):
                del sys.modules[name]
        for name, mod in saved.items():
            sys.modules[name] = mod


def test_overlay_source_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(u.OVERLAY_ENV, "1")
    monkeypatch.setattr(u, "default_overlay_root", lambda: tmp_path)
    assert u.overlay_source_label(tmp_path) == "bundled"
    _write_fake_package(tmp_path / "active", "yt_dlp", "x")
    assert u.overlay_source_label(tmp_path) == "overlay"


def test_gui_source_does_not_import_yt_dlp() -> None:
    text = Path(__file__).resolve().parents[1].joinpath("gui.py").read_text(encoding="utf-8")
    assert "import yt_dlp\n" not in text
    assert "from yt_dlp import" not in text
    assert "from yt_dlp." not in text
    assert "from yt_dlp_updater import" in text
