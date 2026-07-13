"""Round 9 / Phase 2.3: shared NaN-safe / zero-safe _safe_div helper."""
from __future__ import annotations

import math
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_marker_safe_div_helper() -> None:
    src = REPO_ROOT.joinpath('adoptiq_backend.py').read_text(encoding='utf-8')
    assert 'Round 9 / Phase 2.3' in src, 'Round 9 / Phase 2.3 marker missing in adoptiq_backend.py'
    assert 'def _safe_div(' in src, 'adoptiq_backend.py: _safe_div helper definition missing'


def test_safe_div_handles_zero_denominator() -> None:
    from adoptiq_backend import _safe_div
    assert _safe_div(10, 0) == 0.0
    assert _safe_div(10, 0, default=-1.0) == -1.0


def test_safe_div_handles_nan_inputs() -> None:
    from adoptiq_backend import _safe_div
    assert _safe_div(float('nan'), 5) == 0.0
    assert _safe_div(5, float('nan')) == 0.0
    assert _safe_div(float('inf'), 5) == 0.0
    assert _safe_div(5, float('inf')) == 0.0


def test_safe_div_returns_float() -> None:
    from adoptiq_backend import _safe_div
    result = _safe_div(7, 2)
    assert isinstance(result, float)
    assert math.isclose(result, 3.5)


def test_safe_div_handles_non_numeric() -> None:
    from adoptiq_backend import _safe_div
    assert _safe_div('not-a-number', 5) == 0.0
    assert _safe_div(5, None) == 0.0


def test_safe_div_negative_denominator_returns_default() -> None:
    """A negative denominator is treated as invalid for ratio metrics."""
    from adoptiq_backend import _safe_div
    assert _safe_div(10, -2) == 0.0
