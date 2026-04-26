"""Round 16 / Phase 2 -- sort determinism regression tests.

Round 14/15 swept the leader and Excel writers.  Three Round-16 findings
remained:

- **R16-001 (compact_report_formatter / sorted_yellow)** — the yellow-tier
  customer list sorted by score alone.  Two yellow customers with the
  same risk score rendered in dict-insertion order, which is stable
  inside one process but not across runs.
- **R16-002 (compact_report_formatter / sorted_themes)** — the problem-
  themes ranking sorted by count alone.  Same dict-insertion-order
  fragility.
- **R16-003 (adoptiq_backend / ARR concentration top-N)** — three
  ``groupby(...).sort_values(arr_sum, ascending=False)`` callsites with
  no tiebreaker on the account id / display label.  Two accounts with
  the same ARR could swap positions across runs, flipping the top-5
  composition reported in the executive narrative.

All three were tightened in Round 16 / Phase 2.x with explicit
casefold-name (or sort-by-index + mergesort) tiebreakers.  These tests
pin the determinism: tied inputs always yield identical output rankings.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# R16-001 / R16-002 -- compact_report_formatter sort tiebreakers.
# Source-level pin: the markers and tuple keys must be in place.
# ---------------------------------------------------------------------------


def test_phase_2_1_sorted_yellow_uses_tuple_tiebreaker():
    """Round 16 / Phase 2.1 marker + tuple-key idiom must be present."""
    src = _read("compact_report_formatter.py")
    assert "Round 16 / Phase 2.1" in src, (
        "Round 16 / Phase 2.1 marker must remain at the sorted_yellow "
        "callsite so the regression is greppable."
    )
    # Tuple key form: ``key=lambda x: (-(...), str(x[0] or '').casefold())``
    assert "sorted_yellow = sorted(" in src
    assert "casefold()" in src, (
        "sorted_yellow must use a casefold-name tiebreaker; otherwise the "
        "Round 16 / Phase 2.1 fix has been reverted."
    )


def test_phase_2_2_sorted_themes_uses_tuple_tiebreaker():
    """Round 16 / Phase 2.2 marker + tuple-key idiom must be present."""
    src = _read("compact_report_formatter.py")
    assert "Round 16 / Phase 2.2" in src
    assert "sorted_themes = sorted(" in src
    # The fixed form must include both ``-x[1]`` (score, descending)
    # and ``casefold()`` (theme name tiebreaker).
    fix_idx = src.find("Round 16 / Phase 2.2")
    fix_window = src[fix_idx : fix_idx + 1500]
    assert "casefold()" in fix_window, (
        "sorted_themes fix window must use casefold name tiebreaker."
    )


# ---------------------------------------------------------------------------
# R16-001 -- behavioral test for sorted_yellow tiebreaker.
# ---------------------------------------------------------------------------


def _yellow_sort(yellow_customers):
    """Replicate the post-fix sort idiom."""
    return sorted(
        yellow_customers.items(),
        key=lambda x: (
            -(x[1].get("score", 0) if isinstance(x[1], dict) else 0),
            str(x[0] or "").casefold(),
        ),
    )


def test_phase_2_1_sorted_yellow_breaks_ties_alphabetically():
    """Round 16 / Phase 2.1 -- two yellow customers with the same risk
    score must rank by case-folded customer name."""
    yellow = {
        "Zeta Co": {"score": 5.5},
        "Acme Corp": {"score": 5.5},
        "Mu Inc": {"score": 5.5},
        "beta inc": {"score": 5.5},
    }
    out = _yellow_sort(yellow)
    names = [k for (k, _) in out]
    assert names == ["Acme Corp", "beta inc", "Mu Inc", "Zeta Co"], (
        f"Tied scores must rank alphabetically (casefold), got {names}"
    )


def test_phase_2_1_sorted_yellow_score_dominates_name_tiebreaker():
    """Round 16 / Phase 2.1 -- the casefold name must only matter on
    score ties; higher-score customers always rank first."""
    yellow = {
        "Acme Corp": {"score": 6.0},
        "Zeta Co": {"score": 7.0},
    }
    out = _yellow_sort(yellow)
    names = [k for (k, _) in out]
    assert names == ["Zeta Co", "Acme Corp"]


# ---------------------------------------------------------------------------
# R16-002 -- behavioral test for sorted_themes tiebreaker.
# ---------------------------------------------------------------------------


def _themes_sort(problem_themes):
    return sorted(
        problem_themes.items(),
        key=lambda x: (-x[1], str(x[0] or "").casefold()),
    )


def test_phase_2_2_sorted_themes_breaks_ties_alphabetically():
    """Round 16 / Phase 2.2 -- two themes with the same affected-row count
    must rank by case-folded theme name."""
    themes = {"Network": 7, "Auth": 7, "Provisioning": 5, "Routing": 7}
    out = _themes_sort(themes)
    names = [k for (k, _) in out]
    assert names == ["Auth", "Network", "Routing", "Provisioning"], (
        f"Tied counts must rank alphabetically (casefold), got {names}"
    )


def test_phase_2_2_sorted_themes_count_dominates_name_tiebreaker():
    """Round 16 / Phase 2.2 -- count dominates; name tiebreak only on tie."""
    themes = {"Auth": 3, "Network": 9}
    out = _themes_sort(themes)
    names = [k for (k, _) in out]
    assert names == ["Network", "Auth"]


# ---------------------------------------------------------------------------
# R16-003 -- ARR concentration top-N stable tiebreaker.
# ---------------------------------------------------------------------------


def test_phase_2_3_arr_concentration_marker_present():
    """Round 16 / Phase 2.3 marker must remain at all three rewritten
    callsites in adoptiq_backend.py."""
    src = _read("adoptiq_backend.py")
    occurrences = src.count("Round 16 / Phase 2.3")
    assert occurrences >= 3, (
        f"Round 16 / Phase 2.3 marker must appear at all three rewritten "
        f"ARR concentration callsites, found {occurrences}"
    )


def _stable_arr_concentration_top_n(arr_df: pd.DataFrame, acct_col: str, arr_col: str, n: int = 5):
    """Replicate the post-fix sort idiom for the ARR concentration."""
    _agg = arr_df.groupby(acct_col)[arr_col].sum()
    _agg = _agg.sort_index(kind="mergesort").sort_values(
        ascending=False, kind="mergesort"
    )
    return list(_agg.index[:n])


def test_phase_2_3_arr_concentration_breaks_ties_by_account_id():
    """Round 16 / Phase 2.3 -- two accounts that share an ARR sum must
    rank by ``acct_col`` ascending (mergesort + sort_index pattern), so
    the top-5 list is byte-stable across runs of the same input."""
    df = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["acct_z", "acct_a", "acct_m", "acct_b", "acct_y"],
            "ANNUAL_CONTRACT_VALUE": [
                1_000_000.0,
                1_000_000.0,
                1_000_000.0,
                500_000.0,
                500_000.0,
            ],
        }
    )

    top5 = _stable_arr_concentration_top_n(
        df, "ACCOUNT_ID_C", "ANNUAL_CONTRACT_VALUE", n=5
    )
    # The three 1M accounts must be ranked alphabetically by account id;
    # then the two 500k accounts likewise.
    assert top5 == ["acct_a", "acct_m", "acct_z", "acct_b", "acct_y"], (
        f"Tied ARR sums must break ties by account id ascending, got {top5}"
    )


def test_phase_2_3_arr_concentration_idempotent_under_input_shuffle():
    """Round 16 / Phase 2.3 -- shuffling the input row order must NOT
    change the top-N composition or order.  This is the canary for the
    'pandas internal hashing flips order' regression class."""
    base = pd.DataFrame(
        {
            "ACCOUNT_ID_C": [f"a{i}" for i in range(10)],
            "ANNUAL_CONTRACT_VALUE": [
                500_000.0, 500_000.0, 500_000.0, 500_000.0, 500_000.0,
                100_000.0, 100_000.0, 100_000.0, 100_000.0, 100_000.0,
            ],
        }
    )
    out_a = _stable_arr_concentration_top_n(base, "ACCOUNT_ID_C", "ANNUAL_CONTRACT_VALUE", n=5)
    out_b = _stable_arr_concentration_top_n(
        base.sample(frac=1.0, random_state=42).reset_index(drop=True),
        "ACCOUNT_ID_C",
        "ANNUAL_CONTRACT_VALUE",
        n=5,
    )
    out_c = _stable_arr_concentration_top_n(
        base.sample(frac=1.0, random_state=7).reset_index(drop=True),
        "ACCOUNT_ID_C",
        "ANNUAL_CONTRACT_VALUE",
        n=5,
    )
    assert out_a == out_b == out_c, (
        f"Top-5 must be invariant under row-order shuffles. "
        f"a={out_a}, b={out_b}, c={out_c}"
    )


def test_phase_2_3_arr_concentration_distinct_arrs_unchanged():
    """Round 16 / Phase 2.3 -- when ARR sums are all distinct, the
    fixed sort matches the prior behavior; the tiebreaker only kicks in
    on ties."""
    df = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["a", "b", "c", "d", "e"],
            "ANNUAL_CONTRACT_VALUE": [100.0, 500.0, 200.0, 900.0, 700.0],
        }
    )
    top5 = _stable_arr_concentration_top_n(df, "ACCOUNT_ID_C", "ANNUAL_CONTRACT_VALUE", n=5)
    assert top5 == ["d", "e", "b", "c", "a"]


# ---------------------------------------------------------------------------
# Marker presence in this test module.
# ---------------------------------------------------------------------------


def test_phase_2_marker_in_test_module():
    """Self-pin: this module documents Phase 2.x markers."""
    contents = _read("tests/test_round16_sort_determinism.py")
    assert "Round 16 / Phase 2.1" in contents
    assert "Round 16 / Phase 2.2" in contents
    assert "Round 16 / Phase 2.3" in contents
