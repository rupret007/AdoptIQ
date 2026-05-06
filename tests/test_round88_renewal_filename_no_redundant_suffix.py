"""Round 88 / F3 — Renewal docx filename drops redundant ``_Renewal_Report`` suffix.

The Build 63 acceptance audit observed Renewal docx artifacts named
``AdoptIQ_Report_Renewal_<tag>_Renewal_Report.docx``.  The
``_Renewal_Report`` suffix is redundant: the caller in
``run_customer_renewal_analysis`` (L14544) already constructs
``base = AdoptIQ_Report_Renewal_<tag>`` so the writer's
``f"{base_path}_Renewal_Report.docx"`` line produced the doubled
``Renewal`` token.

The fix aligns Renewal with Compact (L9994: ``f"{base}.docx"``) and
Comprehensive (L18007: ``f"{base}.docx"``) by saving as
``f"{base_path}.docx"``.

Source-shape pin (``app_simple.py``):
- ``word_path = f"{base_path}.docx"`` is the new line.
- ``f"{base_path}_Renewal_Report.docx"`` MUST NOT appear (negative).

Behavior pin (round-trip):
- ``_create_simple_renewal_report(base_path=<tmp>/foo, ...)`` returns
  ``<tmp>/foo.docx`` (NOT ``<tmp>/foo_Renewal_Report.docx``).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PATH = _REPO_ROOT / "app_simple.py"


def test_r88_f3_writer_uses_clean_basename_save_pattern() -> None:
    """``_create_simple_renewal_report`` MUST save to
    ``f"{base_path}.docx"`` (clean) — not the legacy
    ``f"{base_path}_Renewal_Report.docx"`` (redundant).
    """

    text = _APP_PATH.read_text(encoding="utf-8")
    # Positive: the new clean save line is present
    assert 'word_path = f"{base_path}.docx"' in text, (
        "Round 88 / F3: ``_create_simple_renewal_report`` must save to "
        "``f\"{base_path}.docx\"`` (clean) so the redundant ``_Renewal_Report`` "
        "suffix that pre-R88 produced ``..._Renewal_Report.docx`` is gone."
    )


def test_r88_f3_legacy_redundant_suffix_pattern_is_removed() -> None:
    """The pre-R88 redundant suffix pattern MUST NOT appear in
    ``app_simple.py``.  This is the negative pin so a future refactor
    cannot accidentally re-introduce the bug.
    """

    text = _APP_PATH.read_text(encoding="utf-8")
    # Negative: the legacy redundant suffix is gone
    assert 'word_path = f"{base_path}_Renewal_Report.docx"' not in text, (
        "Round 88 / F3: the legacy redundant-suffix line "
        "``word_path = f\"{base_path}_Renewal_Report.docx\"`` must be removed "
        "from app_simple.py — the new clean ``{base_path}.docx`` line is the "
        "SSoT."
    )


def test_r88_f3_round_trip_renewal_single_customer_no_redundant_suffix() -> None:
    """End-to-end: call ``_create_simple_renewal_report`` with a
    synthetic ``base_path`` and assert the returned path is
    ``<base_path>.docx`` — NOT the pre-R88 redundant
    ``<base_path>_Renewal_Report.docx``.
    """

    pytest.importorskip("docx", reason="python-docx required for round-trip test")

    from app_simple import (  # noqa: PLC0415
        _calculate_simple_renewal_risk,
        _create_simple_renewal_report,
    )

    # Minimal synthetic team_subs DataFrame to feed the risk calc
    team_subs = pd.DataFrame([
        {
            "Customer": "Acme Corp",
            "Subscription_ID": "SUB-001",
            "End_Date": "2026-12-31",
            "ARR": 100000.0,
        },
    ])
    ab = pd.DataFrame([
        {
            "Customer Name": "Acme Corp",
            "Status": "Open",
            "Severity": "P3",
        },
    ])
    csone = pd.DataFrame([
        {
            "Customer Name": "Acme Corp",
            "Severity": "P3",
            "Status": "Open",
        },
    ])
    renewal_analysis = _calculate_simple_renewal_risk(
        customer_name="Acme Corp",
        customer_ab=ab,
        customer_csone=csone,
        team_subs_df=team_subs,
        days=90,
    )

    with tempfile.TemporaryDirectory() as tmp:
        # Mirror the production base-path shape:
        # ``AdoptIQ_Report_Renewal_<tag>``
        base = os.path.join(tmp, "AdoptIQ_Report_Renewal_TestCustomer_Webex_90d_20260101")
        path = _create_simple_renewal_report(
            base_path=base,
            customer_name="Acme Corp",
            technology="Webex",
            days=90,
            renewal_analysis=renewal_analysis,
            customer_ab=ab,
            customer_csone=csone,
            portfolio_mode=False,
        )
        # Positive: the saved path matches the clean ``{base}.docx`` shape
        assert path == base + ".docx", (
            f"Round 88 / F3: expected clean path ``{base}.docx`` but got "
            f"``{path}``.  The redundant ``_Renewal_Report`` suffix is back."
        )
        # Negative: the legacy redundant pattern is NOT used
        assert not path.endswith("_Renewal_Report.docx"), (
            "Round 88 / F3: returned path still ends with the redundant "
            "``_Renewal_Report.docx`` suffix.  The R88/F3 fix has regressed."
        )
        assert os.path.exists(path), (
            f"Round 88 / F3: docx not actually saved at {path}"
        )


def test_r88_f3_production_basename_pattern_yields_no_double_renewal_token() -> None:
    """The production caller in ``run_customer_renewal_analysis``
    builds ``base = AdoptIQ_Report_Renewal_<tag>``; with R88/F3 the
    final file is ``AdoptIQ_Report_Renewal_<tag>.docx`` — the
    ``Renewal`` token appears EXACTLY ONCE in the saved filename.
    """

    pytest.importorskip("docx", reason="python-docx required for round-trip test")

    from app_simple import (  # noqa: PLC0415
        _calculate_simple_renewal_risk,
        _create_simple_renewal_report,
    )

    team_subs = pd.DataFrame([
        {
            "Customer": "Acme Corp",
            "Subscription_ID": "SUB-001",
            "End_Date": "2026-12-31",
            "ARR": 100000.0,
        },
    ])
    ab = pd.DataFrame([{"Customer Name": "Acme Corp", "Status": "Open", "Severity": "P3"}])
    csone = pd.DataFrame([{"Customer Name": "Acme Corp", "Severity": "P3", "Status": "Open"}])
    renewal_analysis = _calculate_simple_renewal_risk(
        customer_name="Acme Corp",
        customer_ab=ab,
        customer_csone=csone,
        team_subs_df=team_subs,
        days=90,
    )

    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "AdoptIQ_Report_Renewal_Acme_Corp_Webex_90d_20260101")
        path = _create_simple_renewal_report(
            base_path=base,
            customer_name="Acme Corp",
            technology="Webex",
            days=90,
            renewal_analysis=renewal_analysis,
            customer_ab=ab,
            customer_csone=csone,
            portfolio_mode=False,
        )
        # The basename should contain ``Renewal`` exactly once
        basename = os.path.basename(path)
        renewal_count = basename.count("Renewal")
        assert renewal_count == 1, (
            f"Round 88 / F3: expected ``Renewal`` to appear exactly once in "
            f"the saved filename ``{basename}`` but it appears "
            f"{renewal_count} times.  The redundant suffix is back."
        )
