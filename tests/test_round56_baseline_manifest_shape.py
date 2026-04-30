"""Round 56 -- pin the shape of the Build31 baseline manifest.

The Round 56 plan replaced the stale ``baselines/round52`` snapshot
(taken on Build28 before the Round 53.2 distinct-AB fix) with a fresh
``baselines/round56`` capture against the Build31 .app on :5151.  The
supervisor's default ``--baseline-manifest`` was repointed at the same
time so the autofix loop now diffs current runs against the intended
KPI ground truth.

This test pins the contract that future "rebaseline" rounds (Round
N+M) MUST satisfy when they replace ``baselines/round56`` with a newer
snapshot:

1. The manifest sits at ``baselines/round56/baseline_manifest.json``.
2. It carries the four canonical scenarios (``compact``,
   ``comprehensive``, ``leader``, ``renewal``) -- the same set the
   supervisor invokes by default.  Renaming a scenario or adding a
   fifth one without updating this test is a silent harness break.
3. Each scenario has BOTH a ``docx`` and ``xlsx`` block with a
   non-empty 64-char ``sha256`` and a positive ``size_bytes``.  An
   empty / placeholder hash means the capture didn't actually write
   the artifact and would silently break drift detection.
4. Each ``path`` field points to a file that EXISTS on disk under
   ``baselines/round56/``.  A manifest that references missing files
   would let drift detection silently pass against a phantom baseline.
5. The captured ``adoptiq_build`` matches the current ``Config``
   build at the time the manifest was minted.  Build31 is the floor;
   if a future rebaseline lands on a newer build, this assertion
   tightens automatically (the live ``Config`` advances at the same
   time the new manifest is captured).
6. The supervisor default points at the round56 manifest so the
   autofix loop, when invoked without ``--baseline-manifest``, diffs
   against the intended ground truth.

Round 56 / Phase C source-pin.  Made-with: Cursor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "baselines" / "round56" / "baseline_manifest.json"
EXPECTED_SCENARIOS = {"compact", "comprehensive", "leader", "renewal"}
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "run_report_accuracy_autofix_loop.py"


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST_PATH.exists(), (
        f"baselines/round56/baseline_manifest.json missing -- the Round 56 "
        f"capture step must run before this contract can be pinned. "
        f"Re-run `python3 scripts/run_report_iteration_loop.py "
        f"--init-baseline --init-baseline-dir baselines/round56 ...` "
        f"per the Round 56 handoff."
    )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_round56_manifest_has_top_level_metadata(manifest: dict) -> None:
    """Top-level keys the harness depends on for reproducibility."""

    for key in ("environment", "generated_at_utc", "git_sha", "label", "scenarios", "version"):
        assert key in manifest, f"manifest is missing top-level key {key!r}"
    assert isinstance(manifest["scenarios"], dict)
    assert manifest["version"] == 1, (
        "Manifest schema version pinned at 1; bumping requires a coordinated "
        "harness change in report_iteration_loop.py."
    )


def test_round56_manifest_carries_canonical_four_scenarios(manifest: dict) -> None:
    """The supervisor's default scenario set must round-trip via this manifest."""

    captured = set(manifest["scenarios"].keys())
    assert captured == EXPECTED_SCENARIOS, (
        f"Round 56 baseline must contain exactly the four canonical scenarios "
        f"(compact/comprehensive/leader/renewal). Captured: {sorted(captured)}; "
        f"expected: {sorted(EXPECTED_SCENARIOS)}. The Phase 3.5 typo "
        f"`renewal_portfolio` is NOT a scenario name -- the runner registers "
        f"it under `renewal`."
    )


def test_round56_manifest_has_build31_environment(manifest: dict) -> None:
    """The manifest must record the build it was captured against."""

    env = manifest.get("environment", {})
    build = str(env.get("adoptiq_build", "")).strip()
    assert build, "manifest.environment.adoptiq_build is missing or empty"
    # Round 56 captured against Build31 specifically -- if a future rebaseline
    # lands on a higher build, that's expected and this assertion advances.
    # Going BACKWARDS would mean we re-baselined against an older build, which
    # is a regression worth catching.
    try:
        captured_build = int(build)
    except ValueError:
        pytest.fail(f"adoptiq_build {build!r} is not an integer")
    assert captured_build >= 31, (
        f"Round 56 baseline was captured against build {captured_build}, "
        f"expected >= 31. A re-baselined snapshot must not regress builds."
    )


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@pytest.mark.parametrize("scenario", sorted(EXPECTED_SCENARIOS))
@pytest.mark.parametrize("artifact_kind", ["docx", "xlsx"])
def test_round56_each_scenario_artifact_is_real(
    manifest: dict, scenario: str, artifact_kind: str
) -> None:
    """Both DOCX and XLSX per scenario have real content + valid sha256 + on-disk file."""

    block = manifest["scenarios"][scenario][artifact_kind]
    sha = str(block.get("sha256", "")).lower()
    assert _SHA256_RE.match(sha), (
        f"{scenario}.{artifact_kind}.sha256 = {sha!r} does not look like a hex sha256"
    )
    size = int(block.get("size_bytes", 0))
    assert size > 0, f"{scenario}.{artifact_kind}.size_bytes = {size} (must be positive)"
    rel_path = block.get("path", "")
    assert rel_path, f"{scenario}.{artifact_kind}.path is empty"
    abs_path = MANIFEST_PATH.parent / rel_path
    assert abs_path.exists(), (
        f"{scenario}.{artifact_kind} manifest references {abs_path} which is missing on disk; "
        f"baseline drift detection would silently pass against a phantom file."
    )
    assert abs_path.stat().st_size == size, (
        f"{scenario}.{artifact_kind} on-disk size {abs_path.stat().st_size} "
        f"does not match manifest size {size}; capture was likely interrupted."
    )


def test_round56_supervisor_default_points_at_round56_manifest() -> None:
    """The supervisor's default --baseline-manifest must be the Round 56 manifest.

    Future rounds rebaselining MUST also repoint this default; otherwise the
    autofix loop silently keeps comparing against a stale baseline. We check
    by source-text inspection (cheaper + more robust than instantiating the
    argparse and resolving the path).
    """

    text = SUPERVISOR_PATH.read_text(encoding="utf-8")
    needle = 'REPO_ROOT / "baselines/round56/baseline_manifest.json"'
    assert needle in text, (
        f"scripts/run_report_accuracy_autofix_loop.py default --baseline-manifest "
        f"must point at the Round 56 manifest. Expected literal:\n  {needle}\n"
        f"Found instead: see argparse setup. Update the default and bump this "
        f"test to the new path when rebaselining in a future round."
    )
