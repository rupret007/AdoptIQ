# -*- mode: python ; coding: utf-8 -*-
# AdoptIQ PC build spec — run on Windows: pyinstaller adoptiq_pc.spec
# Produces dist/AdoptIQ.exe. Use build_pc.bat to automate build and package.

import sys
import os

block_cipher = None


DEVELOPER_ONLY = str(os.environ.get('ADOPTIQ_DEVELOPER_ONLY', '')).strip().lower() in {
    '1', 'true', 'yes', 'on'
}


# Data files to include in the bundle (extracted to sys._MEIPASS at runtime)
def _datas():
    root = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in globals() else os.getcwd()
    datas = [
        (os.path.join(root, 'team_config.json'), '.'),
        (os.path.join(root, 'customer_aliases.defaults.json'), '.'),
        (os.path.join(root, 'templates'), 'templates'),
        (os.path.join(root, 'static'), 'static'),
    ]
    # Snowflake connector needs certifi CA bundle in _MEIPASS (fixes "No cabundle file" error)
    try:
        import certifi
        cert_path = certifi.where()
        if cert_path and os.path.exists(cert_path):
            datas.append((cert_path, 'certifi'))
    except Exception:
        pass

    # Round 66 / Pass 5 - bundle the fastembed model cache (mirrors
    # adoptiq_mac.spec).  The ONNX INT8 model is ~33 MB and lets the
    # first Ask AI query in a fresh install skip the HuggingFace
    # download entirely (corporate Windows installs rarely have egress
    # to huggingface.co without explicit allow-listing).
    if DEVELOPER_ONLY:
        marker_dir = os.path.join(root, 'build', 'developer-only-marker')
        os.makedirs(marker_dir, exist_ok=True)
        marker_path = os.path.join(marker_dir, 'DEVELOPER_ONLY_BUILD.txt')
        with open(marker_path, 'w', encoding='utf-8') as marker:
            marker.write(
                'Developer-only AdoptIQ candidate. No bundled credentials or prebaked corpus. '
                'Not production-ready.\n'
            )
        datas.append((marker_path, '.'))
    else:
        embeddings_dir = os.path.join(root, 'embeddings')
        if os.path.isdir(embeddings_dir):
            datas.append((embeddings_dir, 'Resources/embeddings'))
    return datas

# Local Python modules that may be imported directly or dynamically
# Round 71 / Phase 7 (#34): align with adoptiq_mac.spec so the PC build
# carries the same lazy-imported corpus / Round-57 / Round-59 / Round-69
# modules that the Mac build pins.  Pre-R71 the PC spec was missing
# error_classifier, connectivity_diagnostics, ai_narrative_validator,
# report_source_injector, report_iteration_loop, knowledge_schema,
# corpus_crypto/indexer/retriever/bootstrap, ask_ai_corpus,
# report_corpus_context, adoptiq_settings -- all of which are imported
# lazily inside function bodies so PyInstaller's static analyser misses
# them, and the Windows build silently degrades (no source citations,
# no narrative validation, no knowledge corpus on first launch).  We
# also pin model_resolver + adoptiq_settings (R69 / Build 43 LLM model
# selection plumbing) on BOTH spec files since they too are loaded
# lazily by app_simple._read_env_value-style fallbacks.
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
    # Round 59 / Build32 + Round 71 / Phase 7 #34 parity with mac spec --
    # see adoptiq_mac.spec for the multi-line rationale.  Pinning these
    # avoids the silent-noop degradation in frozen builds.
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
    # Without this pin the frozen build silently ships every report
    # .docx WITHOUT the v{VER} build {N} stamp.  See adoptiq_mac.spec
    # for the full rationale.
    '_r68_build_label',
    # Round 74 / Phase 1 (F1): defense-in-depth post-save footer
    # enforcer.  Lazy-imported by ``_r74_enforce_footer_safe``
    # (app_simple.py); without this pin the frozen build silently
    # regresses to the Build 47 P0 (empty footers across all 4 reports).
    # See adoptiq_mac.spec for the full rationale.
    '_r74_footer_enforcer',
    # Round 69 / Build 43: operator-flippable LLM model selection -- the
    # resolver is imported lazily by both Ask AI and report-narrative
    # paths so PyInstaller's analyser misses it without an explicit pin.
    'model_resolver',
    # Round 84 / Build 60: operator-configurable corpus share URL.
    # Lazy-imported by app_simple, so the frozen build needs an explicit pin.
    'corpus_share_url_resolver',
    # Round 79 / Build 55: BE-engineering priority barrier analysis.
    # All four R79 modules are imported lazily inside try blocks in
    # app_simple.py.  Without these pins the frozen Windows build
    # silently ships WITHOUT the BE_Priority_Barriers / BE_Focus_Areas
    # XLSX sheets AND the BE Priority Focus Areas Word section.
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
    'ask_ai_reranker',
    'fastembed',
    'fastembed.text',
    'fastembed.text.text_embedding',
    'fastembed.rerank',
    'fastembed.rerank.cross_encoder',
    'onnxruntime',
    'tokenizers',
    # Report charts must be present in the Windows frozen build too.  Excluding
    # these packages produced text-only reports even when chart data existed.
    'matplotlib', 'matplotlib.pyplot', 'matplotlib.backends',
    'matplotlib.backends.backend_agg',
    'PIL', 'PIL.Image', 'PIL.PngImagePlugin', 'PIL.JpegImagePlugin',
]

if DEVELOPER_ONLY:
    hidden_imports = [name for name in hidden_imports if name != '_bundled_secrets']

analysis_excludes = [
    'tkinter', 'playwright',
]
if DEVELOPER_ONLY:
    analysis_excludes.append('_bundled_secrets')

a = Analysis(
    ['app_simple.py'],
    pathex=[],
    binaries=[],
    datas=_datas(),
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=analysis_excludes,
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
    name='AdoptIQ',
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
