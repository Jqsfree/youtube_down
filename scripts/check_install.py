"""Opt-in native installer exercise using old/new real build artifacts.

Never points at a user's installation. Run only on Windows/macOS build runners.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_updater


def native_env(**updates: str) -> dict[str, str]:
    # Do not feed PowerShell 7 module directories to Windows PowerShell 5.1.
    env = dict(os.environ, **updates)
    if os.name == 'nt':
        env = {key: value for key, value in env.items()
               if key.casefold() != 'psmodulepath'}
    return env


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def processes(executable: Path) -> list[int]:
    if os.name == 'nt':
        env = native_env(CHECK_EXE=str(executable))
        result = subprocess.check_output([
            'powershell.exe', '-NoProfile', '-Command',
            'Get-CimInstance Win32_Process | Where-Object { '
            '$_.ExecutablePath -eq $env:CHECK_EXE } | ForEach-Object { $_.ProcessId }',
        ], env=env, text=True)
        return [int(line) for line in result.splitlines() if line.strip()]
    result = subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True)
    return [int(parts[0]) for line in result.splitlines()
            if len(parts := line.strip().split(maxsplit=1)) == 2
            and parts[1] == str(executable)]


def stop(executable: Path) -> None:
    for pid in processes(executable):
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 15
    while processes(executable) and time.monotonic() < deadline:
        time.sleep(.5)
    assert not processes(executable), 'temporary application did not stop'


def assert_started(executable: Path) -> None:
    deadline = time.monotonic() + 60
    while not processes(executable):
        if time.monotonic() >= deadline:
            if os.name != 'nt':
                listing = subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True)
                print('Expected executable:', str(executable), flush=True)
                print('Application process candidates:', '\n'.join(
                    line for line in listing.splitlines() if 'YouTube' in line), flush=True)
            raise AssertionError('installed application did not start')
        time.sleep(1)
    time.sleep(3)
    assert processes(executable), 'installed application exited during startup'


def check_acl(directory: Path) -> None:
    if os.name != 'nt':
        assert directory.stat().st_mode & 0o777 == 0o700
        return
    subprocess.run([
        'powershell.exe', '-NoProfile', '-Command',
        '$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; '
        '$acl=Get-Acl -LiteralPath $env:CHECK_DIR; '
        'Write-Output $acl.Sddl; Write-Output $sid; '
        'foreach ($entry in $acl.Access) { '
        'if ($entry.IsInherited -or '
        '$entry.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -ne $sid '
        '-or $entry.AccessControlType -ne "Allow") { '
        '$acl | Format-List Owner,AccessToString | Out-Host; exit 1 } }; '
        'if ($acl.Access.Count -eq 0) { exit 1 }; Write-Output "ACL verified"',
    ], env=native_env(CHECK_DIR=str(directory)), text=True, check=True)


def exercise(old: Path, new: Path) -> None:
    assert sys.platform == 'darwin' or os.name == 'nt', 'native runner required'
    old, new = old.resolve(), new.resolve()
    assert old.is_file() and new.is_file()
    with tempfile.TemporaryDirectory(prefix='ytdown-native-check-') as folder:
        root = Path(folder).resolve()
        special = root / "中文 空格 [a] ' & $(touch INJECTED)"
        special.mkdir()
        neighbour = special / 'neighbour.txt'
        neighbour.write_text('keep', encoding='utf-8')
        if os.name == 'nt':
            target = special / "Downloader [a] ' &.exe"
            exe = target
            original = old
            incoming = new
        else:
            subprocess.run(['ditto', '-x', '-k', str(old), str(root / 'old')], check=True)
            subprocess.run(['ditto', '-x', '-k', str(new), str(root / 'new')], check=True)
            original = next((root / 'old').glob('*.app'))
            incoming = next((root / 'new').glob('*.app'))
            target = special / "Downloader [a] ' &.app"
            exe = target / 'Contents/MacOS' / incoming.stem
        for case in ('success', 'rollback', *(['occupied'] if os.name == 'nt' else [])):
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if os.name == 'nt':
                shutil.copy2(original, target)
                expected = digest(incoming if case != 'rollback' else original)
            else:
                shutil.copytree(original, target, symlinks=True)
                old_exe = next((target / 'Contents/MacOS').iterdir())
                exe = target / 'Contents/MacOS' / incoming.stem
                expected = digest(incoming / 'Contents/MacOS' / incoming.stem
                                  if case != 'rollback' else old_exe)
            payload = root / ('payload.exe' if os.name == 'nt' else 'payload.zip')
            shutil.copy2(new, payload)
            script = (app_updater._write_windows_script(payload, target) if os.name == 'nt'
                      else app_updater._write_macos_script(payload, target))
            handle = None
            env = native_env(QT_QPA_PLATFORM='offscreen')
            if case == 'rollback':
                if os.name == 'nt':
                    payload.unlink()  # Fails after old target has been moved.
                else:
                    # Fail only the copy step; use real ditto/mv/open and real .app.
                    bindir = root / 'fail-copy'
                    bindir.mkdir()
                    cp = bindir / 'cp'
                    cp.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
                    cp.chmod(0o755)
                    env['PATH'] = str(bindir) + os.pathsep + env['PATH']
            if case == 'occupied':
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.CreateFileW.restype = ctypes.c_void_p
                kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                    ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                    ctypes.c_void_p]
                handle = kernel.CreateFileW(str(target), 0x80000000, 0, None, 3, 0, None)
                assert handle not in (None, ctypes.c_void_p(-1).value)
            try:
                check_acl(script.parent)
                command = (['powershell.exe', '-NoProfile', '-NonInteractive',
                            '-ExecutionPolicy', 'Bypass', '-File', str(script)]
                           if os.name == 'nt' else ['/bin/sh', str(script)])
                installer = subprocess.Popen(command, env=env, cwd=root)
                if handle is not None:
                    time.sleep(5)
                    assert installer.poll() is None, 'installer did not wait for file lock'
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    assert kernel.CloseHandle(handle)
                    handle = None
                rc = installer.wait(timeout=120)
                assert rc == (1 if case == 'rollback' else 0), (case, rc)
                assert digest(exe) == expected, 'replacement/rollback content differs'
                assert_started(exe)
                assert neighbour.read_text(encoding='utf-8') == 'keep'
                assert not (root / 'INJECTED').exists()
                assert not (special / 'INJECTED').exists()
                print(json.dumps({'case': case, 'result': 'passed',
                                  'platform': sys.platform}, ensure_ascii=True), flush=True)
            finally:
                if handle is not None:
                    kernel.CloseHandle(handle)
                stop(exe)
                shutil.rmtree(script.parent, ignore_errors=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', required=True, type=Path)
    parser.add_argument('--new', required=True, type=Path)
    args = parser.parse_args()
    exercise(args.old, args.new)
