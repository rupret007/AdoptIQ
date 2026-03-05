from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_build_workflow_runs_pytest_gate():
    workflow = PROJECT_ROOT.joinpath(".github", "workflows", "build.yml").read_text(encoding="utf-8")
    assert "quality-checks:" in workflow
    assert "run: pytest -q" in workflow


def test_build_jobs_depend_on_quality_gate():
    workflow = PROJECT_ROOT.joinpath(".github", "workflows", "build.yml").read_text(encoding="utf-8")
    assert "build-mac:" in workflow and "needs: quality-checks" in workflow
    assert "build-windows:" in workflow and "needs: quality-checks" in workflow
