"""Round 93 -- All Contact Center AB scope must not widen to all products."""

from __future__ import annotations

import pandas as pd

import adoptiq_backend as ab


def _ab_frame(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "OPEN_DATE_C": "2026-04-01T00:00:00Z",
        "SUBJECT_C": "",
        "DESCRIPTION_C": "",
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def test_all_contact_center_ab_filter_drops_explicit_non_cc_products() -> None:
    df = _ab_frame(
        [
            {"ID": "AB-WXCC", "PRODUCT_C": "Webex Contact Center"},
            {"ID": "AB-UCCE", "PRODUCT_C": "Contact Center Software", "SUB_TECHNOLOGY_C": "Cisco UCCE"},
            {"ID": "AB-UCCX", "PRODUCT_C": "Cisco Unified Contact Center Express"},
            {"ID": "AB-DEVICES", "PRODUCT_C": "Webex Devices", "SUCCESS_TRACK_C": "Webex Meetings & Messaging"},
            {"ID": "AB-UNKNOWN", "PRODUCT_C": "", "PRODUCT_NAME_C": ""},
        ]
    )

    out = ab._apply_scope_filter_ab(df, tech="All Contact Center", days=365)

    assert list(out["ID"]) == ["AB-WXCC", "AB-UCCE", "AB-UCCX"]
    assert out.attrs.get("tech_filter_strict_applied") is True
    assert out.attrs.get("tech_filter_widened") is not True
    assert out.attrs.get("tech_filter_matched") == 3
    assert out.attrs.get("tech_filter_total") == 5
    assert out.attrs.get("tech_filter_excluded_total") == 2
    assert out.attrs.get("tech_filter_nonmatching_total") == 1
    assert out.attrs.get("tech_filter_unknown_total") == 1


def test_all_contact_center_ab_filter_does_not_widen_when_no_rows_match() -> None:
    df = _ab_frame(
        [
            {"ID": f"AB-DEVICES-{i}", "PRODUCT_C": "Webex Devices", "SUCCESS_TRACK_C": "Webex Meetings & Messaging"}
            for i in range(20)
        ]
    )

    out = ab._apply_scope_filter_ab(df, tech="All Contact Center", days=365)

    assert out.empty
    assert out.attrs.get("tech_filter_strict_applied") is True
    assert out.attrs.get("tech_filter_widened") is not True
    assert out.attrs.get("tech_filter_matched") == 0
    assert out.attrs.get("tech_filter_total") == 20
    assert out.attrs.get("tech_filter_excluded_total") == 20
    assert "widened to unfiltered" not in str(out.attrs.get("tech_filter_warning", "")).lower()


def test_all_contact_center_ab_scope_warning_entry_shape() -> None:
    from app_simple import _r93_ab_scope_warning_entries

    df = _ab_frame(
        [
            {"ID": "AB-WXCC", "PRODUCT_C": "Webex Contact Center"},
            {"ID": "AB-DEVICES", "PRODUCT_C": "Webex Devices"},
        ]
    )
    out = ab._apply_scope_filter_ab(df, tech="All Contact Center", days=365)

    entries = _r93_ab_scope_warning_entries(out)

    assert entries == [
        {
            "dataset": "adoption_barriers",
            "error": out.attrs["tech_filter_warning"],
            "kind": "tech_filter_scope_excluded",
            "tech_requested": "All Contact Center",
            "matched": 1,
            "total": 2,
            "excluded": 1,
            "unknown": 0,
            "nonmatching": 1,
        }
    ]


def test_named_technology_empty_match_returns_empty_with_warning() -> None:
    df = _ab_frame(
        [
            {"ID": f"AB-MEETINGS-{i}", "PRODUCT_C": "Webex Meetings"}
            for i in range(20)
        ]
    )

    out = ab._apply_scope_filter_ab(df, tech="Webex Calling", days=365)

    assert out.empty
    assert out.attrs.get("tech_filter_empty_after_scope") is True
    assert out.attrs.get("tech_filter_requested") == "Webex Calling"
    assert out.attrs.get("tech_filter_matched") == 0
    assert out.attrs.get("tech_filter_total") == 20
    assert "returning an empty scoped set instead of widening" in out.attrs.get("tech_filter_warning", "")
