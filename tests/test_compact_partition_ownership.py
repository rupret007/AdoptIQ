"""Full-frame ownership quarantine for Compact and Leader report inputs."""

from pathlib import Path
from typing import Any

import pandas as pd

import compact_report_formatter as crf


def _profile_stub(calls: dict[str, dict[str, Any]]):
    def _capture(**kwargs: Any) -> dict[str, Any]:
        calls[kwargs["customer_name"]] = kwargs
        return {
            "risk_score_0_10": 1.0,
            "risk_score_0_100": 10.0,
            "risk_band": "HEALTHY",
            "risk_factors": [],
            "components": {
                "adoption_barriers": {"details": {"aging_open_count": 0}}
            },
        }

    return _capture


def _conflict_diag(frame: pd.DataFrame) -> dict[str, Any]:
    return dict(frame.attrs.get("cross_customer_id_conflicts") or {})


def test_compact_quarantines_conflicting_ids_before_customer_slices(
    monkeypatch,
) -> None:
    calls: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(crf, "compute_customer_risk_profile", _profile_stub(calls))

    subs = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["A-1", "B-1"],
            "BU_NAME": ["Alpha", "Beta"],
            "SUBSCRIPTION_ID": ["S-A", "S-B"],
        }
    )
    tac = pd.DataFrame(
        {
            "case_id": ["C-SHARED", "C-SHARED"],
            "customer_name": ["Alpha", "Beta"],
            "ACCOUNT_ID_C": ["A-1", "B-1"],
        }
    )
    pulse = pd.DataFrame(
        {
            "ID": ["P-SHARED", "P-SHARED"],
            "BU_NAME": ["Alpha", "Beta"],
            "ACCOUNT_ID_C": ["A-1", "B-1"],
            "SCORE__C": [2, 9],
        }
    )
    action_plans = pd.DataFrame(
        {
            "ID": ["AP-SHARED", "AP-SHARED"],
            "BU_NAME": ["Alpha", "Beta"],
            "ACCOUNT_ID_C": ["A-1", "B-1"],
            "STATUS_C": ["Open", "Closed"],
        }
    )

    result = crf.calculate_renewal_risk_scores(
        pd.DataFrame(),
        tac,
        pulse_df=pulse,
        action_plans_df=action_plans,
        subs_df=subs,
    )

    assert set(result) == {"Alpha", "Beta"}
    assert set(calls) == {"Alpha", "Beta"}
    for customer_call in calls.values():
        for argument, expected_id in (
            ("customer_csone", "C-SHARED"),
            ("customer_pulse", "P-SHARED"),
            ("customer_action_plans", "AP-SHARED"),
        ):
            frame = customer_call[argument]
            assert isinstance(frame, pd.DataFrame)
            assert frame.empty
            diag = _conflict_diag(frame)
            assert diag["conflicting_ids"] == [expected_id]
            assert diag["quarantined_rows"] == 2


def test_compact_collapses_customer_aliases_to_one_profile(monkeypatch) -> None:
    calls: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(crf, "compute_customer_risk_profile", _profile_stub(calls))

    ab = pd.DataFrame(
        {
            "customer_name": ["Acme, Inc.", "acme"],
            "ID": ["AB-1", "AB-2"],
        }
    )
    pulse = pd.DataFrame(
        {"BU_NAME": ["ACME INC"], "ID": ["P-1"], "SCORE__C": [8]}
    )
    subs = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["A-1", "A-1"],
            "BU_NAME": ["Acme, Inc.", "acme"],
            "SUBSCRIPTION_ID": ["S-1", "S-2"],
        }
    )

    result = crf.calculate_renewal_risk_scores(
        ab,
        pd.DataFrame(),
        pulse_df=pulse,
        subs_df=subs,
        account_to_customer={"A-1": "ACME INC"},
    )

    assert len(result) == 1
    assert len(calls) == 1
    profile_input = next(iter(calls.values()))
    assert len(profile_input["customer_ab"]) == 2
    assert len(profile_input["customer_pulse"]) == 1


def test_compact_does_not_assign_account_only_row_across_true_collision(
    monkeypatch,
) -> None:
    calls: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(crf, "compute_customer_risk_profile", _profile_stub(calls))
    subs = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["SHARED", "SHARED"],
            "BU_NAME": ["Alpha", "Beta"],
            "SUBSCRIPTION_ID": ["S-A", "S-B"],
        }
    )
    account_only_pulse = pd.DataFrame(
        {"ACCOUNT_ID_C": ["SHARED"], "ID": ["P-1"], "SCORE__C": [1]}
    )

    crf.calculate_renewal_risk_scores(
        pd.DataFrame(),
        pd.DataFrame(),
        pulse_df=account_only_pulse,
        subs_df=subs,
    )

    assert set(calls) == {"Alpha", "Beta"}
    assert all(call["customer_pulse"].empty for call in calls.values())


def test_compact_preserves_missing_vs_observed_empty_sources(monkeypatch) -> None:
    calls: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(crf, "compute_customer_risk_profile", _profile_stub(calls))

    observed_empty_ap = pd.DataFrame(columns=["ID", "BU_NAME", "STATUS_C"])
    observed_empty_ap.attrs["fetch_error"] = "action-plan feed unavailable"
    subs = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["A-1"],
            "BU_NAME": ["Alpha"],
            "SUBSCRIPTION_ID": ["S-1"],
        }
    )

    crf.calculate_renewal_risk_scores(
        None,
        pd.DataFrame(),
        pulse_df=None,
        action_plans_df=observed_empty_ap,
        subs_df=subs,
    )

    profile_input = calls["Alpha"]
    assert profile_input["customer_ab"] is None
    assert isinstance(profile_input["customer_csone"], pd.DataFrame)
    assert profile_input["customer_csone"].empty
    assert profile_input["customer_pulse"] is None
    assert profile_input["customer_action_plans"].empty
    assert (
        profile_input["customer_action_plans"].attrs["fetch_error"]
        == "action-plan feed unavailable"
    )


def test_compact_classifies_schema_bearing_empty_legacy_frame(monkeypatch) -> None:
    calls: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(crf, "compute_customer_risk_profile", _profile_stub(calls))

    empty_pulse = pd.DataFrame(columns=["ID", "BU_NAME", "SCORE__C"])
    empty_pulse.attrs["fetch_error_kind"] = "source_timeout"
    subs = pd.DataFrame(
        {
            "ACCOUNT_ID_C": ["A-1"],
            "BU_NAME": ["Alpha"],
            "SUBSCRIPTION_ID": ["S-1"],
        }
    )

    crf.calculate_renewal_risk_scores(
        pd.DataFrame(),
        pd.DataFrame(),
        extra_frames=[empty_pulse, subs],
    )

    pulse_input = calls["Alpha"]["customer_pulse"]
    assert pulse_input.empty
    assert pulse_input.attrs["fetch_error_kind"] == "source_timeout"


def test_compact_partitions_each_scoring_source_once(monkeypatch) -> None:
    calls: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(crf, "compute_customer_risk_profile", _profile_stub(calls))
    real_partition = crf.partition_customer_frame
    partitioned_sources: list[pd.DataFrame] = []

    def _counting_partition(frame: pd.DataFrame, **kwargs: Any):
        # Retain the objects so CPython cannot recycle an ``id`` between the
        # five sequential calls.
        partitioned_sources.append(frame)
        return real_partition(frame, **kwargs)

    monkeypatch.setattr(crf, "partition_customer_frame", _counting_partition)
    source = pd.DataFrame({"BU_NAME": ["Alpha"]})
    crf.calculate_renewal_risk_scores(
        source.rename(columns={"BU_NAME": "customer_name"}),
        source.rename(columns={"BU_NAME": "customer_name"}),
        pulse_df=source.assign(ID="P-1", SCORE__C=8),
        action_plans_df=source.assign(ID="AP-1", STATUS_C="Open"),
        subs_df=source.assign(ACCOUNT_ID_C="A-1", SUBSCRIPTION_ID="S-1"),
    )

    assert len(partitioned_sources) == 5
    assert len({id(frame) for frame in partitioned_sources}) == 5


def test_leader_quarantines_pulse_before_creator_or_account_slicing() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "leader_report_generator.py"
    ).read_text(encoding="utf-8")

    quarantine_at = source.index(
        "customer_pulse_all = quarantine_cross_customer_record_ids("
    )
    creator_series_at = source.index(
        "cp_creator_emails = _creator_email_series(customer_pulse_all"
    )
    slice_at = source.index(
        "customer_pulse_df = _slice_by_owner_or_account(customer_pulse_all"
    )
    assert quarantine_at < creator_series_at < slice_at
    assert "if df.empty:\n                    return df.copy()" in source
    assert "return df.iloc[0:0].copy()" in source
