"""Private temporary directories for credentials and installer scripts."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path


def restrict_directory(directory: Path) -> None:
    if os.name != "nt":
        directory.chmod(0o700)
        return
    # chmod does not restrict Windows ACLs. Use a SID to avoid localized names.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
    )
    sid = re.search(rb"S-1-5-(?:\d+-)+\d+", result.stdout)
    if sid is None:
        raise RuntimeError("无法确定当前用户权限，停止创建敏感临时文件")
    # mkdtemp can create explicit OWNER/SYSTEM/Administrators entries. Reset
    # this new, still-empty directory before removing inheritance and granting.
    subprocess.run(
        ["icacls", str(directory), "/reset"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
    )
    subprocess.run(
        ["icacls", str(directory), "/inheritance:r", "/grant:r",
         f"*{sid.group().decode('ascii')}:(OI)(CI)F"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
    )


def make_private_directory(prefix: str) -> Path:
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        restrict_directory(directory)
    except BaseException:
        shutil.rmtree(directory)
        raise
    return directory


@contextmanager
def private_directory(prefix: str):
    directory = make_private_directory(prefix)
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
