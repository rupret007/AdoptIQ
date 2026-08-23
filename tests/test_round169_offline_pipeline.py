"""Round 169 / Round 169.1: Cloud/Bob offline pipeline and work-Mac handoff."""

from __future__ import annotations

import json
from pathlib import Path

from decision_report_delivery import SOURCE_DATA_SHEET_NAMES
from scripts.run_jeff_only_stubs import run_jeff_only_stubs
from scripts.run_offline_pipeline_smoke import UX_PATHS, probe_manager_ux


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "WORK_MAC_CURSOR_HANDOFF.md"
PLAYBOOK = ROOT / "OFFLINE_SIM_PLAYBOOK.md"
MAKEFILE = ROOT / "Makefile"
SIM_SCRIPT = ROOT / "scripts" / "run_offline_bob_sim.sh"
PIPELINE = ROOT / "scripts" / "run_offline_pipeline_smoke.py"
STUBS = ROOT / "scripts" / "run_jeff_only_stubs.py"


def test_work_mac_handoff_is_honest_and_actionable() -> None:
    text = HANDOFF.read_text(encoding="utf-8")
    assert "live_validation_performed=false" in text
    assert "production_accuracy_claimed=false" in text
    assert "release_ready=false" in text
    assert "Build 114" in text
    assert "Build 115" in text
    assert "invalidated" in text.casefold()
    assert "Build 116" in text
    assert "WORK_MACHINE_BUILD116_PROMPT.md" in text
    assert "make offline-sim" in text
    assert "make verify" in text
    assert "CSONE_CORPUS_DIR" in text
    assert "NEXT_MACHINE_PROMPT.md" in text
    assert "--mode live" in text
    assert "SNOWFLAKE_PASSWORD" not in text
    assert "Keeper" in text  # named as Jeff-only, not a secret value
    assert "@cisco.com" not in text.casefold()


def test_playbook_and_sim_wire_round169_pipeline() -> None:
    playbook = PLAYBOOK.read_text(encoding="utf-8")
    script = SIM_SCRIPT.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "offline-pipeline-smoke" in playbook
    assert "jeff-only-stubs" in playbook
    assert "WORK_MAC_CURSOR_HANDOFF.md" in playbook
    assert "run_round169_metamorphic_acceptance.py" in playbook
    assert "run_offline_pipeline_smoke.py" in script
    assert "run_jeff_only_stubs.py" in script
    assert "run_round169_metamorphic_acceptance.py" in script
    assert "offline-pipeline-smoke:" in makefile
    assert "jeff-only-stubs:" in makefile
    assert "source-contracts:" in makefile


def test_pipeline_and_stub_scripts_stay_secret_free() -> None:
    joined = "\n".join(
        path.read_text(encoding="utf-8") for path in (PIPELINE, STUBS, HANDOFF, PLAYBOOK)
    )
    assert "SNOWFLAKE_PASSWORD" not in joined
    assert "ADOPTIQ_ADMIN_SECRET_KEY" not in joined
    assert "@cisco.com" not in joined.casefold()
    assert "live_validation_performed=true" not in joined
    assert "Round 169" in PIPELINE.read_text(encoding="utf-8")
    assert "Round 169" in STUBS.read_text(encoding="utf-8")


def test_canonical_source_workbook_has_exactly_17_sheets() -> None:
    assert len(SOURCE_DATA_SHEET_NAMES) == 17
    assert SOURCE_DATA_SHEET_NAMES[0] == "Report_Info"
    assert "Evidence_Links" in SOURCE_DATA_SHEET_NAMES
    assert "Defect_Correlations" in SOURCE_DATA_SHEET_NAMES


def test_manager_ux_paths_are_fixture_safe() -> None:
    assert "/" in UX_PATHS
    assert "/preferences" in UX_PATHS
    assert "/ask-ai" in UX_PATHS
    assert "/leader_report_form" in UX_PATHS
    payload = probe_manager_ux()
    assert payload["live_validation_performed"] is False
    assert payload["ok"] is True
    missing = [item["path"] for item in payload["paths"] if not item["ok"]]
    assert missing == []


def test_jeff_only_stubs_fail_closed_and_stay_honest() -> None:
    output = ROOT / ".adoptiq-acceptance" / "round169-pytest-stubs"
    payload = run_jeff_only_stubs(output)
    assert payload["live_validation_performed"] is False
    assert payload["production_accuracy_claimed"] is False
    assert payload["release_ready"] is False
    assert payload["all_passed"] is True
    names = {item["name"] for item in payload["checks"]}
    assert "live_decision_reports_fail_closed" in names
    assert "work_machine_profile_requires_dmg" in names
    assert "live_snowflake_keeper" in names
    assert "build114_must_stay_invalidated" in names
    assert "build115_must_stay_invalidated" in names
    assert "build116_package_smoke_promote" in names
