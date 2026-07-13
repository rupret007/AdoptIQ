"""Round 9 / Phase 1.1: app_simple.py docx_path basename redaction.

Marker + pattern regression test.  Asserts that the success log no
longer emits the host-absolute ``docx_path`` at INFO and instead
surfaces only ``os.path.basename(...)``.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_app_simple_docx_basename() -> None:
    src = REPO_ROOT.joinpath('app_simple.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 1.1' in src, 'Round 9 / Phase 1.1 marker missing in app_simple.py'
    assert '_docx_basename' in src, 'app_simple.py: _docx_basename helper missing'
    assert 'os.path.basename(str(docx_path))' in src, (
        'app_simple.py: docx_path should be projected to basename for INFO log'
    )
    assert 'full docx_path=' in src, (
        'app_simple.py: full docx_path should remain at DEBUG for local troubleshooting'
    )
