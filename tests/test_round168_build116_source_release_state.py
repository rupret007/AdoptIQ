"""Round 168 source identity and fail-closed Build 115 invalidation contracts."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts import release_candidate_contract as contract


ROOT = Path(__file__).resolve().parents[1]
BUILD115 = ROOT / "release_candidates" / "macos-build115"


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_source_identity_is_pending_build116() -> None:
    tree = ast.parse(_text("config.py"))
    values = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    assert values["ADOPTIQ_VERSION"] == "1.0.4"
    assert values["ADOPTIQ_BUILD"] == "116"


def test_mac_release_preflight_banner_is_build_neutral() -> None:
    source = _text("build_mac_dmg.sh")
    assert "AdoptIQ macOS release preflight" in source
    assert "Build 115: macOS release preflight" not in source
    assert '--expected-build "${ADOPTIQ_BUILD:-}"' in source


def test_build115_identity_is_preserved_but_invalidated() -> None:
    manifest_path = BUILD115 / "candidate.json"
    loaded = contract.load_release_candidate_manifest(manifest_path)
    assert loaded.release_status == "invalidated"
    assert loaded.build == 115
    assert loaded.source_commit_sha == "d972c367b6eff07ce3095c39c3136af437517b6f"
    assert loaded.artifact.name == "AdoptIQ-v1.0.4-build115.dmg"
    assert loaded.artifact.sha256 == (
        "6e828e896510d75647c9db126d99a48be57384f31305f7c32b3da317739096d0"
    )
    assert loaded.artifact.size_bytes == 1711563863
    assert loaded.built_at_utc == "2026-08-13T23:51:26Z"
    assert contract.verify_release_candidate_sidecars(manifest_path) == loaded

    build_info = (BUILD115 / "build_info.txt").read_bytes()
    assert hashlib.sha256(build_info).hexdigest() == (
        "54529ca61d3057ef87a98493b6d797df767ca3841d3bf274941e9322f8c315cf"
    )
    assert "INVALIDATED / NO-GO" in _text(
        "release_candidates/macos-build115/README.md"
    )


def test_build115_manual_review_is_fail_closed() -> None:
    payload = json.loads(
        _text("release_candidates/macos-build115/manual-review.template.json")
    )
    assert payload["candidate"]["release_status"] == "invalidated"
    assert payload["release_recommendation"] == "no-go"
    assert payload["manual_source_reconciliation_complete"] is False
    assert payload["visual_review_complete"] is False
    assert payload["mismatch_count"] is None


def test_invalidated_build115_cannot_verify_even_with_matching_name(tmp_path: Path) -> None:
    loaded = contract.load_release_candidate_manifest(BUILD115 / "candidate.json")
    artifact = tmp_path / loaded.artifact.name
    artifact.write_bytes(b"not-the-historical-bytes")
    with pytest.raises(contract.ReleaseCandidateContractError, match="invalidated"):
        contract.verify_release_candidate(BUILD115 / "candidate.json", artifact)


def test_active_handoffs_are_build116_pending_and_copy_prompt_is_bounded() -> None:
    active = (
        "README.md",
        "BRANCH_WORKFLOW.md",
        "CURSOR_MAC_BUILD_INSTRUCTIONS.md",
        "CODEX_HANDOFF_PROMPT.md",
        "HANDOFF_PROMPT.md",
        "NEXT_MACHINE_PROMPT.md",
    )
    for relative in active:
        source = _text(relative)
        assert "Build 116" in source, relative
        assert "WORK_MACHINE_BUILD115_PROMPT.md` as the only current" not in source

    prompt_path = ROOT / "WORK_MACHINE_BUILD116_PROMPT.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    assert len(prompt_path.read_bytes()) <= 4_000
    assert "Build 115 is invalidated/NO-GO" in prompt
    assert "Build 116 is source-only" in prompt
    assert "no candidate identity" in prompt
    assert re.search(r"\b[0-9a-f]{64}\b", prompt) is None

    historical = _text("WORK_MACHINE_BUILD115_PROMPT.md")
    assert historical.startswith("# Historical only")
    assert "must not be run" in historical


def test_readme_hosted_lane_is_developer_candidate_only() -> None:
    readme = _text("README.md")
    assert "All GitHub-hosted Actions builds are **developer-candidate-only**" in readme
    assert "Hosted jobs reject release/tag modes" in readme
    assert "Tag/release builds retain the production path" not in readme


def test_build116_runbook_uses_real_candidate_creator_cli_flag() -> None:
    runbook = _text("NEXT_MACHINE_PROMPT.md")

    assert '--source-commit-sha "$(git rev-parse HEAD)"' in runbook
    assert re.search(r"--source-commit(?:\s|\")", runbook) is None
    copy = 'cp -n -- OUTBOX/build_info.txt "$CANDIDATE_DIR/build_info.txt"'
    compare = 'cmp -s OUTBOX/build_info.txt "$CANDIDATE_DIR/build_info.txt"'
    assert copy in runbook
    assert runbook.index(copy) < runbook.index(compare)
    manifest = '"$CANDIDATE_DIR/candidate.json"'
    review = '"$CANDIDATE_DIR/manual-review.template.json"'
    assert "scripts/create_manual_review_template.py" in runbook
    assert f"--candidate-manifest {manifest}" in runbook
    assert f"--output {review}" in runbook
    assert runbook.index("scripts/create_release_candidate.py") < runbook.index(
        "scripts/create_manual_review_template.py"
    )


def test_build116_candidate_evidence_workflow_is_fail_fast_and_create_only() -> None:
    runbook = _text("NEXT_MACHINE_PROMPT.md")

    directory_guard = '[[ -e "$CANDIDATE_DIR" || -L "$CANDIDATE_DIR" ]]'
    directory_create = 'mkdir -- "$CANDIDATE_DIR"'
    manual_stop = "**STOP here.**"
    target_guard = '[[ -e "$TARGET" || -L "$TARGET" ]]'
    copy = 'cp -n -- OUTBOX/build_info.txt "$CANDIDATE_DIR/build_info.txt"'
    readme_guard = (
        '[[ ! -f "$CANDIDATE_DIR/README.md" || '
        '-L "$CANDIDATE_DIR/README.md" ]]'
    )
    assert runbook.count("set -euo pipefail") >= 3
    assert directory_guard in runbook
    assert target_guard in runbook
    assert readme_guard in runbook
    assert '"$CANDIDATE_DIR/candidate.json"' in runbook
    assert '"$CANDIDATE_DIR/manual-review.template.json"' in runbook
    assert runbook.index(directory_guard) < runbook.index(directory_create)
    assert runbook.index(directory_create) < runbook.index(manual_stop)
    assert runbook.index(manual_stop) < runbook.index(target_guard)
    assert runbook.index(target_guard) < runbook.index(copy)


def test_build116_runbook_distinguishes_local_staging_from_consumer_manifest() -> None:
    runbook = _text("NEXT_MACHINE_PROMPT.md")

    assert "ignored local\n`OUTBOX/latest.json` staging metadata" in runbook
    assert "must remain untracked and unpublished" in runbook
    assert "consumer or\nOneDrive `latest.json`" in runbook


def test_stale_build_guides_are_explicitly_historical_and_route_to_build116() -> None:
    stale_guides = (
        "BUILD_WINDOWS.md",
        "CURSOR_BUILD_GUIDE.md",
        "CURSOR_PC_BUILD_INSTRUCTIONS.md",
        "WORK_MACHINE_ROLLOUT.md",
    )
    for relative in stale_guides:
        source = _text(relative)
        normalized = source.replace("\n> ", " ")
        assert source.startswith("# Historical only — do not execute"), relative
        assert "NEXT_MACHINE_PROMPT.md" in source, relative
        assert "WORK_MACHINE_BUILD116_PROMPT.md" in source, relative
        assert "Build 115 is invalidated" in normalized, relative
        assert "Build 116 is source-only" in normalized, relative
        assert "Do not execute" in source or "Do not run" in source, relative

    claude = _text("CLAUDE.md")
    assert "`NEXT_MACHINE_PROMPT.md` is the only active release runbook" in claude
    assert "`WORK_MACHINE_BUILD116_PROMPT.md`" in claude
    assert "`WORK_MACHINE_ROLLOUT.md` and older PC/build guides are historical" in claude
