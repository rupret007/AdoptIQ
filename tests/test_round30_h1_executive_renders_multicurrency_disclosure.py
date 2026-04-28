"""Round 30 / H1 (executive parity) — executive ARR Exposure must
render a per-currency breakdown when the upstream portfolio mixes
currencies.

Compact does not render ARR totals at all (verified via grep:
compact_report_formatter.py contains zero references to "currency",
"ARR Exposure", or "ANNUAL_CONTRACT_VALUE"), so the H1 work for the
non-leader surfaces lands in the executive intelligence formatter.
This test pins the per-currency rendering shape so a future refactor
cannot silently collapse to a single-line headline.
"""

from __future__ import annotations

import inspect

import executive_intelligence_formatter


def test_round30_h1_executive_arr_exposure_branches_on_is_multi_currency() -> None:
    """Source pin: the ARR Exposure section in
    ``executive_intelligence_formatter`` MUST branch on
    ``is_multi_currency`` and render per-currency totals (not a
    single-line USD headline) when the flag is set."""
    src = inspect.getsource(executive_intelligence_formatter)
    assert "is_multi_currency" in src, (
        "Round 30 / H1: executive ARR Exposure must branch on "
        "is_multi_currency."
    )
    assert "multi-currency -- not summed across currencies" in src, (
        "Round 30 / H1: executive ARR Exposure must render the "
        "canonical multi-currency disclosure phrase so the leader / "
        "compact reports can match it byte-for-byte."
    )
    # Pin the per-currency breakdown shape.  The executive renders a
    # bullet list of ``  {ccy_lbl}: {amt:,.2f}`` rows.
    assert "arr_by_currency" in src or "totals_by_currency" in src, (
        "Round 30 / H1: executive must enumerate ARR totals by "
        "currency (arr_by_currency or totals_by_currency)."
    )


def test_round30_h1_executive_arr_exposure_calls_assert_arr_attrs() -> None:
    """Source pin: the executive ARR Exposure section must call
    ``_assert_arr_attrs`` (Round 30 / M5) at the entry of the section
    so the multi-currency contract is enforced before the disclosure
    branch runs."""
    src = inspect.getsource(executive_intelligence_formatter)
    assert "_assert_arr_attrs" in src, (
        "Round 30 / M5: executive ARR Exposure must guard the "
        "arr_data attrs contract via _assert_arr_attrs."
    )


def test_round30_h1_compact_does_not_silently_render_mixed_currencies() -> None:
    """Negative source pin: compact does NOT render ARR totals, so it
    cannot silently mislabel a multi-currency portfolio as USD.
    Pin that contract so a future addition of an ARR section in
    compact must also add the multi-currency branch."""
    import compact_report_formatter
    src = inspect.getsource(compact_report_formatter)
    # The compact formatter must NOT contain a hardcoded single-line
    # ARR headline that adds totals across rows.  We grep for the
    # patterns the executive uses internally; any of these appearing
    # in compact would indicate an ARR section was added without
    # multi-currency awareness.
    bad_patterns = [
        "Total Portfolio ARR:",
        "Total ARR:",
        "ANNUAL_CONTRACT_VALUE",
    ]
    found = [p for p in bad_patterns if p in src]
    if found:
        # If any ARR rendering surfaces, it must include the
        # multi-currency disclosure too.  This is the post-fix
        # contract for compact ARR rendering.
        for p in ("is_multi_currency", "multi-currency"):
            assert p in src, (
                f"Round 30 / H1: compact contains ARR-rendering "
                f"pattern {found!r} without the {p!r} disclosure.  "
                f"Adding ARR totals to compact requires the same "
                f"multi-currency branch the executive carries."
            )
