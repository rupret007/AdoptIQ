"""Round 142: Source Data filenames remain compatible with legacy Data files."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

from report_iteration_loop import _filename_matches_scenario


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_r114_module():
    path = REPO_ROOT / "scripts" / "r114_audit_reports.py"
    spec = importlib.util.spec_from_file_location("r114_audit_reports_round142", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_app_group_helper():
    """Execute the pure grouping helper without importing the Flask monolith."""
    source = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_R142_ARTIFACT_SUFFIX_RE"
            for target in node.targets
        ):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_r142_report_group_id":
            selected.append(node)
    assert len(selected) == 2
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    namespace = {"re": re}
    exec(compile(module, str(REPO_ROOT / "app_simple.py"), "exec"), namespace)  # noqa: S102
    return namespace["_r142_report_group_id"]


@pytest.mark.parametrize("prefix", ("AdoptIQ_Source_Data_", "AdoptIQ_Data_"))
@pytest.mark.parametrize(
    ("scenario", "suffix"),
    (
        ("comprehensive", "Brian_Frazier_All_Contact_Center_90d_123.xlsx"),
        ("compact", "Compact_All_Managers_All_Contact_Center_90d_123.xlsx"),
        ("renewal", "Renewal_Portfolio_All_Managers_All_Contact_Center_90d_123.xlsx"),
        ("leader", "Leader_Brian_Frazier_90d_123.xlsx"),
    ),
)
def test_iteration_harness_accepts_current_and_legacy_workbook_names(prefix, scenario, suffix):
    assert _filename_matches_scenario(prefix + suffix, scenario, "xlsx") is True


def test_iteration_harness_rejects_unrecognized_workbook_prefix():
    filename = "AdoptIQ_Source_Compact_All_Managers_All_Contact_Center_90d_123.xlsx"
    assert _filename_matches_scenario(filename, "compact", "xlsx") is False


def test_r114_prefers_exact_source_data_partner_but_keeps_legacy(tmp_path):
    r114 = _load_r114_module()
    base = tmp_path / "AdoptIQ_Report_Leader_Brian_Frazier_90d_20260803_120000"
    source_data = tmp_path / "AdoptIQ_Source_Data_Leader_Brian_Frazier_90d_20260803_120000.xlsx"
    legacy_data = tmp_path / "AdoptIQ_Data_Leader_Brian_Frazier_90d_20260803_120000.xlsx"
    source_data.write_bytes(b"source")
    legacy_data.write_bytes(b"legacy")

    assert r114._resolve_xlsx_for_base(base) == source_data

    source_data.unlink()
    assert r114._resolve_xlsx_for_base(base) == legacy_data


def test_r114_resolves_source_data_harness_timestamp_drift(tmp_path):
    r114 = _load_r114_module()
    base = tmp_path / (
        "AdoptIQ_Report_Leader_Brian_Frazier_90d_1"
        "__data-loop-round142__scenario-leader__ts-20260803T120000Z"
    )
    source_data = tmp_path / (
        "AdoptIQ_Source_Data_Leader_Brian_Frazier_90d_1"
        "__data-loop-round142__scenario-leader__ts-20260803T120001Z.xlsx"
    )
    source_data.write_bytes(b"source")

    assert r114._resolve_xlsx_for_base(base) == source_data


def test_previous_reports_groups_word_current_and_legacy_workbooks_together():
    group_id = _load_app_group_helper()
    expected = "Leader_Brian_Frazier_90d"
    names = (
        "AdoptIQ_Report_Leader_Brian_Frazier_90d_20260803_120000.docx",
        "AdoptIQ_Source_Data_Leader_Brian_Frazier_90d_20260803_120000.xlsx",
        "AdoptIQ_Data_Leader_Brian_Frazier_90d_20260803_120000.xlsx",
    )
    assert {group_id(name) for name in names} == {expected}


def test_source_data_download_name_and_historical_patterns_are_wired():
    app_source = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    backend_source = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert 'download_name=f"AdoptIQ_Source_Data_{safe_name}.xlsx"' in app_source
    assert "'AdoptIQ_Source_Data_*.xlsx'" in backend_source
    assert "'AdoptIQ_Data_*.xlsx'" in backend_source
    assert "'AdoptIQ_Report_*.xlsx'" in backend_source


def test_source_data_requires_corpus_quality_sidecar_and_legacy_still_does(tmp_path):
    from corpus_indexer import enumerate_user_report_files

    approved_source = tmp_path / "AdoptIQ_Source_Data_Approved.xlsx"
    missing_source = tmp_path / "AdoptIQ_Source_Data_Missing.xlsx"
    approved_legacy = tmp_path / "AdoptIQ_Data_Approved.xlsx"
    missing_legacy = tmp_path / "AdoptIQ_Data_Missing.xlsx"
    for path in (approved_source, missing_source, approved_legacy, missing_legacy):
        path.write_bytes(b"placeholder")
    for path in (approved_source, approved_legacy):
        path.with_name(path.name + ".adoptiq_corpus.json").write_text(
            json.dumps({"corpus_eligible": True}),
            encoding="utf-8",
        )

    files = enumerate_user_report_files(
        tmp_path,
        recursive=False,
        require_adoptiq_quality_gate=True,
    )
    assert {item.filename for item in files} == {
        approved_source.name,
        approved_legacy.name,
    }
