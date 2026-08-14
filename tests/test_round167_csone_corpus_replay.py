"""Round 167 privacy-preserving real-shape CSOne replay."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import sys
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import pytest

import canonical_metrics as canonical
from csone_corpus_replay import (
    SOURCE_CATEGORY_COLUMNS,
    pseudonymize_csone_frame,
    replay_bundle_from_corpus,
    validate_pseudonymous_csone_frame,
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


def _synthetic_core_workbook(path: Path, modified_utc: str) -> None:
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <dcterms:created xsi:type="dcterms:W3CDTF">{modified_utc}</dcterms:created>
 <dcterms:modified xsi:type="dcterms:W3CDTF">{modified_utc}</dcterms:modified>
</cp:coreProperties>
"""
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("docProps/core.xml", core)


def _stratified_corpus(tmp_path: Path) -> tuple[Path, dict[str, tuple[str, int, bool]]]:
    corpus = tmp_path / "stratified-corpus"
    corpus.mkdir()
    specs = {
        # Names intentionally oppose chronology so filename ordering would
        # select the wrong old/new workbooks.
        "zzz-old.xlsx": ("2024-01-01T00:00:00Z", 12, False),
        "middle-ordinary.xlsx": ("2025-01-01T00:00:00Z", 15, False),
        "high-volume.xlsx": ("2025-06-01T00:00:00Z", 60, False),
        "schema-edge.xlsx": ("2025-09-01T00:00:00Z", 10, True),
        "aaa-new.xlsx": ("2026-01-01T00:00:00Z", 18, False),
    }
    for name, (modified, _rows, _edge) in specs.items():
        _synthetic_core_workbook(corpus / name, modified)
    return corpus, specs


def test_pseudonymized_replay_preserves_shape_without_raw_identity() -> None:
    bundle = build_scenario_bundle("multi_manager")

    replay = pseudonymize_csone_frame(_source_frame(), bundle, max_rows=24)
    serialized = replay.to_json(date_format="iso")

    assert len(replay) == 24
    assert replay.attrs["corpus_replay"] is True
    assert replay.attrs["raw_values_retained"] is False
    assert replay.attrs["excluded_non_record_rows"] == 6
    assert replay["Severity"].nunique() == 3
    assert replay["Case Status"].eq("Unknown").any()
    assert replay["SR Number"].eq("").any()
    assert replay["SR Number"].loc[replay["SR Number"].ne("")].duplicated().any()
    assert replay["Date/Time Closed"].max() <= pd.Timestamp(bundle.as_of_utc)
    assert "Sensitive" not in serialized
    assert "sensitive.example" not in serialized
    assert "REAL-SR" not in serialized
    assert "Pseudonymized" in serialized
    assert replay.attrs["case_type_distribution_reconciled"] is True
    assert replay.attrs["privacy_contract"] == {
        "schema_version": "csone-replay-privacy/v1",
        "validated": True,
        "raw_values_retained": False,
        "row_count": 24,
        "column_count": len(replay.columns),
        "text_cell_count": 24 * 36,
        "date_cell_count": 24 * 2,
        "bucketed_cell_count": 24 * 2,
    }
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


def test_every_retained_category_rejects_raw_formula_control_and_overlength() -> None:
    source = _source_frame(48)
    source = source.astype(object)
    raw_markers: list[str] = []
    for category_index, column in enumerate(SOURCE_CATEGORY_COLUMNS):
        marker = f"RAW_CATEGORY_SENTINEL_{category_index:02d}"
        raw_markers.append(marker)
        source.loc[0, column] = marker
        source.loc[1, column] = f'=HYPERLINK("https://invalid.example","{marker}")'
        source.loc[2, column] = f"{marker}\x00CONTROL"
        source.loc[3, column] = marker + ("X" * 1000)

    for column_index, column in enumerate(
        (
            "Customer Name: Customer Name",
            "Subscription Reference Id",
            "SR Number",
            "Case Number",
            "Title",
            "Transaction ID",
            "Date/Time Opened",
            "Date/Time Closed",
            "Current Contact Email",
            "Case Owner: Full Name",
            "Problem Description",
            "CSE Action Plan",
            "Last Cisco Update",
            "Resolution Summary",
            "Problem Details",
            "Customer Activity",
        )
    ):
        marker = f"RAW_NONCATEGORY_SENTINEL_{column_index:02d}"
        raw_markers.append(marker)
        source.loc[4, column] = marker

    replay = pseudonymize_csone_frame(
        source,
        build_scenario_bundle("multi_manager"),
        max_rows=len(source),
    )
    serialized = replay.to_json(date_format="iso") + json.dumps(
        replay.attrs,
        sort_keys=True,
        default=str,
    )

    assert not any(marker in serialized for marker in raw_markers)
    assert "HYPERLINK" not in serialized
    assert "\x00" not in serialized
    assert "X" * 100 not in serialized
    assert not any(
        str(value).lstrip().startswith(("=", "+", "-", "@"))
        for value in replay.astype(object)
        .where(pd.notna(replay), "")
        .astype(str)
        .to_numpy()
        .ravel()
    )
    assert replay["Severity"].isin({"P1", "P2", "P3", "P4", "Unknown"}).all()
    assert replay["Case Status"].isin({"Open", "Closed", "Unknown"}).all()
    assert "Webex Calling" in set(replay["Technology"])
    assert replay["Service Tier"].isin(
        {"Premium", "Enhanced", "Standard", "Other / Unclassified", "Not provided"}
    ).all()
    assert replay["Problem Code"].str.fullmatch(
        r"(?:Problem category \d{2}|Not provided)"
    ).all()
    assert replay["Resolution Code"].str.fullmatch(
        r"(?:Resolution category \d{2}|Not provided)"
    ).all()
    assert set(replay["# of Case Owner Changes"].dropna().astype(int)).issubset(
        {0, 1, 2, 3}
    )
    assert canonical.count_priority_breakdown(replay)["P1"] > 0
    assert any(
        item["label"] == "Webex Calling"
        for item in canonical.tac_theme_summary(replay)
    )
    privacy_contract = replay.attrs["privacy_contract"]
    assert set(privacy_contract) == {
        "schema_version",
        "validated",
        "raw_values_retained",
        "row_count",
        "column_count",
        "text_cell_count",
        "date_cell_count",
        "bucketed_cell_count",
    }
    assert privacy_contract["validated"] is True
    assert privacy_contract["raw_values_retained"] is False


@pytest.mark.parametrize(
    ("column", "unsafe_value", "error"),
    (
        ("Severity", "RAW-SEVERITY", "Severity"),
        ("Case Status", "=1+1", "unsafe output text"),
        ("Technology", "RAW-TECHNOLOGY", "Technology"),
        ("Problem Code", "RAW-PROBLEM", "Problem Code"),
        ("# of Case Owner Changes", 99, "owner-change category"),
        ("ACCOUNT_ID_C", "RAW-ACCOUNT", "ACCOUNT_ID_C"),
    ),
)
def test_privacy_validator_fails_closed_on_tampered_projection(
    column: str,
    unsafe_value: object,
    error: str,
) -> None:
    bundle = build_scenario_bundle("multi_manager")
    replay = pseudonymize_csone_frame(_source_frame(12), bundle, max_rows=12)
    replay.loc[0, column] = unsafe_value

    with pytest.raises(ValueError, match=error):
        validate_pseudonymous_csone_frame(replay, as_of_utc=bundle.as_of_utc)


def test_privacy_validator_accepts_an_empty_bems_subset() -> None:
    bundle = build_scenario_bundle("multi_manager")
    replay = pseudonymize_csone_frame(_source_frame(12), bundle, max_rows=12)
    empty_bems = replay.iloc[0:0].copy()

    contract = validate_pseudonymous_csone_frame(
        empty_bems,
        as_of_utc=bundle.as_of_utc,
    )

    assert contract["validated"] is True
    assert contract["raw_values_retained"] is False
    assert contract["row_count"] == 0
    assert contract["text_cell_count"] == 0


def test_privacy_validator_rejects_tampering_in_every_retained_column() -> None:
    bundle = build_scenario_bundle("multi_manager")
    replay = pseudonymize_csone_frame(_source_frame(12), bundle, max_rows=12)

    for column in replay.columns:
        tampered = replay.copy()
        if column in {"Date/Time Opened", "Date/Time Closed", "# of Case Owner Changes"}:
            tampered[column] = tampered[column].astype(object)
        tampered.loc[0, column] = "RAW_OUTPUT_SENTINEL"
        with pytest.raises(ValueError):
            validate_pseudonymous_csone_frame(
                tampered,
                as_of_utc=bundle.as_of_utc,
            )

    unexpected_column = replay.copy()
    unexpected_column["Unexpected Raw Column"] = "RAW_OUTPUT_SENTINEL"
    with pytest.raises(ValueError, match="output schema"):
        validate_pseudonymous_csone_frame(
            unexpected_column,
            as_of_utc=bundle.as_of_utc,
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
    bems = replayed.frame("bems_cases")
    assert bems.attrs["privacy_contract"]["validated"] is True
    assert bems.attrs["privacy_contract"]["row_count"] == len(bems)
    assert bems.attrs["raw_values_retained"] is False


def test_strict_replay_uses_metadata_chronology_volume_and_schema_edge(
    tmp_path: Path,
) -> None:
    corpus, specs = _stratified_corpus(tmp_path)
    calls: list[str] = []

    def loader(raw_path: str) -> pd.DataFrame:
        name = Path(raw_path).name
        calls.append(name)
        _modified, rows, schema_edge = specs[name]
        frame = _source_frame(rows)
        if schema_edge:
            frame["Synthetic Schema Edge"] = "present"
        return frame

    replayed = replay_bundle_from_corpus(
        build_scenario_bundle("multi_manager"),
        corpus,
        loader=loader,
        max_rows=24,
        strict_breadth=True,
    )

    tac = replayed.frame("tac_cases")
    coverage = tac.attrs["corpus_replay_coverage"]
    assert len(tac) == 24
    assert coverage["breadth_ok"] is True
    assert coverage["chronology_basis"] == "workbook_modified"
    assert coverage["candidate_workbook_count"] == 5
    assert coverage["selected_workbook_count"] == 4
    assert coverage["selected_files_with_replay_rows"] == 4
    assert set(coverage["covered_strata"]) == {
        "old",
        "mid",
        "new",
        "high_volume",
        "schema_edge",
    }
    assert all(item["replay_row_count"] > 0 for item in coverage["selected_files"])
    assert all(
        coverage["strata"][label]["replay_row_count"] > 0
        for label in ("old", "mid", "new", "high_volume", "schema_edge")
    )

    call_counts = Counter(calls)
    # Every candidate is profiled exactly once. Only the four selected files
    # are then reloaded one at a time for bounded sampling.
    assert set(call_counts) == set(specs)
    assert call_counts["middle-ordinary.xlsx"] == 1
    assert sum(count == 2 for count in call_counts.values()) == 4
    serialized = json.dumps(coverage, sort_keys=True)
    assert not any(name in serialized for name in specs)
    assert "Sensitive" not in serialized
    assert "REAL-SR" not in tac.to_json(date_format="iso")


def test_strict_replay_fails_closed_when_required_breadth_is_unprovable(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "too-narrow"
    corpus.mkdir()
    _synthetic_core_workbook(corpus / "one.xlsx", "2026-01-01T00:00:00Z")
    _synthetic_core_workbook(corpus / "two.xlsx", "2026-02-01T00:00:00Z")

    with pytest.raises(ValueError, match="fewer_than_three_nonempty_workbooks"):
        replay_bundle_from_corpus(
            build_scenario_bundle("multi_manager"),
            corpus,
            loader=lambda _path: _source_frame(12),
            max_rows=20,
            strict_breadth=True,
        )


def test_strict_replay_fails_when_max_rows_cannot_cover_selected_files(
    tmp_path: Path,
) -> None:
    corpus, specs = _stratified_corpus(tmp_path)

    def loader(raw_path: str) -> pd.DataFrame:
        _modified, rows, schema_edge = specs[Path(raw_path).name]
        frame = _source_frame(rows)
        if schema_edge:
            frame["Synthetic Schema Edge"] = "present"
        return frame

    with pytest.raises(ValueError, match="max_rows_below_selected_file_count"):
        replay_bundle_from_corpus(
            build_scenario_bundle("multi_manager"),
            corpus,
            loader=loader,
            max_rows=3,
            strict_breadth=True,
        )


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


def test_replay_cli_summary_exposes_only_aggregate_operating_health(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.run_csone_corpus_replay as runner

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for index, modified in enumerate(
        (
            "2024-01-01T00:00:00Z",
            "2025-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
        start=1,
    ):
        _synthetic_core_workbook(corpus / f"representative-{index}.xlsx", modified)
    original_loader = runner.backend.load_csone_excel
    runner.backend.load_csone_excel = lambda _path: _source_frame()
    try:
        summary = runner.run_replay(corpus, max_rows=30)
    finally:
        runner.backend.load_csone_excel = original_loader

    health = summary["replay"]["operating_health"]
    coverage = summary["replay"]["corpus_coverage"]
    assert health["available"] is True
    assert health["closed_case_count"] > 0
    assert coverage["breadth_ok"] is True
    assert coverage["selected_workbook_count"] == 3
    assert set(("old", "mid", "new")).issubset(coverage["covered_strata"])
    # The replay hashes and stratifies source rows before projecting IDs, so
    # exact count equality to input ordering is intentionally not assumed.
    assert 0 < health["ownership_observed_count"] <= 30
    serialized = json.dumps(summary, sort_keys=True)
    assert "Sensitive" not in serialized
    assert "REAL-SR" not in serialized

    summary_path = tmp_path / "aggregate-summary.json"
    runner.backend.load_csone_excel = lambda _path: _source_frame()
    try:
        exit_code = runner.main(
            [
                "--input-dir",
                str(corpus),
                "--max-rows",
                "30",
                "--summary",
                str(summary_path),
            ]
        )
    finally:
        runner.backend.load_csone_excel = original_loader
    public_stdout = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert public_stdout == {
        "all_passed": True,
        "privacy_contract_ok": True,
        "replay_row_count": 30,
        "representative_workbook_count": 3,
    }
    assert str(summary_path) not in json.dumps(public_stdout)
