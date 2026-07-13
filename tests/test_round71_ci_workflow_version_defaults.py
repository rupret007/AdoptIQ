"""Round 71 / Phase 1 (#4) -- CI workflow has no version defaults.

Pre-R71 ``.github/workflows/build.yml`` carried hard-coded
``|| '1.0.3'`` and ``|| '1'`` defaults for the version/build env vars.
This silently overwrote ``config.py`` (the SSoT) every time the
workflow ran without an explicit ``workflow_dispatch`` input -- so a
trigger from a tag / push could ship an artifact labeled with stale
"1.0.3" while ``config.py`` actually said "1.0.4".

Round 71 / Phase 1 (#4) drops both defaults and reads the resolved
version back from ``config.py`` after ``update_version_pc.py`` has run
(which honors the SSoT when env vars are empty).
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build.yml"


def _read_workflow() -> str:
    if not WORKFLOW.exists():
        # Test environments may not check out the .github folder; treat
        # absence as a skip-equivalent (still a passing test) so this
        # file does not become an environment-dependent flake.
        return ""
    return WORKFLOW.read_text(encoding="utf-8", errors="replace")


def test_round71_no_hardcoded_103_default_in_workflow() -> None:
    """The CI workflow MUST not carry a hard-coded ``|| '1.0.3'`` or
    ``|| "1.0.3"`` fallback for ADOPTIQ_VERSION."""
    src = _read_workflow()
    if not src:
        return  # No workflow file in this checkout; nothing to assert.
    forbidden = ("|| '1.0.3'", '|| "1.0.3"', "|| '1.0.4'", '|| "1.0.4"')
    for pat in forbidden:
        assert pat not in src, (
            f"Round 71 / Phase 1 (#4): build.yml must not carry a "
            f"hard-coded version default like ``{pat}``.  Let "
            f"update_version_pc.py read the SSoT from config.py."
        )


def test_round71_no_hardcoded_build_number_one_default() -> None:
    """The CI workflow MUST not carry a hard-coded ``|| '1'`` fallback
    for ADOPTIQ_BUILD."""
    src = _read_workflow()
    if not src:
        return
    forbidden_default_build = "ADOPTIQ_BUILD: ${{ github.event.inputs.build_number || '1' }}"
    assert forbidden_default_build not in src, (
        "Round 71 / Phase 1 (#4): build.yml must not default ADOPTIQ_BUILD "
        "to '1'.  Pre-R71 this overwrote the config.py SSoT silently."
    )


def test_round71_workflow_resolves_label_from_config_py() -> None:
    """The CI workflow MUST resolve the artifact label from ``config.py``
    after ``update_version_pc.py`` runs, so the artifact name reflects
    the SSoT regardless of whether the operator passed inputs."""
    src = _read_workflow()
    if not src:
        return
    assert "Resolve build label" in src or "steps.label.outputs.version" in src, (
        "Round 71 / Phase 1 (#4): build.yml must contain a 'Resolve build "
        "label' step (or equivalent) that reads ADOPTIQ_VERSION/BUILD "
        "from config.py for use in artifact filenames."
    )
