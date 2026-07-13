# -*- mode: python ; coding: utf-8 -*-
# AdoptIQ Installer - packages AdoptIQ.exe + README into AdoptIQ-Setup.exe
# Run AFTER building AdoptIQ.exe (dist/AdoptIQ.exe must exist).
# Usage: pyinstaller adoptiq_installer.spec

import os

block_cipher = None

def _datas():
    root = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in dir() else os.getcwd()
    datas = []
    exe = os.path.join(root, 'dist', 'AdoptIQ.exe')
    uninstall = os.path.join(root, 'dist', 'AdoptIQ-Uninstall.exe')
    readme = os.path.join(root, 'README.md')
    version_info = os.path.join(root, 'version_info.txt')
    if os.path.exists(exe):
        datas.append((exe, '.'))
    if os.path.exists(uninstall):
        datas.append((uninstall, '.'))
    if os.path.exists(readme):
        datas.append((readme, '.'))
    if os.path.exists(version_info):
        datas.append((version_info, '.'))
    return datas

a = Analysis(
    ['installer_app.py'],
    pathex=[],
    binaries=[],
    datas=_datas(),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'PIL', 'tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AdoptIQ-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
)
