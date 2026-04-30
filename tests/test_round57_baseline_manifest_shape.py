"""Round 57 -- pin the shape of the Build31 + R57-citations baseline.

The Round 57 plan added a post-render Word source-citation injector
(``report_source_injector.py``) that rewrites generated DOCX files to
add ~698 / 480 / 558 / 2207 ``[Source: AdoptIQ Report Data Sources]``
markers to the comprehensive / compact / renewal / leader scenarios
respectively. The Round 56 baseline -- captured before the injector
was wired -- now diverges from current runs on TEXT similarity (the
DOCX body is ~10% larger) even though the underlying KPI values are
unchanged.

To keep ``baseline_diff`` meaningful we captured a separate
``baselines/round57`` snapshot AFTER the injector landed. This test
pins the contract that future "rebaseline" rounds (Round N+M) MUST
satisfy when they replace ``baselines/round57`` with a newer snapshot:

1. The manifest sits at ``baselines/round57/baseline_manifest.json``.
2. It carries the four canonical scenarios (``compact``,
   ``comprehensive``, ``leader``, ``renewal``).
3. Each scenario has BOTH a ``docx`` and ``xlsx`` block with a
   non-empty 64-char ``sha256`` and a positive ``size_bytes``.
4. Each ``path`` field points to a file that EXISTS on disk under
   ``baselines/round57/``.
5. The captured ``adoptiq_build`` matches the current ``Config``
   build at the time the manifest was minted (>= 31).
6. The supervisor default points at the round57 manifest so the
   autofix loop, when invoked without ``--baseline-manifest``, diffs
   against the post-citation ground truth.

Round 57 / Phase B source-pin. Made-with: Cursor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "baselines" / "round57" / "baseline_manifest.json"
EXPECTED_SCENARIOS = {"compact", "comprehensive", "leader", "renewal"}
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "run_report_accuracy_autofix_loop.py"


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST_PATH.exists(), (
        f"baselines/round57/baseline_manifest.json missing -- the Round 57 "
        f"capture step must run before this contract can be pinned. "
        f"Re-run `python3 scripts/run_report_iteration_loop.py "
        f"--init-baseline --init-baseline-dir baselines/round57 ...` "
        f"per the Round 57 handoff."
    )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_round57_manifest_has_top_level_metadata(manifest: dict) -> None:
    """Top-level keys the harness depends on for reproducibility."""

    for key in ("environment", "generated_at_utc", "git_sha", "label", "scenarios", "version"):
        assert key in manifest, f"manifest is missing top-level key {key!r}"
    assert isinstance(manifest["scenarios"], dict)
    assert manifest["version"] == 1, (
        "Manifest schema version pinned at 1; bumping requires a coordinated "
        "harness change in report_iteration_loop.py."
    )


def test_round57_manifest_carries_canonical_four_scenarios(manifest: dict) -> None:
    """The supervisor's default scenario set must round-trip via this manifest."""

    captured = set(manifest["scenarios"].keys())
    assert captured == EXPECTED_SCENARIOS, (
        f"Round 57 baseline must contain exactly the four canonical scenarios "
        f"(compact/comprehensive/leader/renewal). Captured: {sorted(captured)}; "
        f"expected: {sorted(EXPECTED_SCENARIOS)}."
    )


def test_round57_manifest_has_build31_environment(manifest: dict) -> None:
    """The manifest must record the build it was captured against."""

    env = manifest.get("environment", {})
    build = str(env.get("adoptiq_build", "")).strip()
    assert build, "manifest.environment.adoptiq_build is missing or empty"
    try:
        captured_build = int(build)
    except ValueError:
        pytest.fail(f"adoptiq_build {build!r} is not an integer")
    assert captured_build >= 31, (
        f"Round 57 baseline was captured against build {captured_build}, "
        f"expected >= 31. A re-baselined snapshot must not regress builds."
    )


def test_round57_manifest_label_records_citation_anchor(manifest: dict) -> None:
    """The label must mention citations so reviewers know this baseline
    differs from round56 in the citation-text dimension specifically.

    Round 56 baseline lacks citations; Round 57 baseline is the canonical
    post-citation snapshot. The label is the human-readable hint future
    operators see when reading the manifest.
    """

    label = str(manifest.get("label", "")).lower()
    assert "citation" in label or "r57" in label, (
        f"Round 57 manifest label {manifest.get('label')!r} should mention "
        f"R57 / citations so future operators understand why it diverges "
        f"from baselines/round56/."
    )


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@pytest.mark.parametrize("scenario", sorted(EXPECTED_SCENARIOS))
@pytest.mark.parametrize("artifact_kind", ["docx", "xlsx"])
def test_round57_each_scenario_artifact_is_real(
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


def test_round57_supervisor_default_points_at_round57_manifest() -> None:
    """The supervisor's default --baseline-manifest must be the Round 57 manifest.

    Round 56 repointed from round52 -> round56 when the .app dropped the
    distinct-AB fix; Round 57 must repoint round56 -> round57 because the
    post-render citation injector inflates DOCX text. Future rebaseline
    rounds repeat this pattern.
    """

    text = SUPERVISOR_PATH.read_text(encoding="utf-8")
    needle = 'REPO_ROOT / "baselines/round57/baseline_manifest.json"'
    assert needle in text, (
        f"scripts/run_report_accuracy_autofix_loop.py default --baseline-manifest "
        f"must point at the Round 57 manifest. Expected literal:\n  {needle}\n"
        f"Found instead: see argparse setup. Update the default and bump this "
        f"test to the new path when rebaselining in a future round."
    )
