"""Round 3 / Phase 1.3 regression test.

Portfolio metrics must not ship a hardcoded ``'Stable'`` trend
direction next to real metrics. Until a comparable prior-period trend
computation is wired in, the field must either be ``None`` or carry a
``trend_direction_reason`` that says it was not computed.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _grep_file_for_pattern(path: Path, pattern: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return pattern in text


def test_app_simple_does_not_ship_literal_stable_trend():
    """``trend_direction`` must not be set to ``'Stable'`` in
    ``app_simple.py``'s portfolio_metrics builder. The fix sets it to
    ``None`` with ``trend_direction_reason='not_computed'``."""
    app_simple = PROJECT_ROOT / "app_simple.py"
    text = app_simple.read_text(encoding="utf-8", errors="ignore")
    assert "'trend_direction': 'Stable'" not in text
    assert '"trend_direction": "Stable"' not in text


def test_adoptiq_backend_does_not_ship_literal_stable_trend():
    backend = PROJECT_ROOT / "adoptiq_backend.py"
    text = backend.read_text(encoding="utf-8", errors="ignore")
    assert "'trend_direction': 'Stable'" not in text
    assert '"trend_direction": "Stable"' not in text


def test_trend_direction_reason_documented_when_not_computed():
    """If trend_direction is None, the metrics dict must explain why
    via ``trend_direction_reason`` so downstream renderers can show
    "not computed" instead of fabricating "Stable"."""
    app_simple = PROJECT_ROOT / "app_simple.py"
    text = app_simple.read_text(encoding="utf-8", errors="ignore")
    assert "'trend_direction_reason'" in text or '"trend_direction_reason"' in text
