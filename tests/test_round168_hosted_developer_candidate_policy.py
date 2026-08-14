"""Hosted builds cannot cross the local production-release boundary."""

from __future__ import annotations

from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build.yml"
MAKEFILE_PATH = ROOT / "Makefile"


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject YAML mappings whose duplicate keys PyYAML normally overwrites."""


def _construct_unique_mapping(loader, node, deep: bool = False) -> dict:
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _workflow_source() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_workflow_has_unique_yaml_keys_and_one_root_environment() -> None:
    source = _workflow_source()

    # This loader subclasses SafeLoader and changes only duplicate-key handling.
    parsed = yaml.load(source, Loader=_UniqueKeyLoader)  # noqa: S506

    assert isinstance(parsed, dict)
    assert source.count("\nenv:\n") == 1
    assert parsed["env"]["ADOPTIQ_DEVELOPER_ONLY"]


def test_hosted_policy_fails_before_checkout_for_tags_or_non_developer_mode() -> None:
    source = _workflow_source()
    policy = source.index("developer-candidate-policy:")
    checkout = source.index("uses: actions/checkout@v4")
    policy_block = source[policy:source.index("quality-checks:", policy)]

    assert policy < checkout
    assert '"$GITHUB_EVENT_NAME" != "workflow_dispatch"' in policy_block
    assert '"$GITHUB_REF_TYPE" == "tag"' in policy_block
    assert '"$ADOPTIQ_DEVELOPER_ONLY" != "1"' in policy_block
    assert "developer_only=false are prohibited" in policy_block
    assert "exit 1" in policy_block
    assert "needs: developer-candidate-policy" in source


def test_hosted_jobs_are_explicitly_non_release_and_never_handle_credentials() -> None:
    source = _workflow_source()
    mac_job = source[source.index("build-mac:"):source.index("build-windows:")]
    windows_job = source[source.index("build-windows:"):]

    for job in (mac_job, windows_job):
        assert 'ADOPTIQ_DEVELOPER_ONLY: "1"' in job
        assert 'ADOPTIQ_RELEASE_GATE: "0"' in job
    assert 'ADOPTIQ_BAKE_CORPUS: "0"' in mac_job
    assert mac_job.index("Assert non-release macOS build invariant") < mac_job.index(
        "Build developer-only app bundle and DMG"
    )
    assert windows_job.index("Assert developer-only Windows build invariant") < (
        windows_job.index("Build developer-only EXE with PyInstaller")
    )
    for forbidden in (
        "secrets.",
        "SECRETS_ENV_FILE",
        "embed_credentials.py",
        "Upload macOS release",
        "Upload Windows release",
        "build${{ steps.label.outputs.build }}-release",
    ):
        assert forbidden not in source
    assert source.count("uses: actions/upload-artifact@v4") == 2
    assert source.count("Upload macOS developer candidate") == 1
    assert source.count("Upload Windows developer candidate") == 1


def test_hosted_simulation_is_truthfully_labeled_fixture_only() -> None:
    source = _workflow_source()
    step = source[source.index("Run developer-candidate fixture simulation"):]
    step = step[:step.index("build-mac:")]

    assert "developer-candidate-fixture-simulation" in step
    assert "run_round146_acceptance.py" in step
    assert "local" in step
    assert "production-simulation" not in step
    assert "release" not in step.casefold()


def test_make_production_simulation_fails_closed_without_csone_corpus() -> None:
    result = subprocess.run(
        [
            "make",
            "production-simulation",
            "PY=true",
            "CSONE_CORPUS_DIR=",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "CSONE_CORPUS_DIR is required for production-simulation" in (
        result.stdout + result.stderr
    )


def test_make_production_simulation_always_passes_explicit_corpus() -> None:
    source = MAKEFILE_PATH.read_text(encoding="utf-8")
    target = source.split("production-simulation:\n", 1)[1].split("\ntest:\n", 1)[0]
    dry_run = subprocess.run(
        [
            "make",
            "-n",
            "production-simulation",
            "PY=python3",
            "CSONE_CORPUS_DIR=/tmp/adoptiq-real-shape-corpus",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert 'test -n "/tmp/adoptiq-real-shape-corpus"' in dry_run.stdout
    assert '--csone-corpus-dir "/tmp/adoptiq-real-shape-corpus"' in dry_run.stdout
    assert "$(if $(CSONE_CORPUS_DIR)" not in target
    assert target.count("--csone-corpus-dir") == 1
