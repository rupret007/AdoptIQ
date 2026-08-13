from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import create_release_candidate as creator  # noqa: E402
import release_candidate_contract as contract  # noqa: E402


SOURCE_SHA = "a" * 40
BUILT_AT = "2026-08-13T19:20:21Z"


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _inputs(tmp_path: Path) -> tuple[Path, list[Path], Path]:
    artifact = tmp_path / "AdoptIQ-v1.0.4-build115.dmg"
    artifact.write_bytes(b"exact native candidate bytes")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    readme = evidence / "README.md"
    readme.write_bytes(b"candidate handoff\n")
    build_info = evidence / "build_info.txt"
    build_info.write_bytes(b"immutable build evidence\n")
    return artifact, [readme, build_info], evidence / "candidate.json"


def _create(tmp_path: Path) -> tuple[Path, Path, list[Path]]:
    artifact, sidecars, output = _inputs(tmp_path)
    created = creator.create_release_candidate_manifest(
        artifact_path=artifact,
        sidecar_paths=sidecars,
        output_path=output,
        platform_name="macos",
        version="1.0.4",
        build=115,
        source_commit_sha=SOURCE_SHA,
        built_at_utc=BUILT_AT,
    )
    return created, artifact, sidecars


def test_create_manifest_hashes_exact_bytes_and_self_verifies(tmp_path: Path) -> None:
    output, artifact, sidecars = _create(tmp_path)

    loaded = contract.verify_release_candidate(output, artifact)
    assert loaded.identity == {
        "release_status": "eligible",
        "platform": "macos",
        "version": "1.0.4",
        "build": 115,
        "source_commit_sha": SOURCE_SHA,
        "artifact_name": artifact.name,
        "artifact_sha256": _sha(artifact.read_bytes()),
        "artifact_size_bytes": artifact.stat().st_size,
        "built_at_utc": BUILT_AT,
    }
    assert [item.path for item in loaded.sidecars] == ["build_info.txt", "README.md"]
    assert [item.sha256 for item in loaded.sidecars] == [
        _sha(sidecars[1].read_bytes()),
        _sha(sidecars[0].read_bytes()),
    ]
    assert output.read_bytes() == creator._canonical_json_bytes(loaded.to_public_dict())
    assert json.loads(output.read_text(encoding="utf-8")) == loaded.to_public_dict()


def test_cli_creates_and_reports_only_manifest_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact, sidecars, output = _inputs(tmp_path)

    exit_code = creator.main(
        [
            "--artifact",
            str(artifact),
            "--sidecar",
            str(sidecars[0]),
            "--sidecar",
            str(sidecars[1]),
            "--output",
            str(output),
            "--platform",
            "macos",
            "--version",
            "1.0.4",
            "--build",
            "115",
            "--source-commit-sha",
            SOURCE_SHA,
            "--built-at-utc",
            BUILT_AT,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert str(output) in captured.out
    assert captured.err == ""
    assert contract.verify_release_candidate(output, artifact).release_status == "eligible"


def test_existing_manifest_is_never_overwritten(tmp_path: Path) -> None:
    artifact, sidecars, output = _inputs(tmp_path)
    original = b"operator-owned evidence\n"
    output.write_bytes(original)

    with pytest.raises(contract.ReleaseCandidateContractError, match="refusing to overwrite"):
        creator.create_release_candidate_manifest(
            artifact_path=artifact,
            sidecar_paths=sidecars,
            output_path=output,
            platform_name="macos",
            version="1.0.4",
            build=115,
            source_commit_sha=SOURCE_SHA,
            built_at_utc=BUILT_AT,
        )

    assert output.read_bytes() == original


@pytest.mark.parametrize("unsafe_kind", ["outside", "nested", "symlink"])
def test_sidecars_must_be_direct_regular_children(tmp_path: Path, unsafe_kind: str) -> None:
    artifact, sidecars, output = _inputs(tmp_path)
    if unsafe_kind == "outside":
        unsafe = tmp_path / "outside.txt"
        unsafe.write_bytes(b"outside\n")
    elif unsafe_kind == "nested":
        nested = output.parent / "nested"
        nested.mkdir()
        unsafe = nested / "nested.txt"
        unsafe.write_bytes(b"nested\n")
    else:
        target = output.parent / "real.txt"
        target.write_bytes(b"real\n")
        unsafe = output.parent / "linked.txt"
        unsafe.symlink_to(target)

    with pytest.raises(contract.ReleaseCandidateContractError):
        creator.create_release_candidate_manifest(
            artifact_path=artifact,
            sidecar_paths=[*sidecars, unsafe],
            output_path=output,
            platform_name="macos",
            version="1.0.4",
            build=115,
            source_commit_sha=SOURCE_SHA,
            built_at_utc=BUILT_AT,
        )
    assert not output.exists()


def test_rejects_symlink_artifact_and_manifest_directory(tmp_path: Path) -> None:
    artifact, sidecars, output = _inputs(tmp_path)
    real_artifact = artifact.with_name("real.dmg")
    artifact.rename(real_artifact)
    artifact.symlink_to(real_artifact)

    with pytest.raises(contract.ReleaseCandidateContractError, match="regular non-symlink"):
        creator.create_release_candidate_manifest(
            artifact_path=artifact,
            sidecar_paths=sidecars,
            output_path=output,
            platform_name="macos",
            version="1.0.4",
            build=115,
            source_commit_sha=SOURCE_SHA,
            built_at_utc=BUILT_AT,
        )

    linked_root = tmp_path / "linked-evidence"
    linked_root.symlink_to(output.parent, target_is_directory=True)
    with pytest.raises(contract.ReleaseCandidateContractError, match="real non-symlink directory"):
        creator.create_release_candidate_manifest(
            artifact_path=real_artifact,
            sidecar_paths=[linked_root / path.name for path in sidecars],
            output_path=linked_root / "candidate.json",
            platform_name="macos",
            version="1.0.4",
            build=115,
            source_commit_sha=SOURCE_SHA,
            built_at_utc=BUILT_AT,
        )


def test_contract_rejection_leaves_no_partial_manifest(tmp_path: Path) -> None:
    artifact, sidecars, output = _inputs(tmp_path)

    with pytest.raises(contract.ReleaseCandidateContractError, match="platform/version/build"):
        creator.create_release_candidate_manifest(
            artifact_path=artifact,
            sidecar_paths=sidecars,
            output_path=output,
            platform_name="macos",
            version="1.0.4",
            build=116,
            source_commit_sha=SOURCE_SHA,
            built_at_utc=BUILT_AT,
        )
    assert not output.exists()


def test_failed_post_write_self_verification_removes_only_created_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, sidecars, output = _inputs(tmp_path)

    def fail_verification(*_args, **_kwargs):
        raise contract.ReleaseCandidateContractError("simulated post-write mismatch")

    monkeypatch.setattr(creator, "verify_release_candidate", fail_verification)
    with pytest.raises(contract.ReleaseCandidateContractError, match="post-write mismatch"):
        creator.create_release_candidate_manifest(
            artifact_path=artifact,
            sidecar_paths=sidecars,
            output_path=output,
            platform_name="macos",
            version="1.0.4",
            build=115,
            source_commit_sha=SOURCE_SHA,
            built_at_utc=BUILT_AT,
        )
    assert not output.exists()


def test_cli_returns_failure_without_leaving_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact, sidecars, output = _inputs(tmp_path)

    exit_code = creator.main(
        [
            "--artifact",
            str(artifact),
            "--sidecar",
            str(sidecars[0]),
            "--output",
            str(output),
            "--platform",
            "macos",
            "--version",
            "1.0.4",
            "--build",
            "115",
            "--source-commit-sha",
            "not-a-sha",
            "--built-at-utc",
            BUILT_AT,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "was not created" in captured.err
    assert not output.exists()
