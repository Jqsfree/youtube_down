import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_private_directory_permissions_and_cleanup():
    from private_files import private_directory
    with private_directory("ytdown-test-") as directory:
        path = Path(directory)
        assert path.is_dir()
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o700
        else:
            from scripts.check_install import check_acl
            check_acl(path)
    assert not path.exists()


def test_windows_acl_is_restricted_to_current_sid(tmp_path, monkeypatch):
    import private_files
    calls = []
    monkeypatch.setattr(private_files, "os", SimpleNamespace(name="nt"))
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=b'"user","S-1-5-21-111-222-333-1001"')
    monkeypatch.setattr(private_files.subprocess, "run", run)
    private_files.restrict_directory(tmp_path)
    assert calls[1] == ["icacls", str(tmp_path), "/inheritance:r", "/grant:r",
                        "*S-1-5-21-111-222-333-1001:(OI)(CI)F"]


def test_windows_acl_failure_stops_creation(tmp_path, monkeypatch):
    import private_files
    monkeypatch.setattr(private_files, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(private_files.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"invalid"))
    with pytest.raises(RuntimeError, match="权限"):
        private_files.restrict_directory(tmp_path)
