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


def test_pr_quality_workflow_runs_verify_without_native_builds():
    """Round 168: ordinary PRs must run ``make verify``.

    ``build.yml`` still only fires on tags / workflow_dispatch and then
    packages macOS/Windows candidates.  A separate ``quality.yml`` is the
    fail-closed PR gate so a merge cannot skip lint/security/audit/tests
    without also triggering native packaging or the production-simulation.
    """
    workflow = PROJECT_ROOT.joinpath(".github", "workflows", "quality.yml").read_text(
        encoding="utf-8"
    )
    assert "pull_request:" in workflow
    assert "run: make verify" in workflow
    assert "pip install ruff bandit pip-audit" in workflow
    assert "build-mac:" not in workflow
    assert "build-windows:" not in workflow
    assert "run_round146_acceptance.py" not in workflow


def test_requirements_pin_clean_install_runtime_contracts():
    """Round 141: clean installs retain data and chart runtime contracts."""
    requirements = PROJECT_ROOT.joinpath("requirements.txt").read_text(encoding="utf-8")
    package_lines = [
        line.replace(" ", "").lower()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "pandas>=2.0.0,<3.0.0" in package_lines
    assert any(line.startswith("matplotlib>=") for line in package_lines)


def test_build_workflow_resolves_version_from_repo_ssot():
    """Round 71 / Phase 1 (#4): the workflow no longer hardcodes a
    default version/build.  When the operator launches a manual
    workflow_dispatch run with no version/build supplied, the CI
    falls through to ``config.py`` (the SSoT) via a "Resolve build
    label" step that reads ``ADOPTIQ_VERSION`` / ``ADOPTIQ_BUILD``
    from the source so the artifact name always reflects the
    ``config.py`` value the bake step writes into the binary.

    Pre-R71 the workflow shipped ``default: "1.0.3"`` and ``|| '1'``
    fallbacks, so a forgotten input on a manual run produced
    ``AdoptIQ-macOS-v1.0.3-build1.zip`` even when ``config.py`` said
    ``1.0.4`` / ``45``.  This test pins the new contract: the inputs
    accept an empty default, and the resolver step (``id: label``)
    is always run before the artifact-naming step.
    """
    workflow = PROJECT_ROOT.joinpath(".github", "workflows", "build.yml").read_text(encoding="utf-8")
    # No hardcoded version defaults.
    assert 'default: "1.0.3"' not in workflow, (
        "Round 71 / Phase 1 (#4): build.yml must no longer carry the "
        "hardcoded ``default: \"1.0.3\"`` -- the SSoT in config.py is "
        "the single source of truth for artifact names."
    )
    assert "|| '1.0.3' }}" not in workflow, (
        "Round 71 / Phase 1 (#4): build.yml must no longer fall back "
        "to ``'1.0.3'`` on missing version input."
    )
    assert "|| '1' }}" not in workflow, (
        "Round 71 / Phase 1 (#4): build.yml must no longer fall back "
        "to ``'1'`` on missing build input."
    )
    # Resolver step present so artifact names reflect config.py.
    assert "Resolve build label" in workflow, (
        "Round 71 / Phase 1 (#4): build.yml must include a "
        "``Resolve build label`` step that reads ADOPTIQ_VERSION / "
        "ADOPTIQ_BUILD from config.py."
    )
    assert "steps.label.outputs.version" in workflow, (
        "Round 71 / Phase 1 (#4): artifact-naming step must consume "
        "the resolver's ``steps.label.outputs.version`` output."
    )
    assert "steps.label.outputs.build" in workflow, (
        "Round 71 / Phase 1 (#4): artifact-naming step must consume "
        "the resolver's ``steps.label.outputs.build`` output."
    )
