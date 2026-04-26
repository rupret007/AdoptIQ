"""Round 9 / Phase 1.3: download_result error JSON projects analysis_id to digest."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_download_result_id_digest() -> None:
    src = REPO_ROOT.joinpath('app_simple.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 1.3' in src, 'Round 9 / Phase 1.3 marker missing in app_simple.py'
    assert '_aid_digest' in src, 'app_simple.py: _aid_digest helper missing'
