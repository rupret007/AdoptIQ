#!/usr/bin/env python3
"""Create one fail-closed manual-review template for an eligible candidate.

The template is an empty, explicit NO-GO worksheet.  It never records an
approval and cannot overwrite operator-owned evidence.  The destination inode
is created atomically with exclusive semantics, written through that bound file
descriptor, and removed only when its exact device/inode identity still matches.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from release_candidate_contract import (  # noqa: E402
    ReleaseCandidateContractError,
    ReleaseCandidateManifest,
    _read_regular_bytes,
    load_release_candidate_manifest,
    verify_release_candidate_sidecars,
)


SCHEMA_VERSION = "adoptiq-live-manual-review/v1"
_OUTPUT_NAME = "manual-review.template.json"
_TEMPLATE_MAX_BYTES = 64 * 1024


def _canonical_json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def _template_payload(candidate: ReleaseCandidateManifest) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
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


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseCandidateContractError(
                "manual-review template contains duplicate JSON fields"
            )
        result[key] = value
    return result


def _validate_template_payload(
    payload: Any,
    *,
    candidate: ReleaseCandidateManifest,
) -> dict[str, Any]:
    """Require the exact empty NO-GO schema bound to ``candidate.identity``."""

    if not isinstance(payload, Mapping):
        raise ReleaseCandidateContractError("manual-review template must be a JSON object")
    expected = _template_payload(candidate)
    if _canonical_json_bytes(dict(payload)) != _canonical_json_bytes(expected):
        raise ReleaseCandidateContractError(
            "manual-review template is not the exact candidate-bound NO-GO schema"
        )
    return expected


def _read_and_validate(
    path: Path,
    *,
    candidate: ReleaseCandidateManifest,
    expected_bytes: bytes,
) -> dict[str, Any]:
    raw = _read_regular_bytes(
        path,
        label="manual-review template",
        maximum_bytes=_TEMPLATE_MAX_BYTES,
    )
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except ReleaseCandidateContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateContractError(
            "manual-review template is not valid UTF-8 JSON"
        ) from exc
    validated = _validate_template_payload(payload, candidate=candidate)
    if raw != expected_bytes or raw != _canonical_json_bytes(validated):
        raise ReleaseCandidateContractError(
            "manual-review template bytes are not canonical or deterministic"
        )
    return validated


def _require_real_directory(path: Path, *, label: str) -> tuple[Path, os.stat_result]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseCandidateContractError(f"{label} is missing or unreadable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseCandidateContractError(
            f"{label} must be a real non-symlink directory"
        )
    return path, metadata


def _remove_exact_regular(
    path: Path,
    *,
    device: int,
    inode: int,
    ctime_ns: int,
    expected_bytes: bytes | None = None,
) -> None:
    """Remove only the exact regular inode created by this process."""

    try:
        metadata = path.lstat()
    except OSError:
        return
    # Round 184: inode reuse can occur after unlink/recreate races on some
    # filesystems. Include ctime in the identity guard so we never delete a
    # competing operator file that happens to reuse the same inode number.
    if stat.S_ISREG(metadata.st_mode) and (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_ctime_ns,
    ) == (
        device,
        inode,
        ctime_ns,
    ):
        # Round 184: on filesystems that can recycle inode numbers quickly,
        # require the final bytes to still match the template we published
        # before unlinking. This preserves a competing operator file that
        # replaced the path after publication.
        if expected_bytes is not None:
            try:
                current_bytes = path.read_bytes()
            except OSError:
                return
            if current_bytes != expected_bytes:
                return
        try:
            path.unlink()
        except OSError:
            pass


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseCandidateContractError(
            "manual-review output directory could not be opened safely"
        ) from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ReleaseCandidateContractError(
            "manual-review output directory could not be synchronized"
        ) from exc
    finally:
        os.close(descriptor)


def _load_eligible_candidate(path: Path) -> ReleaseCandidateManifest:
    candidate = load_release_candidate_manifest(path)
    if candidate.release_status != "eligible":
        raise ReleaseCandidateContractError(
            "manual-review templates require an eligible release candidate"
        )
    verified = verify_release_candidate_sidecars(path)
    if verified.to_public_dict() != candidate.to_public_dict():
        raise ReleaseCandidateContractError(
            "release candidate manifest changed during template preparation"
        )
    return candidate


def create_manual_review_template(
    *,
    candidate_manifest_path: Path,
    output_path: Path,
) -> Path:
    """Create and immediately re-validate one candidate-bound NO-GO template."""

    manifest_path = candidate_manifest_path.expanduser()
    if not manifest_path.is_absolute():
        manifest_path = Path.cwd() / manifest_path
    manifest_path = manifest_path.absolute()
    candidate = _load_eligible_candidate(manifest_path)

    output = output_path.expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    output = output.absolute()
    if output.name != _OUTPUT_NAME:
        raise ReleaseCandidateContractError(
            f"manual-review template output name must be {_OUTPUT_NAME}"
        )
    output_root, root_before = _require_real_directory(
        output.parent,
        label="manual-review output directory",
    )
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ReleaseCandidateContractError(
            "manual-review template output could not be checked safely"
        ) from exc
    else:
        raise ReleaseCandidateContractError(
            "manual-review template already exists; refusing to overwrite it"
        )

    body = _canonical_json_bytes(_template_payload(candidate))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags, 0o600)
    except OSError as exc:
        raise ReleaseCandidateContractError(
            "manual-review template could not be created exclusively"
        ) from exc
    created = os.fstat(descriptor)
    cleanup_ctime_ns = created.st_ctime_ns
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())

        published_after = output.lstat()
        if (
            not stat.S_ISREG(published_after.st_mode)
            or (published_after.st_dev, published_after.st_ino)
            != (created.st_dev, created.st_ino)
        ):
            raise ReleaseCandidateContractError(
                "manual-review template identity changed during publication"
            )
        # Round 184: publication writes can legitimately advance ctime on some
        # filesystems. Use the post-publication value as the cleanup identity.
        cleanup_ctime_ns = published_after.st_ctime_ns
        root_after = output_root.lstat()
        if (
            root_after.st_dev,
            root_after.st_ino,
            root_after.st_mode,
        ) != (
            root_before.st_dev,
            root_before.st_ino,
            root_before.st_mode,
        ):
            raise ReleaseCandidateContractError(
                "manual-review output directory changed during publication"
            )
        _fsync_directory(output_root)
        current_candidate = _load_eligible_candidate(manifest_path)
        if current_candidate.to_public_dict() != candidate.to_public_dict():
            raise ReleaseCandidateContractError(
                "release candidate manifest changed during template publication"
            )
        _read_and_validate(
            output,
            candidate=candidate,
            expected_bytes=body,
        )
    except BaseException:
        _remove_exact_regular(
            output,
            device=created.st_dev,
            inode=created.st_ino,
            ctime_ns=cleanup_ctime_ns,
            expected_bytes=body,
        )
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-manifest",
        required=True,
        type=Path,
        help="Exact eligible candidate.json manifest",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help=f"New {_OUTPUT_NAME} path",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = create_manual_review_template(
            candidate_manifest_path=args.candidate_manifest,
            output_path=args.output,
        )
    except (OSError, ReleaseCandidateContractError, ValueError) as exc:
        print(f"Manual-review template was not created: {exc}", file=sys.stderr)
        return 2
    print(f"Created and verified candidate-bound NO-GO manual-review template: {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
