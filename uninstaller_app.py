#!/usr/bin/env python3
r"""
AdoptIQ Uninstaller - Removes AdoptIQ from Programs and Features,
deletes shortcuts, and optionally removes the installation folder.
"""
import os
import sys
import shutil
from pathlib import Path

# Registry key for per-user uninstall (no admin required)
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AdoptIQ"


def _remove_registry():
    """Remove AdoptIQ from Programs and Features."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
            0,
            winreg.KEY_ALL_ACCESS
        )
        try:
            winreg.DeleteKey(key, "AdoptIQ")
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass


def _remove_shortcuts():
    """Remove Start Menu and Desktop shortcuts."""
    try:
        appdata = Path(os.environ.get('APPDATA', ''))
        if not appdata:
            return
        start_menu = appdata / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
        desktop = Path(os.environ.get('USERPROFILE', '')) / 'Desktop'
        for folder in [start_menu, desktop]:
            lnk = folder / 'AdoptIQ.lnk'
            if lnk.exists():
                try:
                    lnk.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def main():
    if sys.platform != 'win32':
        print('Uninstaller is for Windows only.')
        input('Press Enter to close...')
        return 1

    dest = Path(os.environ.get('APPDATA', '')) / 'AdoptIQ'
    if not dest.exists():
        print('AdoptIQ is not installed (folder not found).')
        _remove_registry()
        _remove_shortcuts()
        input('Press Enter to close...')
        return 0

    print('AdoptIQ Uninstaller')
    print('=' * 40)
    print(f'Installation folder: {dest}')
    print()
    response = input('Remove AdoptIQ and all its data? (y/n): ').strip().lower()
    if response not in ('y', 'yes'):
        print('Cancelled.')
        input('Press Enter to close...')
        return 0

    # Remove shortcuts first
    _remove_shortcuts()
    print('Shortcuts removed.')

    # Remove from Programs and Features
    _remove_registry()
    print('Removed from Programs and Features.')

    # Remove installation folder
    try:
        shutil.rmtree(dest)
        print('Installation folder removed.')
    except Exception as e:
        print(f'Could not remove folder: {e}')
        print('You may need to close AdoptIQ and try again, or delete manually.')
        input('Press Enter to close...')
        return 1

    print()
    print('AdoptIQ has been uninstalled.')
    input('Press Enter to close...')
    return 0


if __name__ == '__main__':
    sys.exit(main())
