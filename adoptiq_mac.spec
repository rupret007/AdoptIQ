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

    # Round 35 + Round 53 / native-corpus: bundle the bake artifacts
    # produced by ``scripts/bake_corpus.py`` (invoked by
    # ``build_mac_dmg.sh`` BEFORE PyInstaller).  Each entry ships
    # under ``AdoptIQ.app/Contents/Resources/baked_corpus/`` and is
    # read at runtime by ``corpus_bootstrap``, which copies the
    # encrypted database to a writable user dir on first launch.
    #
    # Each artifact is opt-in: when ``ADOPTIQ_BAKE_CORPUS=0`` (or the
    # bake script crashes), the files are absent and PyInstaller would
    # otherwise abort with "missing source file".  ``os.path.exists``
    # gates each one so a skipped/failed bake still produces a working
    # .app -- the user just sees "Sign in to OneDrive to unlock the
    # corpus" on first run and the corpus refreshes from their own
    # OneDrive sentinel once it syncs.
    #
    # Round 53 / Phase 53.2 -- this loop now bundles ONLY 2 files:
    # ``corpus.db.enc`` (encrypted snapshot) and ``corpus.db.salt``
    # (HKDF salt).  Pre-Round-53 the loop also shipped
    # ``sentinel.json`` (the AES key material) and
    # ``corpus.sentinel.lock.json`` (the digest pin), which made the
    # corpus offline-decryptable by anyone who obtained the DMG (see
    # QUALITY_AUDIT.md Round 52.2 -- HIGH severity).  Both files are
    # now resolved at runtime against the user's own OneDrive sync of
    # ``AI Projects/AdoptIQ_CSOne_Reports``; Microsoft's tenant ACL
    # on that share is the actual access boundary.  ``corpus.db.salt``
    # is NOT a secret (HKDF salt -- only the sentinel is) so shipping
    # it is intentional and pinned by ``corpus_crypto._salt_path_for``
    # which derives ``<db>.with_suffix(".salt")``.
    bake_dir = os.path.join(root, 'bake')
    for fname in (
        'corpus.db.enc',
        'corpus.db.salt',
    ):
        candidate = os.path.join(bake_dir, fname)
        if os.path.exists(candidate):
            datas.append((candidate, 'baked_corpus'))

    # Round 66 / Pass 5 - bundle the fastembed model cache produced
    # by ``scripts/bake_corpus.py`` (or pre-staged by the build
    # operator) so the FIRST Ask AI query in a fresh install does not
    # need a HuggingFace fetch.  The fastembed cache layout is
    # ``Resources/embeddings/<provider>/<model>/...``; ship the whole
    # directory if it exists.  When absent, the runtime falls back to
    # an on-demand HuggingFace download (still requires network).
    embeddings_dir = os.path.join(root, 'embeddings')
    if os.path.isdir(embeddings_dir):
        datas.append((embeddings_dir, 'Resources/embeddings'))

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
    # Round 59 / Build32: pin Round 57 modules so the post-render Word
    # source-citation injector ships in the frozen build.
    # ``report_source_injector`` is statically imported by
    # ``app_simple.py`` so PyInstaller's analyzer would normally find it;
    # we still pin it explicitly because ``report_iteration_loop`` is only
    # *lazy*-imported inside ``report_source_injector._gate_kpi_aliases``
    # (PyInstaller does not follow lazy imports inside function bodies).
    # Without this pin the injector silently degrades to a no-op in the
    # frozen build (canonical KPI filter returns False for every match,
    # so zero citations are injected) even though the source is correct.
    'report_source_injector',
    'report_iteration_loop',
    'knowledge_schema',
    'corpus_crypto',
    'corpus_indexer',
    'corpus_retriever',
    'corpus_bootstrap',
    'ask_ai_corpus',
    'report_corpus_context',
    'adoptiq_settings',
    # Round 73 / Phase 1 (F1): every Word writer pulls
    # ``apply_word_footer`` from ``_r68_build_label`` via a lazy
    # ``from _r68_build_label import ...`` inside a try/except block.
    # PyInstaller's static analyser does NOT follow lazy imports inside
    # function bodies, so without this pin the frozen build silently
    # ships every report .docx WITHOUT the v{VER} build {N} stamp -- the
    # entire stale-binary trap detection mechanism (R68 / A1) is
    # unreachable.  Build 46 acceptance found 0/4 docx artifacts carried
    # ``word/footer1.xml``; this pin closes that regression.
    '_r68_build_label',
    # Round 74 / Phase 1 (F1): defense-in-depth post-save footer
    # enforcer.  ``_r74_footer_enforcer`` is lazy-imported inside
    # ``_r74_enforce_footer_safe`` (app_simple.py) so PyInstaller's
    # static analyser does NOT follow it without an explicit pin.
    # Without this pin the frozen build silently regresses to the
    # Build 47 P0 (empty footers across all 4 reports) the moment any
    # writer call site bypasses ``apply_word_footer``.
    '_r74_footer_enforcer',
    # Round 69 / Build 43 + Round 71 / Phase 7 #34: operator-flippable
    # LLM model selection -- model_resolver is imported lazily by both
    # Ask AI and report-narrative paths so PyInstaller's analyser misses
    # it without an explicit pin.  Without this pin the frozen build
    # silently falls back to the platform default model whenever the
    # operator's settings.json says otherwise.
    'model_resolver',
    # Round 84 / Build 60: operator-configurable corpus share URL.
    # corpus_share_url_resolver is imported lazily inside
    # app_simple._r83_safe_share_url() (the same lazy pattern as
    # model_resolver). Without this pin the frozen build's
    # /api/corpus/bootstrap-shortcut endpoint would silently bypass
    # the resolver and read Config.ADOPTIQ_CORPUS_SHARE_URL directly,
    # defeating the operator's settings.json override.
    'corpus_share_url_resolver',
    # Round 79 / Build 55: BE-engineering priority barrier analysis.
    # All four R79 modules are imported lazily inside try blocks in
    # app_simple.py (so a malformed module never blocks report
    # generation). PyInstaller's static analyser does NOT follow
    # lazy imports inside function bodies, so without these pins the
    # frozen build silently ships WITHOUT the BE_Priority_Barriers /
    # BE_Focus_Areas XLSX sheets AND the BE Priority Focus Areas
    # Word section -- defeats the whole feature.
    'be_priority_scorer',
    'be_priority_llm_classifier',
    'be_priority_pipeline',
    'be_priority_word_section',
    # Round 66 / Pass 5 - hybrid retrieval (BM25 + dense + RRF) via
    # fastembed.  fastembed lazy-loads onnxruntime + tokenizers; pin
    # all three so the frozen build can warm the embedder on
    # corpus-bootstrap without a runtime ImportError that silently
    # forces lexical-only retrieval.
    'ask_ai_embeddings',
    'fastembed',
    'fastembed.text',
    'fastembed.text.text_embedding',
    'onnxruntime',
    'tokenizers',
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
