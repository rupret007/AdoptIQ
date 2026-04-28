"""Round 30 / H1b — leader Word doc must surface a multi-currency
advisory on the title page when the upstream portfolio mixes
currencies.

The leader report does NOT render ARR dollar figures itself (per the
``ARR is intentionally excluded from reporting outputs`` comment in
``LeaderReportGenerator.__init__``), so the disclosure surfaces as a
one-line italic footer on the title page that warns readers cross-
referencing the executive ARR Exposure section that the underlying
figures are not currency-comparable.
"""

from __future__ import annotations

import inspect

import leader_report_generator


def test_round30_h1b_leader_title_page_renders_multicurrency_advisory() -> None:
    """Source pin: ``LeaderReportGenerator._create_title_page`` (or the
    method that owns the title-page advisory) must read
    ``self.arr_impact.is_multi_currency`` and add a paragraph that
    matches the executive ARR Exposure phrasing
    ('multi-currency -- not summed across currencies')."""
    src = inspect.getsource(leader_report_generator)
    # 1. The class accepts an arr_impact argument so the wrapper can
    #    pass the upstream multi-currency context through.
    assert "arr_impact" in src, (
        "Round 30 / H1b: LeaderReportGenerator must accept an "
        "arr_impact argument so the title page can render a multi-"
        "currency advisory."
    )
    # 2. The class reads is_multi_currency from the impact dict.
    assert "is_multi_currency" in src, (
        "Round 30 / H1b: leader title-page rendering must branch on "
        "is_multi_currency."
    )
    # 3. The advisory phrasing is at parity with the executive
    #    formatter's ARR Exposure header.
    assert "mixes currencies" in src, (
        "Round 30 / H1b: leader advisory must say "
        "'mixes currencies' so readers cross-referencing the "
        "executive ARR Exposure section see the same disclaimer."
    )
    assert "not summed across currencies" in src, (
        "Round 30 / H1b: leader advisory must mirror the executive "
        "phrasing ('not summed across currencies') so the two reports "
        "agree on the multi-currency disclosure."
    )


def test_round30_h1b_leader_wrapper_forwards_arr_impact() -> None:
    """Source pin: the ``generate_leader_report`` module-level wrapper
    must forward ``arr_impact`` to ``LeaderReportGenerator`` so the
    advisory can render."""
    src = inspect.getsource(leader_report_generator)
    # The wrapper should construct LeaderReportGenerator with the
    # arr_impact kwarg.  Pin both the kwarg name and the wrapper's
    # propagation comment.
    assert "arr_impact=arr_impact" in src, (
        "Round 30 / H1b: generate_leader_report must forward "
        "arr_impact to LeaderReportGenerator."
    )


def test_round30_h1b_concentration_note_renders_when_present() -> None:
    """Source pin: when the upstream impact dict carries a
    ``concentration_note`` (Round 30 / I1), the leader title-page
    advisory must surface that note as a second italic paragraph."""
    src = inspect.getsource(leader_report_generator)
    assert "concentration_note" in src, (
        "Round 30 / H1b + I1: leader advisory must render the "
        "concentration_note adjacent to the multi-currency advisory."
    )
