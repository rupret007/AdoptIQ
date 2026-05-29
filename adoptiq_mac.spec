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

    # Round 107 / Build 76: bundle the pre-baked Ask AI corpus so first
    # launch has corpus data immediately. The bake step writes the three
    # artifacts below; the runtime copies them into App Support and opens
    # them with the bundled local sentinel. Runtime refresh can still add
    # generated reports and uploads later.
    baked_dir = os.path.join(root, 'bake')
    for fname in ('corpus.db.enc', 'corpus.db.salt', 'sentinel.json'):
        baked_path = os.path.join(baked_dir, fname)
        if os.path.exists(baked_path):
            datas.append((baked_path, 'Resources/baked_corpus'))

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
    # Round 119 / Build 88: cross-platform auto-update engine (Tier C).
    'auto_updater',
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
    # Round 95 / Build 68: second-stage Ask AI reranker.  The runtime
    # wrapper soft-falls to RRF order, but release bakes hard-fail if
    # the configured cross-encoder model cannot score a known pair.
    'ask_ai_reranker',
    'fastembed',
    'fastembed.text',
    'fastembed.text.text_embedding',
    'fastembed.rerank',
    # Round 98: current fastembed exposes the reranker at
    # fastembed.rerank.cross_encoder.  The runtime wrapper still probes
    # older module names defensively, but pinning a module absent from
    # the installed fastembed release makes PyInstaller emit a noisy
    # non-fatal "Hidden import not found" error during every build.
    'fastembed.rerank.cross_encoder',
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

# Round 89 / F3: SSoT-driven version stamping for Info.plist.  The previous
# spec hard-coded fallbacks of ``"1.0.3"`` / ``"1"`` whenever the
# ``ADOPTIQ_VERSION`` / ``ADOPTIQ_BUILD`` env vars weren't explicitly
# exported into the PyInstaller subprocess -- the same R67-class footgun
# that R67 / Phase 4 fixed in the bash scripts but missed here.  Build 65
# acceptance caught this when the .app's Info.plist showed ``1.0.3 build 1``
# even though every in-app surface (``/api/version``, the R68/A1 build
# label in every report, the R68/A2 restart-required banner) read the
# correct ``1.0.4 / 65`` from ``config.py`` at runtime.
#
# Defense-in-depth: prefer env var (set by build_mac.sh after the R67/Phase4
# read-back from config.py), then read directly from config.py as the SSoT
# fallback.  This mirrors the ``update_version_pc.py`` _VERSION_LINE_RE
# pattern so a future caller invoking PyInstaller without the env vars set
# (e.g. a developer running ``pyinstaller adoptiq_mac.spec`` by hand) still
# gets the right version stamp.
def _r89_resolve_version_from_config():
    try:
        root = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in globals() else os.getcwd()
        config_path = os.path.join(root, 'config.py')
        with open(config_path, encoding='utf-8') as fh:
            text = fh.read()
        import re as _re
        m_v = _re.search(r'ADOPTIQ_VERSION\s*=\s*"([^"]*)"', text)
        m_b = _re.search(r'ADOPTIQ_BUILD\s*=\s*"([^"]*)"', text)
        v = m_v.group(1) if m_v else None
        b = m_b.group(1) if m_b else None
        return v, b
    except Exception:
        return None, None


_R89_CFG_VERSION, _R89_CFG_BUILD = _r89_resolve_version_from_config()
_R89_RESOLVED_VERSION = os.environ.get("ADOPTIQ_VERSION") or _R89_CFG_VERSION or "1.0.3"
_R89_RESOLVED_BUILD = os.environ.get("ADOPTIQ_BUILD") or _R89_CFG_BUILD or "1"

app = BUNDLE(
    coll,
    name='AdoptIQ.app',
    icon=None,
    bundle_identifier='com.cisco.adoptiq',
    info_plist={
        # Ensure the app is a standard foreground app.
        "LSBackgroundOnly": False,
        # Populate version metadata in Finder/About dialogs.
        "CFBundleShortVersionString": _R89_RESOLVED_VERSION,
        "CFBundleVersion": _R89_RESOLVED_BUILD,
        "NSHighResolutionCapable": True,
    },
)
