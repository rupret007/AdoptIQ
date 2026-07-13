#!/usr/bin/env python3
r"""
AdoptIQ Installer - Copies AdoptIQ.exe, uninstaller, README to %APPDATA%\AdoptIQ,
registers in Programs and Features, creates shortcuts, launches app, opens browser.
"""
import os
import sys
import shutil
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path

# Programs and Features registry key (per-user, no admin)
_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AdoptIQ"


def _read_version(base: Path) -> tuple:
    """Read version and build from version_info.txt. Returns (version, build)."""
    try:
        vf = base / 'version_info.txt'
        if vf.exists():
            lines = vf.read_text(encoding='utf-8').strip().splitlines()
            return (lines[0].strip(), lines[1].strip()) if len(lines) >= 2 else ('1.0', '1')
    except Exception:
        pass
    return ('1.0', '1')


def _register_programs_features(dest: Path, uninstall_exe: Path, version: str, build: str) -> None:
    """Register AdoptIQ in Programs and Features for uninstall."""
    if sys.platform != 'win32':
        return
    try:
        import winreg
        display_version = f"{version}.{build}"  # e.g. 1.0.1
        display_name = f"AdoptIQ v{version} (Build {build})"
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\AdoptIQ")
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, display_version)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "AdoptIQ")
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstall_exe}"')
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(dest))
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
    except Exception:
        pass


def _create_shortcuts(exe_path: Path, working_dir: Path) -> None:
    """Create Start Menu and Desktop shortcuts so users can easily find AdoptIQ.

    Round 8 / Phase 6.3:
      * Use ``tempfile.NamedTemporaryFile`` so the helper script lives in
        a private, owner-only temp file rather than a predictable
        ``%TEMP%\\adoptiq_shortcut.ps1`` path that any local user could
        race or pre-create as a symlink.
      * Narrow ``ExecutionPolicy Bypass`` so it never persists outside
        this single PowerShell invocation: pass ``-NonInteractive`` and
        ``-InputFormat None`` so the helper cannot be hijacked via
        stdin, and rely on ``-ExecutionPolicy Bypass`` being
        process-scoped per Microsoft docs (it is stored in
        ``$env:PSExecutionPolicyPreference`` for the spawned process
        only and is *not* written to user/machine policy).
    """
    try:
        appdata = Path(os.environ.get('APPDATA', ''))
        if not appdata:
            return
        start_menu = appdata / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
        desktop = Path(os.environ.get('USERPROFILE', '')) / 'Desktop'
        # Escape single quotes for PowerShell
        target = str(exe_path).replace("'", "''")
        workdir = str(working_dir).replace("'", "''")
        for name, folder in [('Start Menu', start_menu), ('Desktop', desktop)]:
            if not folder.exists():
                continue
            lnk = folder / 'AdoptIQ.lnk'
            lnk_path = str(lnk).replace("'", "''")
            ps = f"""
$WshShell = New-Object -ComObject WScript.Shell
$s = $WshShell.CreateShortcut('{lnk_path}')
$s.TargetPath = '{target}'
$s.WorkingDirectory = '{workdir}'
$s.Description = 'AdoptIQ - AI-Powered Renewal Reports'
$s.Save()
"""
            # Write the PowerShell helper into a unique, owner-only temp file
            # via NamedTemporaryFile.  delete=False is required on Windows so
            # that PowerShell can re-open the file after we close it; we unlink
            # it ourselves in the ``finally`` block below.
            script_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode='w',
                    suffix='.ps1',
                    prefix='adoptiq_shortcut_',
                    encoding='utf-8',
                    delete=False,
                ) as tmp:
                    tmp.write(ps)
                    script_path = Path(tmp.name)
                # ``-ExecutionPolicy Bypass`` is process-scoped (stored in
                # $env:PSExecutionPolicyPreference for *this* invocation
                # only).  ``-NoProfile``, ``-NonInteractive`` and
                # ``-InputFormat None`` further narrow the bypass so the
                # spawned PowerShell cannot pick up profile scripts or
                # be driven by stdin.
                subprocess.run(
                    [
                        'powershell',
                        '-NoProfile',
                        '-NonInteractive',
                        '-InputFormat', 'None',
                        '-ExecutionPolicy', 'Bypass',
                        '-File', str(script_path),
                    ],
                    capture_output=True,
                    timeout=5,
                    cwd=str(working_dir),
                )
            finally:
                if script_path is not None:
                    try:
                        script_path.unlink(missing_ok=True)
                    except Exception:
                        pass
    except Exception:
        pass  # Shortcuts are nice-to-have; don't block install


def main():
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent

    appdata = Path(os.environ.get('APPDATA', Path.home()))
    dest = appdata / 'AdoptIQ'
    dest.mkdir(parents=True, exist_ok=True)

    exe_src = base / 'AdoptIQ.exe'
    readme_src = base / 'README.md'
    uninstall_src = base / 'AdoptIQ-Uninstall.exe'

    version, build = _read_version(base)

    if exe_src.exists():
        shutil.copy2(exe_src, dest / 'AdoptIQ.exe')
    if readme_src.exists():
        shutil.copy2(readme_src, dest / 'README.md')
    if uninstall_src.exists():
        shutil.copy2(uninstall_src, dest / 'AdoptIQ-Uninstall.exe')

    (dest / 'uploads').mkdir(exist_ok=True)
    (dest / 'outputs').mkdir(exist_ok=True)

    exe_path = dest / 'AdoptIQ.exe'
    if not exe_path.exists():
        print('Error: AdoptIQ.exe not found.')
        input('Press Enter to close...')
        return

    # Register in Programs and Features (for uninstall)
    uninstall_exe = dest / 'AdoptIQ-Uninstall.exe'
    if sys.platform == 'win32' and uninstall_exe.exists():
        _register_programs_features(dest, uninstall_exe, version, build)
        print(f'AdoptIQ v{version} (Build {build}) registered in Programs and Features.')

    # Create Start Menu and Desktop shortcuts so users can easily find AdoptIQ
    if sys.platform == 'win32':
        _create_shortcuts(exe_path, dest)
        print('Shortcuts added to Start Menu and Desktop.')

    print('AdoptIQ installed. Starting server...')
    # Launch AdoptIQ (Flask window opens)
    subprocess.Popen(
        [str(exe_path)],
        cwd=str(dest),
        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0,
    )

    print('Waiting for server to start...')
    time.sleep(12)

    print('Opening browser to http://localhost:5151')
    webbrowser.open('http://localhost:5151')

    print('AdoptIQ is running. Use the Flask window to see server output.')
    print('This window will close in 5 seconds.')
    time.sleep(5)

if __name__ == '__main__':
    main()
