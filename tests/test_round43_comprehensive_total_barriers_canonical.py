"""Round 43 / Phase 1 regression test.

Pin the comprehensive report's hand-rolled ``portfolio_metrics`` dict to use
canonical helpers (``cm.count_total_barriers``, ``cm.count_total_tac``,
``cm.count_bems``) so it agrees with ``report_consistency.validate_report_consistency``
after Round 42 / Phase 1 hardened the validator's own ``ab_count`` to use
``count_total_barriers`` (distinct ID count).

Pre-fix the comprehensive path at ``app_simple.py:12894-12897`` carried:

    portfolio_metrics = {
        ...
        'total_barriers': len(_ab) if not _ab.empty else 0,    # raw rowcount (72)
        'total_cases': len(_cs) if not _cs.empty else 0,       # raw rowcount
        'bems_count': canonical_bems_count,
        ...
    }

The validator's ``ab_count = count_total_barriers(ab_df)`` returns the
distinct ID count (68 for the build-19 demo dataset which had multi-assignee
barriers).  ``len(_ab)`` (72) != ``count_total_barriers(_ab)`` (68) ->
validator raised ``Portfolio metric mismatch: total_barriers does not match
normalized adoption barriers.`` and the comprehensive Word report never
generated.  Source-pinned by the build-19 demo run
``Brian_Frazier_All_Contact_Center_90d_1777428611``.

Round 43 / Phase 1 replaced the three offending values with canonical helpers
operating on the SAME frames the validator sees so the two sides agree by
construction.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_SIMPLE = REPO_ROOT / "app_simple.py"


def _read_app_simple_text() -> str:
    return APP_SIMPLE.read_text(encoding="utf-8")


def test_comprehensive_pm_dict_uses_count_total_barriers_for_total_barriers() -> None:
    """The hand-rolled comprehensive ``portfolio_metrics`` MUST source
    ``total_barriers`` from ``cm.count_total_barriers(...)``, not from
    ``len(_ab)`` -- otherwise the validator (post-Round-42-Phase-1)
    will raise ``Portfolio metric mismatch: total_barriers does not match
    normalized adoption barriers.``.
    """
    src = _read_app_simple_text()
    # Anchor on the Round 43 / Phase 1 marker comment that immediately
    # precedes the comprehensive PM dict; this is unique to the
    # comprehensive site (the renewal / leader / compact paths use
    # ``cm.build_portfolio_metrics`` directly and have no marker).
    idx_block = src.find("Round 43 / Phase 1: canonicalize the three keys")
    assert idx_block > 0, (
        "could not locate the Round 43 / Phase 1 marker comment; either "
        "the marker was removed or the comprehensive PM site moved -- "
        "either way Phase 1 contract may be at risk."
    )
    window = src[idx_block:idx_block + 2000]
    assert re.search(r"""["']total_barriers["']\s*:\s*cm\.count_total_barriers\s*\(""", window), (
        "comprehensive portfolio_metrics must derive 'total_barriers' from "
        "cm.count_total_barriers(_ab) per Round 43 / Phase 1; the legacy "
        "len(_ab) rowcount disagrees with the validator's distinct-ID count "
        "and reintroduces the build-19 demo crash."
    )
    assert not re.search(r"""["']total_barriers["']\s*:\s*len\(_ab\)""", window), (
        "comprehensive portfolio_metrics must NOT use len(_ab) for "
        "'total_barriers' -- this is the regression Round 43 / Phase 1 fixed."
    )


def test_comprehensive_pm_dict_uses_count_total_tac_for_total_cases() -> None:
    """Same contract for ``total_cases`` -> ``cm.count_total_tac(_cs_norm)``."""
    src = _read_app_simple_text()
    idx_block = src.find("Round 43 / Phase 1: canonicalize the three keys")
    assert idx_block > 0
    window = src[idx_block:idx_block + 2000]
    assert re.search(r"""["']total_cases["']\s*:\s*cm\.count_total_tac\s*\(""", window), (
        "comprehensive portfolio_metrics must derive 'total_cases' from "
        "cm.count_total_tac(_cs_norm) per Round 43 / Phase 1."
    )
    assert not re.search(r"""["']total_cases["']\s*:\s*len\(_cs\)""", window), (
        "comprehensive portfolio_metrics must NOT use len(_cs) for 'total_cases'."
    )


def test_comprehensive_pm_dict_uses_count_bems_for_bems_count() -> None:
    """Same contract for ``bems_count`` -> ``cm.count_bems(_cs_norm)``."""
    src = _read_app_simple_text()
    idx_block = src.find("Round 43 / Phase 1: canonicalize the three keys")
    assert idx_block > 0
    window = src[idx_block:idx_block + 2000]
    assert re.search(r"""["']bems_count["']\s*:\s*cm\.count_bems\s*\(""", window), (
        "comprehensive portfolio_metrics must derive 'bems_count' from "
        "cm.count_bems(_cs_norm) per Round 43 / Phase 1."
    )


def test_canonical_helpers_agree_on_multi_assignee_dataset() -> None:
    """Functional check: on a dataset where rowcount != distinct ID count
    (as in the build-19 demo), the canonical helpers and the validator's
    own ``ab_count`` both produce the same number, so the post-fix
    comprehensive path passes validation.
    """
    import pandas as pd
    import canonical_metrics as cm
    from report_consistency import validate_report_consistency

    # Mimic the build-19 demo shape: 68 distinct AB IDs, fanned out per-assignee
    # to 72 rows.  Pre-Round-42-Phase-1 the validator passed (it counted 72
    # rows == portfolio_metrics["total_barriers"] = 72).  Post-Round-42-Phase-1
    # the validator counts 68 distinct IDs, so portfolio_metrics MUST also
    # count 68 -- which it does via Phase 1.
    rows = []
    for i in range(68):
        rows.append({"ID": f"AB-{i:03d}", "customer_name": f"Customer {i % 10}"})
    # Fan four barriers out to a second assignee row each (-> 72 total rows).
    for i in range(4):
        rows.append({"ID": f"AB-{i:03d}", "customer_name": f"Customer {i % 10}"})
    ab_df = pd.DataFrame(rows)
    csone_df = pd.DataFrame()
    assert len(ab_df) == 72, "fixture sanity: 72 rows"
    assert cm.count_total_barriers(ab_df) == 68, "fixture sanity: 68 distinct IDs"

    portfolio_metrics = {
        "total_customers": 10,
        "total_barriers": cm.count_total_barriers(ab_df),  # Round 43 / Phase 1
        "total_cases": cm.count_total_tac(csone_df),
        "bems_count": cm.count_bems(csone_df),
    }
    result = validate_report_consistency(
        ab_df,
        csone_df,
        portfolio_metrics=portfolio_metrics,
        customer_universe=[f"Customer {i}" for i in range(10)],
    )
    # Must NOT contain the build-19 demo error.
    for err in (result.get("errors", []) or []):
        assert "total_barriers" not in err, (
            "Round 43 / Phase 1 contract violated: validator still raised "
            f"a total_barriers mismatch ({err!r})."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
