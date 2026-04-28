# -*- mode: python ; coding: utf-8 -*-
# AdoptIQ macOS build spec — run on macOS: pyinstaller adoptiq_mac.spec

import os

block_cipher = None


def _datas():
    root = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in globals() else os.getcwd()
    datas = [
        (os.path.join(root, 'team_config.json'), '.'),
        (os.path.join(root, 'templates'), 'templates'),
        (os.path.join(root, 'static'), 'static'),
    ]
    try:
        import certifi
        cert_path = certifi.where()
        if cert_path and os.path.exists(cert_path):
            datas.append((cert_path, 'certifi'))
    except Exception:
        pass

    # Round 35 / native-corpus: bundle the bake artifacts produced by
    # ``scripts/bake_corpus.py`` (invoked by ``build_mac_dmg.sh`` BEFORE
    # PyInstaller).  Each entry ships under
    # ``AdoptIQ.app/Contents/Resources/baked_corpus/`` and is read at
    # runtime by ``corpus_bootstrap``, which copies the encrypted
    # database to a writable user dir on first launch.
    #
    # Each artifact is opt-in: when ``ADOPTIQ_BAKE_CORPUS=0`` (or the
    # bake script crashes), the files are absent and PyInstaller would
    # otherwise abort with "missing source file".  ``Path.exists()``
    # gates each one so a skipped/failed bake still produces a working
    # .app -- the user just sees the "Awaiting sign-in" state on first
    # run and the corpus refreshes from the source share once they
    # connect.
    bake_dir = os.path.join(root, 'bake')
    # ``corpus.db.salt`` matches ``corpus_crypto._salt_path_for`` --
    # changing this name here without updating the bake script and
    # ``corpus_bootstrap._BAKED_CORPUS_FILES`` would ship a phantom
    # salt path that the runtime never finds, regenerating the salt
    # on first launch and bricking the encrypted DB.
    for fname in (
        'corpus.db.enc',
        'sentinel.json',
        'corpus.db.salt',
        'corpus.sentinel.lock.json',
    ):
        candidate = os.path.join(bake_dir, fname)
        if os.path.exists(candidate):
            datas.append((candidate, 'baked_corpus'))

    return datas


hidden_imports = [
    'flask', 'flask_wtf', 'wtforms', 'werkzeug',
    'pandas', 'numpy', 'openpyxl', 'xlsxwriter',
    'docx',
    'requests', 'bs4', 'feedparser',
    'snowflake.connector', 'snowflake.connector.snow_logging', 'sqlalchemy',
    'hvac', 'cryptography',
    'openai', 'dotenv',
    'truststore',
    'adoptiq_backend', 'config', 'executive_report_builder',
    'compact_report_formatter', 'advanced_renewal_analyzer', 'report_utils',
    'data_source_validator', 'leader_report_generator', 'enhanced_snowflake_insights',
    'executive_intelligence_formatter',
    'enhanced_admin_dashboard_v2',
    'incident_storage',
    'cisco_internal_integrations',
    '_bundled_secrets',
    'error_classifier', 'connectivity_diagnostics',
    'ai_narrative_validator',
    'knowledge_schema',
    'corpus_crypto',
    'corpus_indexer',
    'corpus_retriever',
    'corpus_bootstrap',
    'ask_ai_corpus',
    'report_corpus_context',
    'adoptiq_settings',
    # Round 17.2 -> Round 36: SharePoint Microsoft Graph pull retired.
    # The MSAL/Graph runtime path was blocked by Cisco tenant admin-
    # consent on the default Microsoft Graph PowerShell client ID.
    # AdoptIQ now uses the OneDrive desktop client's local mirror at
    # Config.CSONE_ONEDRIVE_FOLDER -- no MSAL, no keyring, no token
    # cache to bundle.  The ``sharepoint_corpus_source`` module was
    # deleted; ``msal`` and ``keyring`` were dropped from
    # requirements.txt.
    # Round 32 / Phase 1.B -- bundle matplotlib + Pillow so chart
    # generation in the executive report actually produces images
    # in the packaged .app.  Build6 had matplotlib in ``excludes``
    # which is why every report logged "Matplotlib not available".
    # Force the headless Agg backend at runtime in the chart
    # generation entry point (no display in a packaged app).
    'matplotlib', 'matplotlib.pyplot', 'matplotlib.backends',
    'matplotlib.backends.backend_agg',
    'PIL', 'PIL.Image', 'PIL.PngImagePlugin', 'PIL.JpegImagePlugin',
]

a = Analysis(
    ['app_simple.py'],
    pathex=[],
    binaries=[],
    datas=_datas(),
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Round 32 / Phase 1.B -- matplotlib + PIL removed from
        # excludes so the executive-report chart generator stops
        # falling back to "Matplotlib not available - charts will
        # be skipped" inside the packaged .app.
        'tkinter', 'playwright',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AdoptIQ',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Build as a normal foreground .app (shows in Dock / can be quit from menu).
    # When console=True, PyInstaller sets LSBackgroundOnly=true which makes the app
    # run "headless" (no Dock icon) and confuses end users even though the server is running.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AdoptIQ',
)

app = BUNDLE(
    coll,
    name='AdoptIQ.app',
    icon=None,
    bundle_identifier='com.cisco.adoptiq',
    info_plist={
        # Ensure the app is a standard foreground app.
        "LSBackgroundOnly": False,
        # Populate version metadata in Finder/About dialogs.
        "CFBundleShortVersionString": os.environ.get("ADOPTIQ_VERSION", "1.0.3"),
        "CFBundleVersion": os.environ.get("ADOPTIQ_BUILD", "1"),
        "NSHighResolutionCapable": True,
    },
)
