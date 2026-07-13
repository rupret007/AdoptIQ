"""Round 9 / Phase 6.4: executive_intelligence_formatter.py save_path basename at INFO."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_marker_exec_intel_save_path_basename() -> None:
    src = REPO_ROOT.joinpath('executive_intelligence_formatter.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 6.4' in src, (
        'Round 9 / Phase 6.4 marker missing in executive_intelligence_formatter.py'
    )
    assert '_save_basename' in src, (
        'executive_intelligence_formatter.py: _save_basename helper missing'
    )
    assert 'os.path.basename' in src or 'os_p64.path.basename' in src or '_os_p64.path.basename' in src
