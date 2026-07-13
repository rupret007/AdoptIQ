"""Round 48 / F-DV-PULSE-CONTRACT-DRIFT regression tests.

The Round 47 audit (run ID 1777445582 / 1777445562 / 1777445600)
showed two upstream contract drifts that propagated all the way
into the renewal Excel "Data_Unavailable" envelope and the renewal
Word fetch_error banner:

  * ``customer_pulse: missing slot(s) rating on a non-empty result
    (186 row(s))`` -- because the rating slot only accepted
    ``PULSE_RATING__C`` / ``Rating`` / ``RATING`` and the upstream
    delivered the friendly label ``Pulse Rating`` (mapped in
    ``report_export_schema.py`` ~L222) or the historic
    ``SCORE__C`` / ``SCORE_C`` columns documented in
    ``ask_ai_grounded.py`` ~L723.
  * ``adoption_barriers: missing slot(s) customer on a non-empty
    result (166 row(s))`` -- because the SQL view
    ``EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW`` only carries
    ``ACCOUNT_ID_C``; downstream loaders that resolve the customer
    name post-fetch stamp it as the friendly ``Customer`` label
    rather than the canonical ``BU_NAME``.

This rule pins the alias map to accept all documented variants so
the contract passes when ANY of the legitimate upstream column
names arrives on the frame.  Without this, the same dataset
silently went down two paths in the same run -- one that read
``BU_NAME`` (worked) and one that needed ``customer`` and lost it.
"""

from __future__ import annotations

import pandas as pd
import pytest

from data_contracts import (
    ROW_CONTRACT_ALIASES,
    annotate_with_contract,
    validate_row_contract,
)


# ---------------------------------------------------------------------------
# customer_pulse rating slot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rating_col",
    [
        # Existing aliases must keep working
        "PULSE_RATING__C",
        "PULSE_RATING",
        "Rating",
        "RATING",
        # Round 48 additions: friendly export label
        "Pulse Rating",
        # Round 48 additions: historic SCORE-style columns documented
        # in ``ask_ai_grounded.py`` ~L723
        "SCORE__C",
        "SCORE_C",
        # Round 48 additions: alternate OVERALL_RATING family
        "OVERALL_RATING__C",
        "OVERALL_RATING",
    ],
)
def test_round48_pulse_rating_slot_accepts_all_documented_variants(rating_col):
    df = pd.DataFrame(
        {
            "ID": [1, 2, 3],
            rating_col: ["Green", "Yellow", "Red"],
            "BU_NAME": ["Acme", "Foo", "Bar"],
            "ACCOUNT__C": ["a1", "a2", "a3"],
            "CREATEDDATE": ["2026-04-01", "2026-04-15", "2026-04-20"],
        }
    )
    result = validate_row_contract(df, dataset="customer_pulse")
    assert result["is_valid"], (
        f"customer_pulse rating slot rejected the {rating_col!r} column "
        f"despite Round 48 alias expansion; missing={result['missing_slots']}"
    )
    assert result["matched_columns"]["rating"] == rating_col


def test_round48_pulse_rating_alias_list_includes_friendly_pulse_rating():
    aliases = ROW_CONTRACT_ALIASES["customer_pulse"]["rating"]
    assert "Pulse Rating" in aliases, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: customer_pulse rating "
        "slot must accept the Excel friendly label 'Pulse Rating' so "
        "post-fetch friendly-renamed frames do not trip a fake schema "
        "drift."
    )


def test_round48_pulse_rating_alias_list_includes_score_variants():
    aliases = ROW_CONTRACT_ALIASES["customer_pulse"]["rating"]
    assert "SCORE__C" in aliases, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: customer_pulse rating "
        "slot must accept the historic ``SCORE__C`` column documented "
        "in ask_ai_grounded.py ~L723."
    )
    assert "SCORE_C" in aliases, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: customer_pulse rating "
        "slot must accept the historic ``SCORE_C`` variant."
    )


# ---------------------------------------------------------------------------
# customer_pulse customer slot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "customer_col",
    [
        # Existing aliases keep working
        "BU_NAME",
        "customer_name",
        "ACCOUNT_NAME",
        # Round 48 additions
        "CUSTOMER_NAME__C",
        "Customer",
        "Customer Name",
    ],
)
def test_round48_pulse_customer_slot_accepts_documented_variants(customer_col):
    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "PULSE_RATING__C": ["G", "Y"],
            customer_col: ["Acme", "Foo"],
            "ACCOUNT__C": ["a1", "a2"],
            "CREATEDDATE": ["2026-04-01", "2026-04-15"],
        }
    )
    result = validate_row_contract(df, dataset="customer_pulse")
    assert result["is_valid"], (
        f"customer_pulse customer slot rejected the {customer_col!r} "
        f"column despite Round 48 alias expansion; "
        f"missing={result['missing_slots']}"
    )
    assert result["matched_columns"]["customer"] == customer_col


# ---------------------------------------------------------------------------
# adoption_barriers customer slot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "customer_col",
    [
        # Existing aliases must keep working
        "BU_NAME",
        "customer_name",
        "ACCOUNT_NAME",
        # Round 48 additions: friendly variants the post-fetch
        # resolver stamps when the source view (C360_CS_TASK_C_VW)
        # only carries ACCOUNT_ID_C.
        "Customer",
        "Customer Name",
        "BU_ACCOUNT_NAME",
    ],
)
def test_round48_ab_customer_slot_accepts_documented_variants(customer_col):
    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "SUBJECT_C": ["barrier-1", "barrier-2"],
            "AB_STATUS_C": ["Open", "Closed"],
            "SEVERITY_C": ["P2", "P3"],
            customer_col: ["Acme", "Foo"],
            "ACCOUNT_ID_C": ["a1", "a2"],
        }
    )
    result = validate_row_contract(df, dataset="adoption_barriers")
    assert result["is_valid"], (
        f"adoption_barriers customer slot rejected the {customer_col!r} "
        f"column despite Round 48 alias expansion; "
        f"missing={result['missing_slots']}"
    )
    assert result["matched_columns"]["customer"] == customer_col


def test_round48_ab_customer_alias_list_includes_friendly_variants():
    aliases = ROW_CONTRACT_ALIASES["adoption_barriers"]["customer"]
    for friendly in ("Customer", "Customer Name", "BU_ACCOUNT_NAME"):
        assert friendly in aliases, (
            f"Round 48 / F-DV-PULSE-CONTRACT-DRIFT: adoption_barriers "
            f"customer slot must accept the friendly label {friendly!r} "
            f"so post-fetch resolved frames do not trip a fake schema "
            f"drift."
        )


# ---------------------------------------------------------------------------
# tac_cases customer slot (parity with adoption_barriers)
# ---------------------------------------------------------------------------


def test_round48_tac_cases_customer_alias_list_includes_friendly_variants():
    aliases = ROW_CONTRACT_ALIASES["tac_cases"]["customer"]
    for friendly in ("Customer", "Customer Name", "BU_ACCOUNT_NAME"):
        assert friendly in aliases, (
            f"Round 48 / F-DV-PULSE-CONTRACT-DRIFT: tac_cases customer "
            f"slot must keep parity with adoption_barriers and accept "
            f"the friendly label {friendly!r}."
        )


# ---------------------------------------------------------------------------
# End-to-end annotate_with_contract: friendly columns no longer escalate to
# ``fetch_error: schema_drift``.
# ---------------------------------------------------------------------------


def test_round48_annotate_pulse_friendly_pulse_rating_does_not_set_fetch_error():
    df = pd.DataFrame(
        {
            "ID": [1, 2, 3],
            "Pulse Rating": ["Green", "Yellow", "Red"],
            "BU_NAME": ["Acme", "Foo", "Bar"],
            "ACCOUNT__C": ["a1", "a2", "a3"],
            "CREATEDDATE": ["2026-04-01", "2026-04-15", "2026-04-20"],
        }
    )
    annotated = annotate_with_contract(df, dataset="customer_pulse")
    assert annotated.attrs.get("fetch_error") is None, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: a frame carrying the "
        "friendly 'Pulse Rating' column must NOT trip schema_drift "
        f"escalation. Got fetch_error={annotated.attrs.get('fetch_error')!r}"
    )
    contract = annotated.attrs.get("row_contract")
    assert contract["is_valid"] is True, (
        f"Round 48 / F-DV-PULSE-CONTRACT-DRIFT: contract should be valid "
        f"for friendly 'Pulse Rating'; got {contract!r}"
    )


def test_round48_annotate_ab_friendly_customer_does_not_set_fetch_error():
    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "SUBJECT_C": ["barrier-1", "barrier-2"],
            "AB_STATUS_C": ["Open", "Closed"],
            "SEVERITY_C": ["P2", "P3"],
            "Customer": ["Acme", "Foo"],
            "ACCOUNT_ID_C": ["a1", "a2"],
        }
    )
    annotated = annotate_with_contract(df, dataset="adoption_barriers")
    assert annotated.attrs.get("fetch_error") is None, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: a frame carrying the "
        "friendly 'Customer' column must NOT trip schema_drift "
        f"escalation. Got fetch_error={annotated.attrs.get('fetch_error')!r}"
    )
    contract = annotated.attrs.get("row_contract")
    assert contract["is_valid"] is True
    # ``annotate_with_contract`` should also copy the matched friendly
    # column into the canonical ``customer`` column (R32 behavior).
    assert "customer" in annotated.columns
    assert list(annotated["customer"]) == ["Acme", "Foo"]


def test_round48_annotate_pulse_score_does_not_set_fetch_error():
    df = pd.DataFrame(
        {
            "ID": [1, 2, 3],
            "SCORE__C": [4, 3, 5],
            "BU_NAME": ["Acme", "Foo", "Bar"],
            "ACCOUNT__C": ["a1", "a2", "a3"],
            "CREATEDDATE": ["2026-04-01", "2026-04-15", "2026-04-20"],
        }
    )
    annotated = annotate_with_contract(df, dataset="customer_pulse")
    assert annotated.attrs.get("fetch_error") is None, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: a frame carrying the "
        "historic SCORE__C column must NOT trip schema_drift escalation."
    )
    contract = annotated.attrs.get("row_contract")
    assert contract["is_valid"] is True
    assert contract["matched_columns"]["rating"] == "SCORE__C"


# ---------------------------------------------------------------------------
# Negative case: aliases are still required when NO customer-bearing column
# arrives on a non-empty frame.  We don't want to relax the contract so far
# that genuine drift gets silenced.
# ---------------------------------------------------------------------------


def test_round48_pulse_no_rating_column_still_escalates_schema_drift():
    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "BU_NAME": ["Acme", "Foo"],
            "ACCOUNT__C": ["a1", "a2"],
            "CREATEDDATE": ["2026-04-01", "2026-04-15"],
            "totally_made_up_column": ["x", "y"],
        }
    )
    annotated = annotate_with_contract(df, dataset="customer_pulse")
    err = annotated.attrs.get("fetch_error")
    assert err and "schema_drift" in err and "rating" in err, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: when NO rating column "
        "arrives, the schema_drift escalation must still fire.  Otherwise "
        "real upstream drift gets silenced."
    )


def test_round48_ab_no_customer_column_still_escalates_schema_drift():
    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "SUBJECT_C": ["x", "y"],
            "AB_STATUS_C": ["Open", "Closed"],
            "SEVERITY_C": ["P2", "P3"],
            "ACCOUNT_ID_C": ["a1", "a2"],
            # NO BU_NAME, customer_name, ACCOUNT_NAME, Customer, etc.
        }
    )
    annotated = annotate_with_contract(df, dataset="adoption_barriers")
    err = annotated.attrs.get("fetch_error")
    assert err and "schema_drift" in err and "customer" in err, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: when NO customer column "
        "arrives, the schema_drift escalation must still fire."
    )


# ---------------------------------------------------------------------------
# Anchor: the fix comment must be present in data_contracts.py so a future
# code review sees the round/defect ID without git blame archaeology.
# ---------------------------------------------------------------------------


def test_round48_data_contracts_carries_fix_anchor():
    from pathlib import Path

    src = Path("data_contracts.py").read_text(encoding="utf-8")
    assert "F-DV-PULSE-CONTRACT-DRIFT" in src, (
        "Round 48 fix anchor F-DV-PULSE-CONTRACT-DRIFT missing from "
        "data_contracts.py"
    )
    # Both pulse and AB sections should call out the fix.
    assert src.count("F-DV-PULSE-CONTRACT-DRIFT") >= 2, (
        "Round 48 / F-DV-PULSE-CONTRACT-DRIFT: expected at least two "
        "fix-anchor comments (one per dataset)"
    )
