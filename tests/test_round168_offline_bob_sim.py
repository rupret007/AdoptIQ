"""Round 168 / Round 169.1: Cloud/Bob offline simulation contracts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pandas as pd

from adoptiq_backend import load_csone_excel
from csone_corpus_replay import (
    discover_corpus_workbooks,
    replay_bundle_from_corpus,
    validate_representative_loaders,
)
from report_iteration_loop import select_latest_baseline
from local_acceptance_lab import SOURCE_MODE, build_scenario_bundle
from scripts.generate_synthetic_csone_corpus import (
    DEFAULT_OUTPUT_DIR,
    generate_synthetic_csone_corpus,
    project_tac_to_csone_shape,
)
from scripts.check_offline_sim_ci_surface import run_ci_surface_check
from scripts.run_metamorphic_acceptance import run_metamorphic_acceptance


ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "OFFLINE_SIM_PLAYBOOK.md"
MAKEFILE = ROOT / "Makefile"
SIM_SCRIPT = ROOT / "scripts" / "run_offline_bob_sim.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "offline-sim.yml"
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
SYNTHETIC = ROOT / "testdata" / "synthetic_csone"


def _run_resolve(env: dict[str, str]) -> dict:
    merged = os.environ.copy()
    for key in ("CSONE_CORPUS_DIR", "SYNTHETIC_CSONE_DIR", "OFFLINE_SIM_PROFILE"):
        merged.pop(key, None)
    merged.update(env)
    result = subprocess.run(
        ["bash", str(SIM_SCRIPT), "--resolve-only"],
        cwd=str(ROOT),
        env=merged,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_playbook_lists_cloud_commands_and_honesty() -> None:
    text = PLAYBOOK.read_text(encoding="utf-8")
    assert "make offline-sim-pr" in text
    assert "make offline-sim" in text
    assert "make verify" in text
    assert "make local-acceptance-lab" in text
    assert "make metamorphic-acceptance" in text
    assert "make offline-pipeline-smoke" in text
    assert "make jeff-only-stubs" in text
    assert "make offline-sim-ci-surface" in text
    assert "WORK_MAC_CURSOR_HANDOFF.md" in text
    assert "CSONE_CORPUS_DIR" in text
    assert "testdata/synthetic_csone" in text
    assert "live_validation_performed=false" in text
    assert "blocked_in_repo_corpus" in text
    assert "Build 114" in text
    assert "Build 115" in text
    assert "invalidated" in text.casefold()
    assert "Build 116" in text
    assert "run_round169_metamorphic_acceptance.py" in text
    assert "bake_corpus.py" in text
    assert "release_ready=false" in text
    assert "production_accuracy_claimed=false" in text


def test_makefile_wires_offline_targets() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n")
    assert "offline-sim-pr" in text
    assert "offline-sim:" in text
    assert "offline-sim-ci-surface:" in text
    assert "offline-sim-local:" in text
    assert "hosted-actions-classify:" in text
    assert "synthetic-csone-metrics:" in text
    assert "metamorphic-acceptance:" in text
    assert "offline-pipeline-smoke:" in text
    assert "jeff-only-stubs:" in text
    assert "synthetic-csone:" in text
    assert "run_offline_bob_sim.sh --profile pr" in text
    assert "Round 168" in text
    assert "Round 169" in text
    # Round 169.1: Makefile SSoT stays on the official Round 169 runner.
    recipe_start = normalized.find("metamorphic-acceptance:")
    recipe = normalized[recipe_start : recipe_start + 400]
    assert "scripts/run_round169_metamorphic_acceptance.py" in recipe
    assert "scripts/run_metamorphic_acceptance.py" not in recipe


def test_pr_workflow_exists_and_stays_secret_free() -> None:
    # Round 169.4: PR CI lives on build.yml (last runner-assigned workflow).
    # offline-sim.yml is dispatch-only so it cannot create empty PR checks.
    dispatch = WORKFLOW.read_text(encoding="utf-8")
    build = BUILD_WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in build
    assert "offline-sim-pr:" in build
    assert "make offline-sim-pr" in build
    assert "if: github.event_name == 'pull_request'" in build
    assert "pull_request:" not in dispatch
    assert "make offline-sim-pr" in dispatch
    assert "check_offline_sim_ci_surface.py" in dispatch
    assert "pip install ruff bandit pip-audit" in dispatch
    assert "secrets" not in dispatch.casefold()
    assert "SNOWFLAKE_PASSWORD" not in dispatch
    assert "KEEPER" not in dispatch
    assert "contents: read" in dispatch


def test_sim_script_refuses_in_repo_non_synthetic_corpus() -> None:
    text = SIM_SCRIPT.read_text(encoding="utf-8")
    assert "blocked_in_repo_corpus" in text
    assert "testdata/synthetic_csone" in text
    assert "live_validation_performed=false" in text
    assert "--resolve-only" in text
    # Round 169.1: official SSoT plus complementary fixture-KPI gate.
    assert "run_round169_metamorphic_acceptance.py" in text
    assert "run_metamorphic_acceptance.py" in text
    assert "fixture-kpi-metamorphic" in text


def test_resolve_only_uses_checked_in_synthetic_corpus() -> None:
    payload = _run_resolve({})
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["corpus_kind"] == "synthetic_checked_in"
    assert payload["corpus_dir_recorded"] is True


def test_resolve_only_skips_when_no_corpus(tmp_path: Path) -> None:
    payload = _run_resolve({"SYNTHETIC_CSONE_DIR": str(tmp_path / "missing")})
    assert payload["corpus_kind"] == "skipped_no_corpus"
    assert payload["corpus_dir_recorded"] is False
    assert payload["live_validation_performed"] is False


def test_resolve_only_blocks_in_repo_xlsx(tmp_path: Path) -> None:
    blocked = ROOT / ".adoptiq-acceptance" / "round168-blocked-corpus"
    blocked.mkdir(parents=True, exist_ok=True)
    (blocked / "not-for-git.xlsx").write_bytes(b"PK")
    payload = _run_resolve({"CSONE_CORPUS_DIR": str(blocked)})
    assert payload["corpus_kind"] == "blocked_in_repo_corpus"
    assert payload["corpus_dir_recorded"] is False


def test_resolve_only_allows_external_xlsx(tmp_path: Path) -> None:
    (tmp_path / "external.xlsx").write_bytes(b"PK")
    payload = _run_resolve({"CSONE_CORPUS_DIR": str(tmp_path)})
    assert payload["corpus_kind"] == "external_operator_dir"
    assert payload["corpus_dir_recorded"] is True
    assert payload["live_validation_performed"] is False


def test_synthetic_corpus_is_fixture_safe() -> None:
    manifest = json.loads((SYNTHETIC / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["live_validation_performed"] is False
    assert manifest["production_accuracy_claimed"] is False
    assert manifest["synthetic"] is True
    assert manifest["email_domain"] == "example.invalid"
    assert set(manifest["customer_names"]) == {
        "Acme Corporation",
        "Beta Industries",
        "Gamma Public Sector",
    }
    workbooks = discover_corpus_workbooks(SYNTHETIC)
    assert len(workbooks) == 3
    joined = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in workbooks)
    assert "@cisco.com" not in joined.casefold()
    assert "live_validation_performed=true" not in joined
    loaded = load_csone_excel(workbooks[0])
    assert not loaded.empty
    assert int(loaded.attrs.get("excluded_non_record_rows") or 0) >= 1
    emails = loaded.get("Current Contact Email", pd.Series(dtype="object")).fillna("").astype(str)
    assert emails.map(lambda value: "@" not in value or value.endswith("@example.invalid")).all()
    customers = loaded.get("Customer", pd.Series(dtype="object")).fillna("").astype(str)
    assert set(customers.unique()) <= {
        "Acme Corporation",
        "Beta Industries",
        "Gamma Public Sector",
    }


def test_synthetic_projection_stays_on_fixture_customers() -> None:
    bundle = build_scenario_bundle("healthy")
    projected = project_tac_to_csone_shape(bundle.frame("tac_cases"))
    assert projected.attrs["live_validation_performed"] is False
    assert projected.attrs["source_mode"] == SOURCE_MODE
    assert projected["Current Contact Email"].eq("fixture.contact1@example.invalid").all()
    assert "cisco.com" not in projected.to_json().casefold()


def test_synthetic_loader_and_replay_are_honest(tmp_path: Path) -> None:
    output = tmp_path / "synthetic_csone"
    generate_synthetic_csone_corpus(output)
    contract = validate_representative_loaders(output, loader=load_csone_excel)
    assert contract["all_nonempty"] is True
    assert contract["no_footer_rows_remaining"] is True
    assert contract["consistent_schema"] is True
    replayed = replay_bundle_from_corpus(
        build_scenario_bundle("multi_manager"),
        output,
        loader=load_csone_excel,
        max_rows=24,
    )
    tac = replayed.frame("tac_cases")
    assert tac.attrs["corpus_replay"] is True
    assert tac.attrs["live_validation_performed"] is False
    assert tac.attrs["raw_values_retained"] is False
    serialized = tac.to_json()
    assert "Pseudonymized" in serialized
    assert "@example.invalid" in serialized
    assert "cisco.com" not in serialized.casefold()


def test_offline_sim_ci_surface_proves_workflow_and_targets_exist() -> None:
    payload = run_ci_surface_check()
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert payload["all_passed"] is True
    failed = [item["name"] for item in payload["checks"] if not item["passed"]]
    assert failed == []


def test_metamorphic_acceptance_is_fixture_only_and_green() -> None:
    payload = run_metamorphic_acceptance()
    assert payload["all_passed"] is True
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert payload["source_mode"] == SOURCE_MODE
    failed = [item["name"] for item in payload["checks"] if not item["passed"]]
    assert failed == []


def test_default_synthetic_dir_is_testdata() -> None:
    assert DEFAULT_OUTPUT_DIR == SYNTHETIC.resolve()


def test_round51_current_run_dump_never_wins_baseline(tmp_path: Path) -> None:
    """Current-run dumps without a trailing `__` must not beat the real baseline."""
    newest = tmp_path / "AdoptIQ_Report_Compact_All_Managers_All_Contact_Center_90d_222.docx"
    older = tmp_path / "AdoptIQ_Report_Compact_All_Managers_All_Contact_Center_90d_111.docx"
    ignored = tmp_path / "AdoptIQ_Report_Compact_All_Managers_All_Contact_Center_90d_333__data-loop-current.docx"
    production_dump = tmp_path / (
        "AdoptIQ_Report_Compact_All_Managers_All_Contact_Center_90d_"
        "444__data-loop-current__scenario-compact__ts-20260823T000000Z.docx"
    )
    for path in (older, newest, ignored, production_dump):
        path.write_text("x", encoding="utf-8")
    epoch = 1_700_000_000
    os.utime(older, (epoch, epoch))
    os.utime(newest, (epoch + 10, epoch + 10))
    os.utime(ignored, (epoch + 50, epoch + 50))
    os.utime(production_dump, (epoch + 60, epoch + 60))
    selected = select_latest_baseline(
        downloads_dir=tmp_path,
        scenario_key="compact",
        extension="docx",
        run_id="current",
        current_debug_name="unrelated-current.docx",
    )
    assert selected == newest


def test_new_files_have_round_168_markers() -> None:
    for path in (
        PLAYBOOK,
        MAKEFILE,
        SIM_SCRIPT,
        WORKFLOW,
        ROOT / "scripts" / "generate_synthetic_csone_corpus.py",
        ROOT / "scripts" / "run_metamorphic_acceptance.py",
        ROOT / "scripts" / "run_offline_pipeline_smoke.py",
        ROOT / "scripts" / "run_jeff_only_stubs.py",
        ROOT / "scripts" / "check_offline_sim_ci_surface.py",
    ):
        text = path.read_text(encoding="utf-8")
        assert "Round 168" in text or "Round 169" in text
