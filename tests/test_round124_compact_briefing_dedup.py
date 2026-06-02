"""Round 124 / F5 regression test.

The Compact briefing builder (`_create_executive_briefing_book_with_csone`)
must dedup TAC case rows on the case-id column (matching the Word dashboard's
`_r118_dedup_tac_cases`) BEFORE computing the "Total Support Cases" / BEMS
totals, so the LLM-cited numbers match the dashboard tile (e.g. 343 not 369).
"""

import re

import pandas as pd

import adoptiq_backend as ab


def _csone_with_dupes():
    # 5 raw rows, 3 distinct Case # -> dashboard-deduped total is 3.
    return pd.DataFrame(
        {
            "Case #": ["C1", "C1", "C2", "C3", "C3"],
            "customer_name": ["ACME", "ACME", "BETA", "GAMMA", "GAMMA"],
            "Priority": ["P1", "P1", "P3", "P2", "P2"],
        }
    )


def _total_support_cases(briefing_text):
    m = re.search(r"\*\*Total Support Cases:\*\*\s*(\d+)", briefing_text)
    assert m, "Total Support Cases line not found in briefing"
    return int(m.group(1))


def test_briefing_total_support_cases_is_deduped():
    df = _csone_with_dupes()
    briefing = ab._create_executive_briefing_book_with_csone(
        "Test Manager",
        pd.DataFrame(),  # ab_norm
        df,
        pd.DataFrame(),  # team_subs_df
        "All Contact Center",
    )
    # 3 distinct Case # values, not the 5 raw rows.
    assert _total_support_cases(briefing) == 3


def test_briefing_no_dupe_column_falls_back_to_rowcount():
    # No recognised case-id column -> dedup is a no-op, total == raw rows.
    df = pd.DataFrame(
        {
            "customer_name": ["ACME", "ACME", "BETA"],
            "Priority": ["P1", "P2", "P3"],
        }
    )
    briefing = ab._create_executive_briefing_book_with_csone(
        "Test Manager", pd.DataFrame(), df, pd.DataFrame(), "All Contact Center",
    )
    assert _total_support_cases(briefing) == 3


def test_briefing_emits_canonical_risk_bands_block_when_supplied():
    block = (
        "### Canonical Risk Bands (authoritative -- cite these)\n"
        "- **Portfolio Health:** A"
    )
    briefing = ab._create_executive_briefing_book_with_csone(
        "Test Manager",
        pd.DataFrame(),
        _csone_with_dupes(),
        pd.DataFrame(),
        "All Contact Center",
        canonical_risk_bands=block,
    )
    assert "Canonical Risk Bands" in briefing
    assert "Portfolio Health:** A" in briefing
