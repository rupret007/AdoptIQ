"""Round 8 / Phase 6.9: behavioural tests for data_normalization helpers.

These tests exercise the actual normalisation surface used by every
report generator -- status / priority / severity buckets, the customer
lookup builder (including collision handling), the BEMS detector and
the case lifecycle pipeline.  They lock in the contract that downstream
report code relies on so a future tweak to one regex can't silently
re-classify thousands of historical rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from data_normalization import (
    add_case_lifecycle_fields,
    build_customer_lookup,
    classify_case_type,
    detect_bems_mask,
    extract_bems_ids_from_row,
    extract_bems_ids_from_text,
    first_existing_column,
    normalize_customer_name,
    normalize_priority_label,
    normalize_severity_label,
    normalize_status_label,
    parse_datetime_series,
    resolve_customer_name,
)


# --------------------------------------------------------------------------
# Customer name normalisation
# --------------------------------------------------------------------------

def test_normalize_customer_name_collapses_whitespace():
    assert normalize_customer_name("  Acme   Corp  ") == "Acme Corp"
    assert normalize_customer_name("Foo\tBar") == "Foo Bar"


def test_normalize_customer_name_unknown_for_blank_or_sentinel():
    assert normalize_customer_name(None) == "Unknown"
    assert normalize_customer_name("") == "Unknown"
    assert normalize_customer_name("nan") == "Unknown"
    assert normalize_customer_name("None") == "Unknown"
    assert normalize_customer_name("NULL") == "Unknown"


# --------------------------------------------------------------------------
# Status / priority / severity buckets
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("Open", "Open"),
        ("In Progress", "Open"),
        ("Waiting on Customer", "Open"),
        ("Awaiting engineering", "Open"),
        ("On Hold", "Open"),
        ("Closed", "Closed"),
        ("RESOLVED", "Closed"),
        ("Completed", "Closed"),
        ("Cancelled", "Closed"),
        ("", "Unknown"),
        ("foobar", "Unknown"),
    ],
)
def test_normalize_status_label_buckets(value, expected):
    assert normalize_status_label(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", "P1"), ("2", "P2"), ("3", "P3"), ("4", "P4"),
        ("P1", "P1"), ("Sev 2", "P2"), ("priority-3", "P3"),
        ("Critical", "P1"), ("High", "P2"), ("Medium", "P3"), ("Low", "P4"),
        ("", "Unknown"), ("foo", "Unknown"),
    ],
)
def test_normalize_priority_label_buckets(value, expected):
    assert normalize_priority_label(value) == expected


def test_severity_label_mirrors_priority():
    assert normalize_severity_label("1") == "Critical"
    assert normalize_severity_label("Sev 2") == "High"
    assert normalize_severity_label("Medium") == "Medium"
    assert normalize_severity_label("Low") == "Low"
    assert normalize_severity_label("") == "Unknown"


# --------------------------------------------------------------------------
# build_customer_lookup deterministic collision handling
# --------------------------------------------------------------------------

def test_build_customer_lookup_handles_empty_inputs():
    out = build_customer_lookup(None)
    assert out["account_to_customer"] == {}
    assert out["key_to_customer"] == {}
    out2 = build_customer_lookup(pd.DataFrame())
    assert out2["collisions"] == []
    assert out2["warnings"] == []


def test_build_customer_lookup_chooses_alphabetical_winner_on_collision():
    df = pd.DataFrame([
        {"BU_NAME": "Zeta Corp", "ACCOUNT_ID_C": "A1"},
        {"BU_NAME": "Alpha Corp", "ACCOUNT_ID_C": "A1"},  # same id, two names
        {"BU_NAME": "Beta Corp", "ACCOUNT_ID_C": "A2"},
    ])
    out = build_customer_lookup(df)
    # Deterministic alphabetical pick across runs / row orders.
    assert out["account_to_customer"]["A1"] == "Alpha Corp"
    assert out["account_to_customer"]["A2"] == "Beta Corp"
    # Collision must be surfaced for partial_data_warnings routing.
    coll = [c for c in out["collisions"] if c["kind"] == "account_to_customer" and c["key"] == "A1"]
    assert coll and coll[0]["chosen"] == "Alpha Corp"
    assert "Zeta Corp" in coll[0]["alternatives"]
    assert any("collision" in w for w in out["warnings"])


def test_build_customer_lookup_skips_unknown_rows():
    df = pd.DataFrame([
        {"BU_NAME": "", "ACCOUNT_ID_C": "A1"},
        {"BU_NAME": "Acme", "ACCOUNT_ID_C": "A2"},
    ])
    out = build_customer_lookup(df)
    assert "A1" not in out["account_to_customer"]
    assert out["account_to_customer"].get("A2") == "Acme"


# --------------------------------------------------------------------------
# resolve_customer_name precedence
# --------------------------------------------------------------------------

def test_resolve_customer_name_prefers_account_lookup():
    lookup = {
        "account_to_customer": {"A1": "Canonical"},
        "key_to_customer": {"some other": "Other"},
    }
    row = pd.Series({"ACCOUNT_ID_C": "A1", "BU_NAME": "Stale Name"})
    assert resolve_customer_name(row, lookup) == "Canonical"


def test_resolve_customer_name_falls_back_to_key_lookup():
    lookup = {
        "account_to_customer": {},
        "key_to_customer": {"acme": "Acme Inc"},
    }
    row = pd.Series({"ACCOUNT_ID_C": "", "BU_NAME": "Acme"})
    assert resolve_customer_name(row, lookup) == "Acme Inc"


def test_resolve_customer_name_returns_unknown_when_blank():
    row = pd.Series({"BU_NAME": ""})
    assert resolve_customer_name(row) == "Unknown"


# --------------------------------------------------------------------------
# BEMS detection
# --------------------------------------------------------------------------

def test_extract_bems_ids_from_text_finds_canonical_ids():
    refs = extract_bems_ids_from_text("see BEMS-12345 and BEMS 67890 today")
    assert "BEMS-12345" in refs
    assert "BEMS 67890" in refs or "BEMS-67890" in refs or any(
        r.startswith("BEMS") and "67890" in r for r in refs
    )


def test_extract_bems_ids_dedupes_and_sorts():
    refs = extract_bems_ids_from_text("BEMS-1 BEMS-1 BEMS-2")
    assert refs == sorted(set(refs))


def test_extract_bems_ids_drops_bare_token_when_numbered_present():
    refs = extract_bems_ids_from_text("BEMS BEMS-100")
    # When at least one numbered ref exists, the bare ``BEMS`` token is
    # dropped to avoid spurious "no-id" entries in the output.
    assert "BEMS" not in refs
    assert any("100" in r for r in refs)


def test_detect_bems_mask_finds_refs_in_multiple_columns():
    df = pd.DataFrame([
        {"Transaction ID": "BEMS-100", "Title": "ok"},
        {"Transaction ID": "TID-1", "Title": "engineering escalation needed"},
        {"Transaction ID": "TID-2", "Title": "boring"},
    ])
    mask = detect_bems_mask(df)
    assert mask.tolist() == [True, True, False]


def test_detect_bems_mask_empty_returns_empty():
    assert detect_bems_mask(None).empty
    assert detect_bems_mask(pd.DataFrame()).empty


def test_extract_bems_ids_from_row_uses_specified_columns():
    row = pd.Series({
        "Transaction ID": "BEMS-99",
        "Title": "discusses BEMS-12 too",
        "Problem Description": "no refs here",
    })
    refs = extract_bems_ids_from_row(row)
    assert "BEMS-99" in refs
    assert "BEMS-12" in refs


# --------------------------------------------------------------------------
# Case-type classification
# --------------------------------------------------------------------------

def test_classify_case_type_picks_provisioning():
    row = pd.Series({"Case Type": "Feature request - enable license"})
    assert classify_case_type(row) == "provisioning_request"


def test_classify_case_type_picks_break_fix():
    row = pd.Series({"Case Type": "Outage - sev 1 incident"})
    assert classify_case_type(row) == "break_fix_technical"


def test_classify_case_type_falls_back_to_text_columns():
    row = pd.Series({
        "Case Type": "",
        "Title": "service crash and degradation",
        "Problem Description": "users see errors",
    })
    assert classify_case_type(row) == "break_fix_technical"


# --------------------------------------------------------------------------
# Datetime helpers
# --------------------------------------------------------------------------

def test_parse_datetime_series_coerces_invalid():
    s = pd.Series(["2024-01-15", "not a date", None, "2024-02-01T10:00:00Z"])
    parsed = parse_datetime_series(s)
    assert parsed.iloc[0] == pd.Timestamp("2024-01-15")
    assert pd.isna(parsed.iloc[1])
    assert pd.isna(parsed.iloc[2])
    assert parsed.iloc[3] == pd.Timestamp("2024-02-01 10:00:00")


def test_first_existing_column_returns_first_match():
    cols = ["a", "b", "c"]
    assert first_existing_column(cols, ("z", "b", "a")) == "b"
    assert first_existing_column(cols, ("x", "y")) is None


# --------------------------------------------------------------------------
# add_case_lifecycle_fields end-to-end
# --------------------------------------------------------------------------

def test_add_case_lifecycle_fields_computes_open_and_age():
    now_utc = datetime.now(timezone.utc)
    open_5d = (now_utc - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    open_15d = (now_utc - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    closed_2d = (now_utc - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    df = pd.DataFrame([
        {
            "BU_NAME": "Acme",
            "ACCOUNT_ID_C": "A1",
            "Case Status": "Open",
            "Severity": "P1",
            "Date/Time Opened": open_5d,
            "Date/Time Closed": "",
            "Title": "outage",
        },
        {
            "BU_NAME": "Acme",
            "ACCOUNT_ID_C": "A1",
            "Case Status": "Closed",
            "Severity": "P3",
            "Date/Time Opened": open_15d,
            "Date/Time Closed": closed_2d,
            "Title": "license enable",
        },
    ])
    out = add_case_lifecycle_fields(df)
    assert list(out["case_status_norm"]) == ["Open", "Closed"]
    assert list(out["case_priority_norm"]) == ["P1", "P3"]
    assert list(out["severity_norm"]) == ["Critical", "Medium"]
    # Open age recorded for the open row only.
    assert out.loc[0, "open_age_days"] == 5
    assert pd.isna(out.loc[1, "open_age_days"])
    # Closed age recorded for the closed row only.
    assert pd.isna(out.loc[0, "closed_age_days"])
    assert out.loc[1, "closed_age_days"] == 2
    # is_open / is_closed flags wired correctly.
    assert list(out["is_open"]) == [True, False]
    assert list(out["is_closed"]) == [False, True]
    assert "customer_name" in out.columns
    assert out.loc[0, "customer_name"] == "Acme"
    # Case classification populated.
    assert "case_type_class" in out.columns


def test_add_case_lifecycle_fields_marks_closed_when_close_date_present_but_status_unknown():
    now_utc = datetime.now(timezone.utc)
    closed_3d = (now_utc - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    df = pd.DataFrame([{
        "BU_NAME": "X",
        "Case Status": "weird-bucket-not-in-patterns",
        "Date/Time Opened": (now_utc - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Date/Time Closed": closed_3d,
    }])
    out = add_case_lifecycle_fields(df)
    # Round 121 / G4a: an Unknown status paired with a real close date is now
    # relabeled "Closed" so the displayed Status matches the (already-correct)
    # is_closed boolean.  Pre-R121 the displayed Status stayed "Unknown".
    assert out.loc[0, "case_status_norm"] == "Closed"
    # Unknown status + close date => still treated as closed.
    assert bool(out.loc[0, "is_closed"]) is True
    assert bool(out.loc[0, "is_open"]) is False


def test_add_case_lifecycle_fields_empty_returns_empty():
    assert add_case_lifecycle_fields(None).empty
    assert add_case_lifecycle_fields(pd.DataFrame()).empty
