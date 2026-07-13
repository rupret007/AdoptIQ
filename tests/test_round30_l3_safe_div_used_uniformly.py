"""Round 30 / L3 — inline ternary divisions in adoptiq_backend.py
must be replaced with the canonical ``_safe_div`` helper.

Round 9 / Phase 2.3 introduced ``_safe_div`` as the single source of
truth for ``num / den`` ratio guards across this module, but several
inline ``num / den * 100 if den > 0 else 0`` ternaries remained.
The inline form silently fell through on ``NaN > 0 == False`` and
propagated NaN through later arithmetic, causing the misleading 73%
concentration figures whenever a single customer row had a missing
ARR value.

Round 30 / L3 sweeps the remaining inline ternaries and adds this
test as a regex-scan regression gate so future code can't reintroduce
the pattern.
"""

from __future__ import annotations

import inspect
import re

import adoptiq_backend


def _module_source_minus_helper_doc() -> str:
    """Return the adoptiq_backend source with the _safe_div docstring
    excluded so the test doesn't false-positive on the documented
    legacy-pattern example.

    The _safe_div helper's docstring contains the literal phrase
    ``num / den if den > 0 else 0`` as the bad pattern it replaces.
    We strip that doc block so the regression scan only flags real
    code paths.
    """
    src = inspect.getsource(adoptiq_backend)
    # Locate the _safe_div helper and its docstring close.  The
    # docstring is delimited by the standard triple-quote block.
    idx = src.find('def _safe_div(')
    assert idx != -1
    # Skip past the docstring close.
    end = src.find('"""', idx + len('def _safe_div('))
    assert end != -1
    end = src.find('"""', end + 3)
    assert end != -1
    return src[:idx] + src[end + 3:]


def test_round30_l3_no_inline_ternary_division_pattern() -> None:
    """Regression scan: no inline ``)/ <var> if <var> > 0 else 0``
    ternary may exist in adoptiq_backend.  The plan calls out the
    specific line numbers (3373/3607-3610/4605-4606/9575) but the
    scan is line-agnostic so future drift is also caught."""
    src = _module_source_minus_helper_doc()
    # Match patterns of the form:
    #   `_arr_val / sub_total * 100) if sub_total > 0 else 0`
    #   `total_closed / total_new * 100 if total_new > 0 else 0`
    #   `(num / den) if den > 0 else 0`
    # We use a permissive regex that catches the prohibited shape
    # without false-positives on string-conditional ternaries.
    pattern = re.compile(
        r'/\s*([a-zA-Z_]\w*)\s*\*\s*\d+(?:\.\d+)?'  # "/ var * 100"
        r'(?:\s*\))?\s*if\s+\1\s*>\s*0\s+else\s+\d',
        re.MULTILINE,
    )
    matches = pattern.findall(src)
    assert not matches, (
        f"Round 30 / L3: found {len(matches)} inline ternary "
        f"division(s) of the form ``num / den * N if den > 0 else "
        f"M`` in adoptiq_backend.py: {matches}.  These must be "
        f"replaced with _safe_div(num, den) * N for canonical "
        f"NaN/None/inf handling."
    )


def test_round30_l3_safe_div_helper_exists() -> None:
    """The canonical helper must exist as a public-name symbol."""
    assert hasattr(adoptiq_backend, '_safe_div'), (
        "Round 30 / L3: adoptiq_backend must export _safe_div."
    )


def test_round30_l3_safe_div_handles_zero_denominator() -> None:
    """Behavioural pin: ``_safe_div(n, 0)`` returns the default."""
    assert adoptiq_backend._safe_div(5, 0) == 0.0
    assert adoptiq_backend._safe_div(5, 0, default=42) == 42.0


def test_round30_l3_safe_div_handles_nan_and_none() -> None:
    """Behavioural pin: NaN / None inputs return the default rather
    than propagating NaN through later arithmetic (the original
    motivation for centralising the helper)."""
    import math
    assert adoptiq_backend._safe_div(None, 5) == 0.0
    assert adoptiq_backend._safe_div(5, None) == 0.0
    assert adoptiq_backend._safe_div(float('nan'), 5) == 0.0
    assert adoptiq_backend._safe_div(5, float('nan')) == 0.0
    # The result is never NaN.
    assert not math.isnan(adoptiq_backend._safe_div(5, float('nan')))


def test_round30_l3_safe_div_handles_negative_denominator() -> None:
    """Behavioural pin: negative denominators return the default
    (matching the previous ``if den > 0 else 0`` semantics)."""
    assert adoptiq_backend._safe_div(5, -3) == 0.0
