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

import release_candidate_contract as contract  # noqa: E402


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _payload(*, artifact_body: bytes = b"candidate", sidecar_body: bytes = b"evidence\n") -> dict[str, object]:
    return {
        "schema_version": contract.SCHEMA_VERSION,
        "release_status": "eligible",
        "platform": "macos",
        "version": "1.0.4",
        "build": 114,
        "source_commit_sha": "6a956e203b88a3586251d7f63eae49b319a653c0",
        "artifact": {
            "name": "AdoptIQ-v1.0.4-build114.dmg",
            "sha256": _sha(artifact_body),
            "size_bytes": len(artifact_body),
        },
        "built_at_utc": "2026-08-13T17:35:19Z",
        "sidecars": [{"path": "build_info.txt", "sha256": _sha(sidecar_body)}],
    }


def _write_contract(tmp_path: Path) -> tuple[Path, Path]:
    artifact_body = b"candidate"
    sidecar_body = b"evidence\n"
    artifact = tmp_path / "AdoptIQ-v1.0.4-build114.dmg"
    artifact.write_bytes(artifact_body)
    (tmp_path / "build_info.txt").write_bytes(sidecar_body)
    manifest = tmp_path / "candidate.json"
    manifest.write_text(json.dumps(_payload(artifact_body=artifact_body, sidecar_body=sidecar_body)), encoding="utf-8")
    return manifest, artifact


def test_valid_manifest_has_canonical_public_identity() -> None:
    manifest = contract.validate_release_candidate_manifest(_payload())

    assert manifest.to_public_dict() == _payload()
    assert manifest.identity == {
        "release_status": "eligible",
        "platform": "macos",
        "version": "1.0.4",
        "build": 114,
        "source_commit_sha": "6a956e203b88a3586251d7f63eae49b319a653c0",
        "artifact_name": "AdoptIQ-v1.0.4-build114.dmg",
        "artifact_sha256": _sha(b"candidate"),
        "artifact_size_bytes": len(b"candidate"),
        "built_at_utc": "2026-08-13T17:35:19Z",
    }


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda value: value.update(extra=True), "fields"),
        (lambda value: value.pop("platform"), "fields"),
        (lambda value: value.update(schema_version="future/v9"), "unsupported"),
        (lambda value: value.update(release_status="unknown"), "unsupported"),
        (lambda value: value.update(platform="linux"), "unsupported"),
        (lambda value: value.update(version="1.0"), "version"),
        (lambda value: value.update(build=True), "positive integer"),
        (lambda value: value.update(build=0), "positive integer"),
        (lambda value: value.update(source_commit_sha="A" * 40), "lowercase 40-hex"),
        (lambda value: value.update(source_commit_sha="a" * 39), "lowercase 40-hex"),
        (lambda value: value.update(built_at_utc="2026-08-13T17:35:19+00:00"), "canonical UTC"),
        (lambda value: value.update(built_at_utc="2026-02-30T17:35:19Z"), "valid UTC"),
        (lambda value: value.update(sidecars=[]), "non-empty JSON array"),
    ],
)
def test_manifest_rejects_malformed_top_level_fields(mutator, match: str) -> None:
    payload = _payload()
    mutator(payload)

    with pytest.raises(contract.ReleaseCandidateContractError, match=match):
        contract.validate_release_candidate_manifest(payload)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("name", "different.dmg", "platform/version/build"),
        ("name", "../AdoptIQ-v1.0.4-build114.dmg", "safe flat"),
        ("sha256", "A" * 64, "lowercase SHA-256"),
        ("sha256", "0" * 63, "lowercase SHA-256"),
        ("size_bytes", False, "positive integer"),
        ("size_bytes", -1, "positive integer"),
    ],
)
def test_manifest_rejects_invalid_artifact_identity(field: str, value: object, match: str) -> None:
    payload = _payload()
    payload["artifact"][field] = value

    with pytest.raises(contract.ReleaseCandidateContractError, match=match):
        contract.validate_release_candidate_manifest(payload)


@pytest.mark.parametrize("path", ["../build_info.txt", "nested/build_info.txt", r"nested\build_info.txt", ".hidden"])
def test_manifest_rejects_unsafe_sidecar_paths(path: str) -> None:
    payload = _payload()
    payload["sidecars"][0]["path"] = path

    with pytest.raises(contract.ReleaseCandidateContractError, match="safe flat"):
        contract.validate_release_candidate_manifest(payload)


def test_manifest_rejects_duplicate_casefolded_sidecars_and_artifact_overlap() -> None:
    payload = _payload()
    digest = _sha(b"evidence")
    payload["sidecars"] = [
        {"path": "README.md", "sha256": digest},
        {"path": "readme.MD", "sha256": digest},
    ]
    with pytest.raises(contract.ReleaseCandidateContractError, match="unique"):
        contract.validate_release_candidate_manifest(payload)

    payload = _payload()
    payload["sidecars"][0]["path"] = "AdoptIQ-v1.0.4-build114.dmg"
    with pytest.raises(contract.ReleaseCandidateContractError, match="distinct"):
        contract.validate_release_candidate_manifest(payload)


def test_load_rejects_invalid_duplicate_oversized_and_symlink_manifests(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    with pytest.raises(contract.ReleaseCandidateContractError, match="UTF-8 JSON"):
        contract.load_release_candidate_manifest(invalid)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"one","schema_version":"two"}', encoding="utf-8")
    with pytest.raises(contract.ReleaseCandidateContractError, match="duplicate JSON"):
        contract.load_release_candidate_manifest(duplicate)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(contract.ReleaseCandidateContractError, match="size limit"):
        contract.load_release_candidate_manifest(oversized)

    link = tmp_path / "link.json"
    link.symlink_to(invalid)
    with pytest.raises(contract.ReleaseCandidateContractError, match="regular non-symlink"):
        contract.load_release_candidate_manifest(link)


def test_verify_candidate_and_sidecars_accept_exact_regular_files(tmp_path: Path) -> None:
    manifest_path, artifact = _write_contract(tmp_path)

    verified = contract.verify_release_candidate(manifest_path, artifact)

    assert verified.artifact.name == artifact.name
    assert contract.verify_release_candidate_sidecars(manifest_path) == verified


@pytest.mark.parametrize("failure", ["name", "size", "hash", "symlink"])
def test_verify_candidate_fails_closed_on_artifact_mismatch(tmp_path: Path, failure: str) -> None:
    manifest_path, artifact = _write_contract(tmp_path)
    candidate = artifact
    if failure == "name":
        candidate = tmp_path / "renamed.dmg"
        candidate.write_bytes(artifact.read_bytes())
    elif failure == "size":
        artifact.write_bytes(b"different size")
    elif failure == "hash":
        artifact.write_bytes(b"Candidate")
    else:
        target = tmp_path / "real.dmg"
        artifact.rename(target)
        artifact.symlink_to(target)

    with pytest.raises(contract.ReleaseCandidateContractError):
        contract.verify_release_candidate(manifest_path, candidate)


@pytest.mark.parametrize("failure", ["missing", "hash", "symlink", "root_symlink"])
def test_verify_candidate_fails_closed_on_sidecar_mismatch(tmp_path: Path, failure: str) -> None:
    manifest_path, artifact = _write_contract(tmp_path)
    sidecar = tmp_path / "build_info.txt"
    sidecar_root: Path | None = None
    if failure == "missing":
        sidecar.unlink()
    elif failure == "hash":
        sidecar.write_bytes(b"changed\n")
    elif failure == "symlink":
        target = tmp_path / "real.txt"
        sidecar.rename(target)
        sidecar.symlink_to(target)
    else:
        linked_root = tmp_path.parent / f"{tmp_path.name}-link"
        linked_root.symlink_to(tmp_path, target_is_directory=True)
        sidecar_root = linked_root

    with pytest.raises(contract.ReleaseCandidateContractError):
        contract.verify_release_candidate(manifest_path, artifact, sidecar_root=sidecar_root)


def test_loaded_manifest_requires_explicit_sidecar_root(tmp_path: Path) -> None:
    manifest_path, artifact = _write_contract(tmp_path)
    loaded = contract.load_release_candidate_manifest(manifest_path)

    with pytest.raises(contract.ReleaseCandidateContractError, match="sidecar_root is required"):
        contract.verify_release_candidate(loaded, artifact)


def test_tracked_build114_manifest_and_sidecars_are_exact() -> None:
    manifest_path = ROOT / "release_candidates" / "macos-build114" / "candidate.json"
    loaded = contract.load_release_candidate_manifest(manifest_path)

    assert loaded.identity == {
        "release_status": "invalidated",
        "platform": "macos",
        "version": "1.0.4",
        "build": 114,
        "source_commit_sha": "6a956e203b88a3586251d7f63eae49b319a653c0",
        "artifact_name": "AdoptIQ-v1.0.4-build114.dmg",
        "artifact_sha256": "4cc485daf1bc695ec1376dd6161ce47eb10ca3dae0c67be11c8f91a4a17d6235",
        "artifact_size_bytes": 1708039038,
        "built_at_utc": "2026-08-13T17:35:19Z",
    }
    assert contract.verify_release_candidate_sidecars(manifest_path) == loaded

    artifact = ROOT / "OUTBOX" / loaded.artifact.name
    if artifact.is_file():
        with pytest.raises(contract.ReleaseCandidateContractError, match="invalidated"):
            contract.verify_release_candidate(manifest_path, artifact)


def test_tracked_build115_manifest_sidecars_and_review_template_are_exact() -> None:
    candidate_dir = ROOT / "release_candidates" / "macos-build115"
    manifest_path = candidate_dir / "candidate.json"
    loaded = contract.load_release_candidate_manifest(manifest_path)

    assert loaded.identity == {
        "release_status": "invalidated",
        "platform": "macos",
        "version": "1.0.4",
        "build": 115,
        "source_commit_sha": "d972c367b6eff07ce3095c39c3136af437517b6f",
        "artifact_name": "AdoptIQ-v1.0.4-build115.dmg",
        "artifact_sha256": "6e828e896510d75647c9db126d99a48be57384f31305f7c32b3da317739096d0",
        "artifact_size_bytes": 1711563863,
        "built_at_utc": "2026-08-13T23:51:26Z",
    }
    assert contract.verify_release_candidate_sidecars(manifest_path) == loaded

    artifact = ROOT / "OUTBOX" / loaded.artifact.name
    if artifact.is_file():
        with pytest.raises(contract.ReleaseCandidateContractError, match="invalidated"):
            contract.verify_release_candidate(manifest_path, artifact)

    template = json.loads(
        (candidate_dir / "manual-review.template.json").read_text(encoding="utf-8")
    )
    assert template["candidate"] == loaded.identity
    assert template["do_not_commit"] is True
    assert template["manual_source_reconciliation_complete"] is False
    assert template["visual_review_complete"] is False
    assert template["report_scopes_reviewed"] == []
    assert template["link_types_opened"] == []
    assert template["claim_count"] == 0
    assert template["release_recommendation"] == "no-go"
