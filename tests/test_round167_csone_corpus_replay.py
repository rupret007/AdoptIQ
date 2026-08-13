"""Round 167 privacy-preserving real-shape CSOne replay."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import json

import pandas as pd

import canonical_metrics as canonical
from csone_corpus_replay import (
    pseudonymize_csone_frame,
    replay_bundle_from_corpus,
    validate_representative_loaders,
)
from local_acceptance_lab import build_scenario_bundle
from data_normalization import add_case_lifecycle_fields


def _source_frame(rows: int = 40) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "Customer Name: Customer Name": f"Sensitive Customer {index % 5}",
                "Subscription Reference Id": f"REAL-SUB-{index % 7}",
                "Product: Product Name": "Webex Calling",
                "Tech.": "Calling",
                "Sub Technology": "Registration",
                "Severity": ("1", "2", "3")[index % 3],
                "Service Tier": "Premium",
                "SR Number": "" if index == 0 else f"REAL-SR-{index // 2}",
                "Case Number": "" if index == 0 else f"REAL-SR-{index // 2}",
                "Title": (
                    f"Sensitive provisioning enablement {index}"
                    if index % 3 == 0
                    else f"Sensitive outage failure {index}"
                    if index % 3 == 1
                    else f"Sensitive inquiry {index}"
                ),
                "Case Status": "" if index % 4 == 0 else "Closed",
                "Transaction ID": f"REAL-TXN-{index}" if index % 3 == 0 else "",
                "Date/Time Opened": pd.Timestamp("2026-07-01", tz="UTC")
                + pd.to_timedelta(index, unit="D"),
                "Date/Time Closed": pd.Timestamp("2026-07-02", tz="UTC")
                + pd.to_timedelta(index, unit="D"),
                "Current Contact Email": f"person{index}@sensitive.example",
                "Case Owner: Full Name": f"Sensitive Owner {index}",
                "Problem Description": f"Sensitive problem {index}",
                "CSE Action Plan": f"Sensitive action {index}",
                "Last Cisco Update": f"Sensitive update {index}",
                "Resolution Summary": f"Sensitive resolution {index}",
                "Problem Details": f"Sensitive details {index}",
                "Customer Activity": f"Sensitive activity {index}",
                "Problem Code": "CONFIG",
                "Resolution Code": "FIXED",
                "Case Origin": "Web",
                "Highest Priority": index % 2 == 0,
                "# of Case Owner Changes": index % 4,
            }
            for index in range(rows)
        ]
    )
    frame.attrs["excluded_non_record_rows"] = 6
    return frame


def test_pseudonymized_replay_preserves_shape_without_raw_identity() -> None:
    bundle = build_scenario_bundle("multi_manager")

    replay = pseudonymize_csone_frame(_source_frame(), bundle, max_rows=24)
    serialized = replay.to_json(date_format="iso")

    assert len(replay) == 24
    assert replay.attrs["corpus_replay"] is True
    assert replay.attrs["raw_values_retained"] is False
    assert replay.attrs["excluded_non_record_rows"] == 6
    assert replay["Severity"].nunique() == 3
    assert replay["Case Status"].eq("").any()
    assert replay["SR Number"].eq("").any()
    assert replay["SR Number"].loc[replay["SR Number"].ne("")].duplicated().any()
    assert replay["Date/Time Closed"].max() <= pd.Timestamp(bundle.as_of_utc)
    assert "Sensitive" not in serialized
    assert "sensitive.example" not in serialized
    assert "REAL-SR" not in serialized
    assert "Pseudonymized" in serialized
    assert replay.attrs["case_type_distribution_reconciled"] is True
    assert set(replay.attrs["case_type_distribution"]) == {
        "break_fix_technical",
        "provisioning_request",
        "unknown",
    }

    normalized = add_case_lifecycle_fields(replay)
    assert normalized["case_type_class"].value_counts().sort_index().to_dict() == (
        replay.attrs["case_type_distribution"]
    )
    operating_health = canonical.tac_operating_health(
        normalized,
        as_of=bundle.as_of_utc,
    )
    assert operating_health is not None
    assert operating_health["closed_case_count"] > 0
    assert operating_health["ownership_observed_count"] == canonical.count_total_tac(
        normalized
    )


def test_replay_projection_is_compatible_with_system_python_39() -> None:
    source = Path(__file__).parents[1].joinpath("csone_corpus_replay.py").read_text(
        encoding="utf-8"
    )

    # This test intentionally pins syntax/API compatibility for the direct
    # macOS pre-build command, which may resolve to CommandLineTools Python 3.9
    # before the release script selects its 3.11 build interpreter.
    tree = ast.parse(source)
    strict_zip_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "zip"
        and any(keyword.arg == "strict" for keyword in node.keywords)
    ]
    assert strict_zip_calls == []
    if sys.version_info[:2] == (3, 9):
        assert len(pseudonymize_csone_frame(_source_frame(), build_scenario_bundle("healthy"))) > 0


def test_replay_bundle_reconciles_canonical_counts(tmp_path: Path) -> None:
    (tmp_path / "representative.xlsx").touch()
    bundle = build_scenario_bundle("multi_manager")

    replayed = replay_bundle_from_corpus(
        bundle,
        tmp_path,
        loader=lambda _path: _source_frame(),
        max_rows=30,
    )

    replayed.assert_reconciled()
    assert replayed.expected_counts["tac_cases"] == 30
    assert replayed.expected_counts["bems_cases"] > 0
    assert replayed.frame("tac_cases").attrs["corpus_replay"] is True


def test_representative_loader_contract_retains_only_counts_and_hashes(
    tmp_path: Path,
) -> None:
    for index in range(5):
        (tmp_path / f"report-{index}.xlsx").touch()

    contract = validate_representative_loaders(
        tmp_path,
        loader=lambda _path: _source_frame(),
    )

    assert contract["representative_workbook_count"] == 3
    assert contract["all_nonempty"] is True
    assert contract["no_footer_rows_remaining"] is True
    assert contract["consistent_schema"] is True
    assert "Sensitive" not in str(contract)


def test_replay_cli_summary_exposes_only_aggregate_operating_health(tmp_path: Path) -> None:
    import scripts.run_csone_corpus_replay as runner

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "representative.xlsx").touch()
    original_loader = runner.backend.load_csone_excel
    runner.backend.load_csone_excel = lambda _path: _source_frame()
    try:
        summary = runner.run_replay(corpus, max_rows=30)
    finally:
        runner.backend.load_csone_excel = original_loader

    health = summary["replay"]["operating_health"]
    assert health["available"] is True
    assert health["closed_case_count"] > 0
    # The replay hashes and stratifies source rows before projecting IDs, so
    # exact count equality to input ordering is intentionally not assumed.
    assert 0 < health["ownership_observed_count"] <= 30
    serialized = json.dumps(summary, sort_keys=True)
    assert "Sensitive" not in serialized
    assert "REAL-SR" not in serialized
