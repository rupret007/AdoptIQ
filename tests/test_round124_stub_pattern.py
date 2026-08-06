"""Round 124 / F11: widen _R78_STUB_RE for trailing [Source:...] + Pattern N.

Build 92 audit found "Pattern N: Data Unavailable [Source: ...]" stub bullets
leaking into the Comprehensive DOCX because ``_R78_STUB_RE`` was ``$``-anchored
and did not tolerate the trailing citation the R57 inline-source injector
appends.  The regex now drops those stubs while strictly preserving any
substantive ``Pattern N: <real text>`` bullet.

Made-with: Cursor.
"""

from source_shape_utils import assert_in_source
import adoptiq_backend as ab


# ---------------------------------------------------------------------------
# Positive controls -- MUST be dropped (regex matches the whole bullet)
# ---------------------------------------------------------------------------

DROP_CASES = [
    "Pattern 1: Data Unavailable [Source: AdoptIQ Report Data Sources]",
    "Pattern 12: Data unavailable [Source: Snowflake CSConsole]",
    "**Pattern 3:** Data Unavailable [Source: X]",
    "Pattern 4: Data Unavailable.",
    "Pattern 5: Data unavailable",
    "Industry Benchmarking: Data unavailable. [Source: AdoptIQ Report Data Sources]",
    "Industry Benchmarking: Data unavailable.",  # original R78 stub still drops
]


def test_stub_pattern_drops_data_unavailable_with_trailing_source():
    for text in DROP_CASES:
        assert ab._R78_STUB_RE.match(text), f"expected DROP but kept: {text!r}"


# ---------------------------------------------------------------------------
# Negative controls -- MUST be preserved
# ---------------------------------------------------------------------------

KEEP_CASES = [
    "Pattern 1: Customers report recurring login failures impacting adoption.",
    "Pattern 2: Data unavailable for ARR, but 4 P1 cases remain open.",
    "Pattern 3: Adoption velocity stalled across 3 BUs [Source: X].",
    "Operational Disruption: Data unavailable. No active incidents.",
    "Strategic Headwinds: signal (SP-ID: data unavailable) suggests churn risk.",
    "Financial Impact: Data unavailable regarding ARR but renewal in 90 days.",
]


def test_stub_pattern_preserves_substantive_bullets():
    for text in KEEP_CASES:
        assert not ab._R78_STUB_RE.match(text), f"expected KEEP but dropped: {text!r}"


def test_source_marker_present():
    import inspect

    src = inspect.getsource(ab)
    assert_in_source(src, "Round 124 / F11", label='src')
