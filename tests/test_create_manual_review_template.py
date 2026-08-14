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

import create_manual_review_template as creator  # noqa: E402
import release_candidate_contract as contract  # noqa: E402


SOURCE_SHA = "b" * 40
BUILT_AT = "2026-08-14T02:30:00Z"


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _candidate_payload(
    *,
    release_status: str = "eligible",
    sidecar_body: bytes = b"immutable build evidence\n",
) -> dict[str, object]:
    return {
        "schema_version": contract.SCHEMA_VERSION,
        "release_status": release_status,
        "platform": "macos",
        "version": "1.0.4",
        "build": 116,
        "source_commit_sha": SOURCE_SHA,
        "artifact": {
            "name": "AdoptIQ-v1.0.4-build116.dmg",
            "sha256": "c" * 64,
            "size_bytes": 123456,
        },
        "built_at_utc": BUILT_AT,
        "sidecars": [{"path": "build_info.txt", "sha256": _sha(sidecar_body)}],
    }


def _write_candidate(
    root: Path,
    *,
    release_status: str = "eligible",
) -> Path:
    root.mkdir()
    sidecar_body = b"immutable build evidence\n"
    (root / "build_info.txt").write_bytes(sidecar_body)
    payload = _candidate_payload(
        release_status=release_status,
        sidecar_body=sidecar_body,
    )
    manifest = contract.validate_release_candidate_manifest(payload)
    path = root / "candidate.json"
    path.write_bytes(
        (json.dumps(manifest.to_public_dict(), indent=2, ensure_ascii=True) + "\n").encode(
            "utf-8"
        )
    )
    return path


def _expected_template(candidate: contract.ReleaseCandidateManifest) -> dict[str, object]:
    return {
        "schema_version": "adoptiq-live-manual-review/v1",
        "sanitized": True,
        "do_not_commit": True,
        "candidate": candidate.identity,
        "acceptance_summary_sha256": "",
        "reviewer": "",
        "reviewed_at_utc": "",
        "manual_source_reconciliation_complete": False,
        "visual_review_complete": False,
        "second_manager_validated": False,
        "all_managers_validated": False,
        "report_scopes_reviewed": [],
        "link_types_opened": [],
        "claim_count": 0,
        "mismatch_count": None,
        "unexplained_unknown_count": None,
        "missing_expected_link_count": None,
        "release_recommendation": "no-go",
    }


def test_create_exact_candidate_bound_no_go_template(tmp_path: Path) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    output_root = tmp_path / "review"
    output_root.mkdir()
    output = output_root / "manual-review.template.json"

    created = creator.create_manual_review_template(
        candidate_manifest_path=candidate_path,
        output_path=output,
    )

    candidate = contract.load_release_candidate_manifest(candidate_path)
    expected = _expected_template(candidate)
    expected_bytes = (json.dumps(expected, indent=2, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    assert created == output
    assert output.read_bytes() == expected_bytes
    assert json.loads(output.read_text(encoding="utf-8")) == expected
    assert not list(output_root.glob(".manual-review.template.json.*.tmp"))


def test_cli_creates_only_a_no_go_template(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    output = candidate_path.parent / "manual-review.template.json"

    exit_code = creator.main(
        [
            "--candidate-manifest",
            str(candidate_path),
            "--output",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert str(output) in captured.out
    assert captured.err == ""
    assert json.loads(output.read_text(encoding="utf-8"))["release_recommendation"] == (
        "no-go"
    )


def test_invalidated_candidate_is_rejected_before_template_creation(tmp_path: Path) -> None:
    candidate_path = _write_candidate(
        tmp_path / "candidate",
        release_status="invalidated",
    )
    output = candidate_path.parent / "manual-review.template.json"

    with pytest.raises(contract.ReleaseCandidateContractError, match="eligible"):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )
    assert not output.exists()


def test_candidate_sidecars_must_still_match_the_manifest(tmp_path: Path) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    (candidate_path.parent / "build_info.txt").write_bytes(b"changed evidence\n")
    output = candidate_path.parent / "manual-review.template.json"

    with pytest.raises(contract.ReleaseCandidateContractError, match="hash"):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )
    assert not output.exists()


@pytest.mark.parametrize("malformed", [b"not json", b'{"schema_version":"future/v9"}\n'])
def test_malformed_candidate_is_rejected(tmp_path: Path, malformed: bytes) -> None:
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    candidate_path = candidate_root / "candidate.json"
    candidate_path.write_bytes(malformed)
    output = candidate_root / "manual-review.template.json"

    with pytest.raises(contract.ReleaseCandidateContractError):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )
    assert not output.exists()


@pytest.mark.parametrize("existing_kind", ["regular", "symlink", "directory"])
def test_existing_output_is_never_overwritten(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    output = candidate_path.parent / "manual-review.template.json"
    target = candidate_path.parent / "operator-owned.json"
    original = b"operator-owned evidence\n"
    if existing_kind == "regular":
        output.write_bytes(original)
    elif existing_kind == "symlink":
        target.write_bytes(original)
        output.symlink_to(target)
    else:
        output.mkdir()

    with pytest.raises(contract.ReleaseCandidateContractError, match="refusing to overwrite"):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )

    if existing_kind == "regular":
        assert output.read_bytes() == original
    elif existing_kind == "symlink":
        assert output.is_symlink()
        assert target.read_bytes() == original
    else:
        assert output.is_dir()


@pytest.mark.parametrize("parent_kind", ["missing", "file", "symlink"])
def test_output_parent_must_be_a_real_existing_directory(
    tmp_path: Path,
    parent_kind: str,
) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    real_root = tmp_path / "real-review"
    real_root.mkdir()
    if parent_kind == "missing":
        output_root = tmp_path / "missing-review"
    elif parent_kind == "file":
        output_root = tmp_path / "not-a-directory"
        output_root.write_bytes(b"operator-owned\n")
    else:
        output_root = tmp_path / "linked-review"
        output_root.symlink_to(real_root, target_is_directory=True)
    output = output_root / "manual-review.template.json"

    with pytest.raises(contract.ReleaseCandidateContractError, match="output directory"):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )
    assert not (real_root / "manual-review.template.json").exists()


def test_output_name_is_fixed_to_the_canonical_template_name(tmp_path: Path) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    output = candidate_path.parent / "review.json"

    with pytest.raises(contract.ReleaseCandidateContractError, match="output name"):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )
    assert not output.exists()


def test_identical_candidate_identity_produces_deterministic_bytes(tmp_path: Path) -> None:
    outputs: list[bytes] = []
    for suffix in ("one", "two"):
        candidate_path = _write_candidate(tmp_path / f"candidate-{suffix}")
        output = candidate_path.parent / "manual-review.template.json"
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )
        outputs.append(output.read_bytes())

    assert outputs[0] == outputs[1]
    assert outputs[0].endswith(b"\n")
    assert not outputs[0].endswith(b"\n\n")


def test_post_publish_validation_failure_removes_only_created_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    output = candidate_path.parent / "manual-review.template.json"
    original_validator = creator._read_and_validate
    calls = 0

    def fail_final_validation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise contract.ReleaseCandidateContractError("simulated final re-read failure")
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(creator, "_read_and_validate", fail_final_validation)
    with pytest.raises(contract.ReleaseCandidateContractError, match="final re-read"):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )

    assert calls == 1
    assert not output.exists()
    assert not list(candidate_path.parent.glob(".manual-review.template.json.*.tmp"))


def test_competitor_at_exclusive_open_is_preserved_without_temp_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    output = candidate_path.parent / "manual-review.template.json"
    competitor_body = b"operator-created concurrent evidence\n"
    real_open = creator.os.open
    injected = False

    def compete_before_open(path, flags, mode=0o777):
        nonlocal injected
        if Path(path) == output and flags & creator.os.O_EXCL and not injected:
            injected = True
            output.write_bytes(competitor_body)
        return real_open(path, flags, mode)

    monkeypatch.setattr(creator.os, "open", compete_before_open)
    with pytest.raises(contract.ReleaseCandidateContractError, match="exclusively"):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )

    assert injected is True
    assert output.read_bytes() == competitor_body
    assert not list(candidate_path.parent.glob(".manual-review.template.json.*.tmp"))


def test_post_write_path_replacement_is_never_deleted_as_created_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_path = _write_candidate(tmp_path / "candidate")
    output = candidate_path.parent / "manual-review.template.json"
    operator_body = b'{"release_recommendation":"go"}\n'
    real_fsync_directory = creator._fsync_directory

    def replace_after_bound_write(path: Path) -> None:
        output.unlink()
        output.write_bytes(operator_body)
        real_fsync_directory(path)

    monkeypatch.setattr(creator, "_fsync_directory", replace_after_bound_write)
    with pytest.raises(contract.ReleaseCandidateContractError, match="NO-GO schema"):
        creator.create_manual_review_template(
            candidate_manifest_path=candidate_path,
            output_path=output,
        )

    assert output.read_bytes() == operator_body
    assert not list(candidate_path.parent.glob(".manual-review.template.json.*.tmp"))
