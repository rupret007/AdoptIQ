from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_build_workflow_runs_full_verify_gate():
    """Round 63 / Tier A: CI now runs the full ``make verify`` (lint +
    security + audit + tests) instead of just ``pytest -q``.  This
    closes R18-NEXT-004 / R20-NEXT-005 / R22-NEXT-CI / R62 deferral
    so a Cursor edit that's clean locally but adds a ruff or bandit
    regression cannot land via CI either.

    The dev-only quality tools (ruff / bandit / pip-audit) are
    intentionally NOT in ``requirements.txt`` per the local-only
    convention, so the workflow MUST install them before invoking
    ``make verify``.
    """
    workflow = PROJECT_ROOT.joinpath(".github", "workflows", "build.yml").read_text(encoding="utf-8")
    assert "quality-checks:" in workflow
    assert "run: make verify" in workflow
    assert "pip install ruff bandit pip-audit" in workflow


def test_build_jobs_depend_on_quality_gate():
    workflow = PROJECT_ROOT.joinpath(".github", "workflows", "build.yml").read_text(encoding="utf-8")
    assert "build-mac:" in workflow and "needs: quality-checks" in workflow
    assert "build-windows:" in workflow and "needs: quality-checks" in workflow


def test_build_workflow_default_version_matches_repo_version():
    workflow = PROJECT_ROOT.joinpath(".github", "workflows", "build.yml").read_text(encoding="utf-8")
    assert 'default: "1.0.3"' in workflow
    assert "ADOPTIQ_VERSION: ${{ github.event.inputs.version || '1.0.3' }}" in workflow
