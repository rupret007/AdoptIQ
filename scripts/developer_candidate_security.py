#!/usr/bin/env python3
"""Shared security and provenance checks for frozen developer candidates."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REQUIRED_ARCHIVE_MODULES = (
    "ask_ai_grounded",
    "canonical_report_adapter",
    "decision_report_delivery",
    "manager_decision_workspace",
)
FORBIDDEN_ARCHIVE_NAMES = (
    "_bundled_secrets",
    "corpus.db.enc",
    "corpus.db.salt",
    "sentinel.json",
    "local_acceptance_lab",
    "local_acceptance_runtime",
)
FORBIDDEN_RESOURCE_NAMES = {
    ".env",
    "_bundled_secrets.py",
    "_bundled_secrets.pyc",
    "analysis_status.json",
    "corpus.db.enc",
    "corpus.db.salt",
    "corpus.sentinel.lock.json",
    "secrets.env",
    "sentinel.json",
}
FORBIDDEN_GENERATED_PREFIXES = (
    "adoptiq_report_",
    "adoptiq_source_data_",
    "adoptiq_measurement_",
    "adoptiq_parity_",
)

_CONTENT_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "github_classic_token",
        re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9_]{20,}"),
    ),
    (
        "github_fine_grained_token",
        re.compile(rb"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}"),
    ),
    # AWS publishes AKIAIOSFODNN7EXAMPLE inside botocore's official example
    # payloads. Exclude only that documented suffix while retaining the normal
    # high-signal access-key shape for every other value.
    (
        "aws_access_key",
        re.compile(
            rb"(?<![A-Za-z0-9])AKIA(?![0-9A-Z]{9}EXAMPLE)[0-9A-Z]{16}"
            rb"(?![A-Za-z0-9])"
        ),
    ),
    (
        "slack_token",
        re.compile(rb"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,}"),
    ),
    (
        "stripe_live_secret",
        re.compile(rb"(?<![A-Za-z0-9])sk_live_[A-Za-z0-9]{20,}"),
    ),
    (
        "private_key",
        re.compile(
            rb"-----BEGIN (?P<key_kind>(?:RSA |EC |OPENSSH )?)PRIVATE KEY-----\r?\n"
            rb"[A-Za-z0-9+/=\r\n]{64,131072}\r?\n"
            rb"-----END (?P=key_kind)PRIVATE KEY-----"
        ),
    ),
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?im)^[ \t]*[\"']?(?:[a-z0-9]+[_-]){0,4}(?:"
    rb"password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    rb"auth[_-]?token|client[_-]?secret|secret[_-]?key|"
    rb"secret[_-]?access[_-]?key|private[_-]?key"
    rb")[\"']?[ \t]*[:=][ \t]*(?P<value>[^\r\n,#]{1,512})"
)
_INTERNAL_EMAIL_DOMAIN = re.compile(rb"(?i)@cisco\.com\b")
_WORD_PAIR_CANDIDATE = re.compile(rb"(?i)(?=([A-Z][A-Za-z'-]{1,63}[ \t]+[A-Z][A-Za-z'-]{1,63}))")
_HASH_TOKEN_CANDIDATE = re.compile(rb"(?i)[A-Z0-9_.-]{3,256}")
_HASH_PATH_CANDIDATE = re.compile(rb"(?i)[A-Z0-9_.-]+(?:/[A-Z0-9_.-]+){2,16}")
_HASH_ORG_PHRASE_CANDIDATE = re.compile(rb"(?i)[A-Z][A-Z0-9_.-]{1,63}[ \t]+-[ \t]+[A-Z][A-Z0-9_.-]{1,63}")
# SHA-256 of one representative roster display value, normalized to lowercase
# with one ASCII space. Keeping only the digest lets verification detect a
# known leak without repeating the value in findings, logs, or manifests.
_KNOWN_ROSTER_IDENTIFIER_DIGESTS = {
    "c4a4b46d262778e1d602577eb7be50a4ddfcb5f1993bd14e9f27d55aa5366bbf",
}
# Digests cover person-specific handles/display names and production-only
# service/report/path locators found in historical developer artifacts. The
# underlying values are deliberately not repeated in source, findings, tests,
# logs, or manifests.
_KNOWN_INTERNAL_IDENTIFIER_DIGESTS = {
    "04c698a72f79f4afe8416f15cd7ce1f08f2dacf831a1c71a51958134837db145",
    "7dc2ee0378b02fb6cc95fada1bcd726100893e256d66f13fa9cf0ebcd018b200",
    "8c2017aa5d7e4780fd53489338b581d38e608d8b685cab6224f6193933c2406d",
    "9854b42a643d2c154d1916b25dc9a4ea8760fedd9d4eb89d3ef8b5aed19cc36e",
    "b8a4add849dd2dbb63fc52221b2d4eb87b81e99b7f34efbaf8beb7ae5f68f951",
}
_KNOWN_INTERNAL_LOCATOR_DIGESTS = {
    "1262dff2bd805f0db0b9ac03e4e23c6a2a6c657eb2bf5a37b7a252d87073b788",
    "239c09642e887960841879f1eeed93b8a17d85dba2fb57fa20eabc4fe1e50de3",
    "30eb36f43808fceeb76ee056b7c5c05fa319c90c74d4cc949af9b931f0400a32",
    "34ae7f59d8e350b1e946c15086f2e6596b5663e6ea94515811560a046b3b436a",
    "4d2ce59b10c50a79d5ecea09126639d12a8aefbbfc6a60eccbea35a1c6703168",
    "7c2b08c5ba3d632589ed167d65141cbf9e9738f8005ca9e2d912baa8f457fade",
    "d760c9f9bafae6e320e61b6926836970a15a84397dd5991ec6b5f2ed57e8356f",
}
DEVELOPER_CONFIG_OVERLAY_MARKER = b"adoptiq-developer-config-overlay/v1"
_PLACEHOLDER_VALUES = {
    "0",
    "changeme",
    "dummy",
    "example",
    "false",
    "none",
    "null",
    "password",
    "placeholder",
    "redacted",
    "replace-me",
    "replace_me",
    "sample",
    "secret",
    "test",
    "testing",
    "token",
    "true",
    "xxx",
}
_SCAN_OVERLAP = 132 * 1024
_MAX_NESTED_ARCHIVE_DEPTH = 8
_MAX_NESTED_ARCHIVE_ENTRIES = 250_000
_MAX_NESTED_ENTRY_BYTES = 512 * 1024 * 1024
_MAX_NESTED_TOTAL_BYTES = 8 * 1024 * 1024 * 1024


def _normalized_candidate_digest(candidate: bytes) -> str:
    normalized = b" ".join(candidate.split()).lower()
    return hashlib.sha256(normalized).hexdigest()


def _contains_known_digest(
    data: bytes,
    *,
    patterns: Sequence[re.Pattern[bytes]],
    digests: set[str],
) -> bool:
    return any(
        _normalized_candidate_digest(match.group(1) if match.lastindex else match.group(0)) in digests
        for pattern in patterns
        for match in pattern.finditer(data)
    )


def _redact_digest_matches(
    data: bytes,
    *,
    patterns: Sequence[re.Pattern[bytes]],
    digests: set[str],
    replacement: bytes,
) -> bytes:
    """Replace digest-matched values without storing or returning the originals."""

    spans: set[tuple[int, int]] = set()
    for pattern in patterns:
        for match in pattern.finditer(data):
            group = 1 if match.lastindex else 0
            candidate = match.group(group)
            if _normalized_candidate_digest(candidate) in digests:
                spans.add((match.start(group), match.end(group)))
    for start, end in sorted(spans, reverse=True):
        data = data[:start] + replacement + data[end:]
    return data


def redact_known_internal_values(data: bytes) -> bytes:
    """Return developer-safe bytes while never materializing sensitive literals.

    Detection and replacement use the same hash-only corpus as the candidate
    scanner.  This keeps generated overlays deterministic and ensures build
    logs and test failures expose rule names, never the matched source value.
    """

    redacted = _redact_digest_matches(
        data,
        patterns=(_HASH_PATH_CANDIDATE,),
        digests=_KNOWN_INTERNAL_LOCATOR_DIGESTS,
        replacement=b"local/placeholder/path",
    )
    redacted = _redact_digest_matches(
        redacted,
        patterns=(_HASH_ORG_PHRASE_CANDIDATE,),
        digests=_KNOWN_INTERNAL_LOCATOR_DIGESTS,
        replacement=b"Local Organization",
    )
    redacted = _redact_digest_matches(
        redacted,
        patterns=(_HASH_TOKEN_CANDIDATE,),
        digests=_KNOWN_INTERNAL_LOCATOR_DIGESTS,
        replacement=b"example.invalid",
    )
    redacted = _redact_digest_matches(
        redacted,
        patterns=(_WORD_PAIR_CANDIDATE,),
        digests=(_KNOWN_ROSTER_IDENTIFIER_DIGESTS | _KNOWN_INTERNAL_IDENTIFIER_DIGESTS),
        replacement=b"Synthetic User",
    )
    redacted = _redact_digest_matches(
        redacted,
        patterns=(_HASH_TOKEN_CANDIDATE,),
        digests=_KNOWN_INTERNAL_IDENTIFIER_DIGESTS,
        replacement=b"synthetic_user",
    )
    return _INTERNAL_EMAIL_DOMAIN.sub(b"@example.invalid", redacted)


def _is_packaged_documentation_path(display_path: str) -> bool:
    normalized = str(display_path or "").replace("\\", "/").casefold()
    if re.search(r"(?:^|/)[^/]+\.dist-info/metadata$", normalized):
        return True
    return "/botocore/data/" in normalized and normalized.rsplit("/", 1)[-1].startswith("examples-")


def _normalized_assignment_value(raw_value: bytes) -> str:
    value = raw_value.decode("utf-8", "replace").strip()
    value = value.rstrip("},]").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def _is_documented_placeholder(raw_value: bytes) -> bool:
    value = _normalized_assignment_value(raw_value)
    folded = value.casefold()
    if not value or folded in _PLACEHOLDER_VALUES:
        return True
    if "example" in folded or "redact" in folded:
        return True
    if folded.startswith(
        (
            "${",
            "$(",
            "%",
            "<",
            "[",
            "dummy_",
            "dummy-",
            "example_",
            "example-",
            "replace_",
            "replace-",
            "test_",
            "test-",
            "your_",
            "your-",
        )
    ):
        return True
    if any(
        marker in folded
        for marker in (
            "environ.get",
            "getenv(",
            "keyring.get",
            "secretmanager",
            "vault.read",
        )
    ):
        return True
    stripped = folded.strip("*x-_ ")
    return not stripped


def resource_path_findings(display_path: str) -> list[dict[str, str]]:
    normalized = str(display_path or "").replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1].casefold()
    findings: list[dict[str, str]] = []
    if basename in FORBIDDEN_RESOURCE_NAMES:
        findings.append({"rule": "forbidden_resource_name", "path": normalized})
    if any(basename.startswith(prefix) for prefix in FORBIDDEN_GENERATED_PREFIXES):
        findings.append({"rule": "generated_customer_artifact", "path": normalized})
    return findings


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    """Digest paths, types, modes, symlink targets, and file bytes."""

    base = root.expanduser().resolve()
    if not base.is_dir():
        return ""
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base).as_posix().encode("utf-8", "surrogateescape")
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode):
            kind = b"L"
        elif stat.S_ISDIR(metadata.st_mode):
            kind = b"D"
        elif stat.S_ISREG(metadata.st_mode):
            kind = b"F"
        else:
            kind = b"O"
        digest.update(kind)
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(stat.S_IMODE(metadata.st_mode).to_bytes(4, "big"))
        if kind == b"L":
            target = os.readlink(path).encode("utf-8", "surrogateescape")
            digest.update(len(target).to_bytes(8, "big"))
            digest.update(target)
        elif kind == b"F":
            digest.update(metadata.st_size.to_bytes(8, "big"))
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _scan_chunks(
    chunks: Iterable[bytes],
    *,
    display_path: str,
    include_assignments: bool = True,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    carry = b""
    matched: set[str] = set()
    for chunk in chunks:
        data = carry + chunk
        for label, pattern in _CONTENT_PATTERNS:
            if label not in matched and pattern.search(data):
                matched.add(label)
                findings.append({"rule": label, "path": display_path})
        if "internal_cisco_email" not in matched and _INTERNAL_EMAIL_DOMAIN.search(data):
            matched.add("internal_cisco_email")
            findings.append({"rule": "internal_cisco_email", "path": display_path})
        if (
            "known_roster_identifier" not in matched
            and b" " in data
            and _contains_known_digest(
                data,
                patterns=(_WORD_PAIR_CANDIDATE,),
                digests=_KNOWN_ROSTER_IDENTIFIER_DIGESTS,
            )
        ):
            matched.add("known_roster_identifier")
            findings.append({"rule": "known_roster_identifier", "path": display_path})
        if "known_internal_identifier" not in matched and _contains_known_digest(
            data,
            patterns=(_HASH_TOKEN_CANDIDATE, _WORD_PAIR_CANDIDATE),
            digests=_KNOWN_INTERNAL_IDENTIFIER_DIGESTS,
        ):
            matched.add("known_internal_identifier")
            findings.append({"rule": "known_internal_identifier", "path": display_path})
        if "internal_service_locator" not in matched and _contains_known_digest(
            data,
            patterns=(
                _HASH_TOKEN_CANDIDATE,
                _HASH_PATH_CANDIDATE,
                _HASH_ORG_PHRASE_CANDIDATE,
            ),
            digests=_KNOWN_INTERNAL_LOCATOR_DIGESTS,
        ):
            matched.add("internal_service_locator")
            findings.append({"rule": "internal_service_locator", "path": display_path})
        if (
            include_assignments
            and "credential_assignment" not in matched
            and not _is_packaged_documentation_path(display_path)
        ):
            for assignment in _CREDENTIAL_ASSIGNMENT.finditer(data):
                if not _is_documented_placeholder(assignment.group("value")):
                    matched.add("credential_assignment")
                    findings.append({"rule": "credential_assignment", "path": display_path})
                    break
        carry = data[-_SCAN_OVERLAP:]
    return findings


def scan_blob(
    display_path: str,
    data: bytes,
    *,
    include_assignments: bool = True,
) -> list[dict[str, str]]:
    return _scan_chunks(
        (data,),
        display_path=display_path,
        include_assignments=include_assignments,
    )


def scan_file(path: Path, *, display_path: str | None = None) -> dict[str, Any]:
    candidate = path.expanduser().resolve()
    if not candidate.is_file():
        return {
            "ok": False,
            "bytes_scanned": 0,
            "findings": [{"rule": "scan_file_missing", "path": str(candidate)}],
        }
    try:
        with candidate.open("rb") as handle:
            findings = _scan_chunks(
                iter(lambda: handle.read(1024 * 1024), b""),
                display_path=display_path or candidate.name,
            )
        return {
            "ok": not findings,
            "bytes_scanned": candidate.stat().st_size,
            "findings": findings,
        }
    except OSError:
        return {
            "ok": False,
            "bytes_scanned": 0,
            "findings": [{"rule": "content_scan_failed", "path": display_path or candidate.name}],
        }


def scan_tree(root: Path) -> dict[str, Any]:
    base = root.expanduser().resolve()
    findings: list[dict[str, str]] = []
    files_scanned = 0
    bytes_scanned = 0
    archive_state: dict[str, Any] = {
        "findings": findings,
        "errors": [],
        "entries_total": 0,
        "entries_scanned": 0,
        "bytes_scanned": 0,
        "containers_scanned": 0,
        "namespace_entries_scanned": 0,
        "directory_entries_scanned": 0,
    }
    if not base.is_dir():
        return {
            "ok": False,
            "files_scanned": 0,
            "bytes_scanned": 0,
            "findings": [{"rule": "scan_root_missing", "path": str(base)}],
        }

    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(base).as_posix()
        findings.extend(resource_path_findings(relative))
        files_scanned += 1
        try:
            bytes_scanned += path.stat().st_size
            with path.open("rb") as handle:
                findings.extend(
                    _scan_chunks(
                        iter(lambda: handle.read(1024 * 1024), b""),
                        display_path=relative,
                    )
                )
            is_declared_zip = path.suffix.casefold() in {
                ".docx",
                ".pptx",
                ".whl",
                ".xlsx",
                ".zip",
            }
            if is_declared_zip or zipfile.is_zipfile(path):
                size = path.stat().st_size
                if size > _MAX_NESTED_ENTRY_BYTES:
                    archive_state["errors"].append(f"filesystem ZIP is too large: {relative}")
                else:
                    _scan_zip_payload(
                        archive_state,
                        display_name=relative,
                        payload=path.read_bytes(),
                        depth=0,
                    )
        except OSError:
            findings.append({"rule": "content_scan_failed", "path": relative})
    return {
        "ok": not findings and not archive_state["errors"],
        "files_scanned": files_scanned,
        "bytes_scanned": bytes_scanned,
        "archive_entries_total": archive_state["entries_total"],
        "archive_entries_scanned": archive_state["entries_scanned"],
        "archive_bytes_scanned": archive_state["bytes_scanned"],
        "archive_containers_scanned": archive_state["containers_scanned"],
        "archive_errors": archive_state["errors"],
        "findings": findings,
    }


def _normalized_archive_name(name: object) -> str:
    return str(name).replace("\\", "/")


def _archive_name_is_safe(name: str) -> bool:
    if not name or "\x00" in name or name.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    return all(part != ".." for part in name.split("/"))


def _reserve_archive_entry(state: dict[str, Any], display_name: str, *, archive_name: str | None = None) -> bool:
    state["entries_total"] += 1
    state["findings"].extend(resource_path_findings(display_name))
    if state["entries_total"] > _MAX_NESTED_ARCHIVE_ENTRIES:
        state["errors"].append(f"nested archive entry limit exceeded: {display_name}")
        return False
    relative_name = archive_name if archive_name is not None else display_name
    if not _archive_name_is_safe(relative_name):
        state["errors"].append(f"nested archive entry has unsafe path: {display_name}")
        return False
    return True


def _mark_zero_byte_entry_scanned(
    state: dict[str, Any],
    *,
    namespace_package: bool = False,
    directory: bool = False,
) -> None:
    state["entries_scanned"] += 1
    if namespace_package:
        state["namespace_entries_scanned"] += 1
    if directory:
        state["directory_entries_scanned"] += 1


def _looks_like_zip(payload: bytes) -> bool:
    return zipfile.is_zipfile(io.BytesIO(payload))


def _scan_archive_payload(
    state: dict[str, Any],
    *,
    display_name: str,
    payload: bytes,
    depth: int,
    include_assignments: bool = True,
) -> bool:
    if len(payload) > _MAX_NESTED_ENTRY_BYTES:
        state["errors"].append(f"nested archive entry is too large: {display_name}")
        return False
    if state["bytes_scanned"] + len(payload) > _MAX_NESTED_TOTAL_BYTES:
        state["errors"].append(f"nested archive decompressed-byte limit exceeded: {display_name}")
        return False
    state["entries_scanned"] += 1
    state["bytes_scanned"] += len(payload)
    try:
        state["findings"].extend(
            scan_blob(
                display_name,
                payload,
                include_assignments=include_assignments,
            )
        )
    except Exception:  # noqa: BLE001 - scanner errors must fail closed
        state["errors"].append(f"nested archive entry could not be content-scanned: {display_name}")
        return False
    # A Python module may legitimately be named ``implementations.zip``;
    # code entries are identified by the caller through include_assignments.
    # Only data/resource entries use their suffix as a declared ZIP contract.
    declared_zip = include_assignments and display_name.casefold().endswith((".docx", ".pptx", ".whl", ".xlsx", ".zip"))
    if include_assignments and (declared_zip or _looks_like_zip(payload)):
        _scan_zip_payload(
            state,
            display_name=display_name,
            payload=payload,
            depth=depth + 1,
        )
    return True


def _scan_zip_payload(
    state: dict[str, Any],
    *,
    display_name: str,
    payload: bytes,
    depth: int,
) -> None:
    if depth > _MAX_NESTED_ARCHIVE_DEPTH:
        state["errors"].append(f"nested archive recursion limit exceeded: {display_name}")
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        members = archive.infolist()
    except Exception:  # noqa: BLE001 - unreadable nested archives fail closed
        state["errors"].append(f"nested ZIP could not be read: {display_name}")
        return

    state["containers_scanned"] += 1
    with archive:
        for member in members:
            normalized_name = _normalized_archive_name(member.filename)
            nested_display_name = f"{display_name.rstrip('/')}/{normalized_name}"
            if not _reserve_archive_entry(
                state,
                nested_display_name,
                archive_name=normalized_name,
            ):
                continue
            if member.is_dir():
                _mark_zero_byte_entry_scanned(state, directory=True)
                continue
            if member.file_size > _MAX_NESTED_ENTRY_BYTES:
                state["errors"].append(f"nested ZIP entry is too large: {nested_display_name}")
                continue
            if state["bytes_scanned"] + member.file_size > _MAX_NESTED_TOTAL_BYTES:
                state["errors"].append(f"nested archive decompressed-byte limit exceeded: {nested_display_name}")
                continue
            try:
                with archive.open(member, "r") as handle:
                    nested_payload = handle.read(_MAX_NESTED_ENTRY_BYTES + 1)
            except Exception:  # noqa: BLE001 - unreadable payload must fail closed
                state["errors"].append(f"nested ZIP entry could not be extracted: {nested_display_name}")
                continue
            if len(nested_payload) != member.file_size:
                state["errors"].append(f"nested ZIP entry size mismatch: {nested_display_name}")
                continue
            _scan_archive_payload(
                state,
                display_name=nested_display_name,
                payload=nested_payload,
                depth=depth,
            )


def _is_carchive_pyz_entry(name: object, metadata: object) -> bool:
    typecode: object | None = None
    if isinstance(metadata, (tuple, list)) and len(metadata) >= 5:
        typecode = metadata[-1]
        if isinstance(typecode, bytes):
            typecode = typecode.decode("ascii", "replace")
    return typecode == "z" or _normalized_archive_name(name).casefold().endswith(".pyz")


def _is_pyz_namespace_entry(metadata: object) -> bool:
    return isinstance(metadata, (tuple, list)) and bool(metadata) and metadata[0] == 3


def _is_pyz_code_entry(metadata: object) -> bool:
    return isinstance(metadata, (tuple, list)) and bool(metadata) and metadata[0] in {0, 1}


def _scan_embedded_pyz(
    state: dict[str, Any],
    *,
    carchive_reader: object,
    name: object,
    display_name: str,
    depth: int,
) -> None:
    if depth > _MAX_NESTED_ARCHIVE_DEPTH:
        state["errors"].append(f"nested archive recursion limit exceeded: {display_name}")
        return
    try:
        pyz_reader = carchive_reader.open_embedded_archive(name)
    except Exception:  # noqa: BLE001 - unreadable payload must fail closed
        state["errors"].append(f"embedded PYZ archive could not be opened: {display_name}")
        return

    pyz_toc = getattr(pyz_reader, "toc", None)
    if not isinstance(pyz_toc, Mapping):
        state["errors"].append(f"embedded PYZ archive has no readable inventory: {display_name}")
        return
    state["containers_scanned"] += 1
    for nested_name in sorted(pyz_toc, key=lambda item: str(item)):
        normalized_name = _normalized_archive_name(nested_name)
        nested_display_name = f"{display_name.rstrip('/')}/{normalized_name}"
        if not _reserve_archive_entry(
            state,
            nested_display_name,
            archive_name=normalized_name,
        ):
            continue
        namespace_package = _is_pyz_namespace_entry(pyz_toc[nested_name])
        try:
            nested_payload = pyz_reader.extract(nested_name, raw=True)
        except Exception:  # noqa: BLE001 - unreadable payload must fail closed
            state["errors"].append(f"embedded PYZ entry could not be extracted: {nested_display_name}")
            continue
        if nested_payload is None:
            if namespace_package:
                _mark_zero_byte_entry_scanned(state, namespace_package=True)
            else:
                state["errors"].append(f"embedded PYZ entry did not return bytes: {nested_display_name}")
            continue
        if namespace_package:
            state["errors"].append(f"embedded PYZ namespace entry unexpectedly returned bytes: {nested_display_name}")
            continue
        if not isinstance(nested_payload, (bytes, bytearray, memoryview)):
            state["errors"].append(f"embedded PYZ entry did not return bytes: {nested_display_name}")
            continue
        payload_bytes = bytes(nested_payload)
        if normalized_name == "config":
            state["config_module_entries"] += 1
            if DEVELOPER_CONFIG_OVERLAY_MARKER in payload_bytes:
                state["developer_config_overlay_marker_found"] = True
        _scan_archive_payload(
            state,
            display_name=nested_display_name,
            payload=payload_bytes,
            depth=depth,
            # Raw marshaled code contains docstrings, parameter descriptions,
            # and arbitrary binary constants that resemble configuration
            # assignments. Provider-token and complete-key patterns still run;
            # generic key=value checks remain enabled for PYZ data entries.
            include_assignments=not _is_pyz_code_entry(pyz_toc[nested_name]),
        )


def scan_pyinstaller_carchive(
    executable: Path,
    *,
    display_prefix: str = "archive",
    require_developer_config_overlay: bool = False,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Extract and content-scan every payload in a PyInstaller CArchive.

    The filesystem-level scan is not sufficient for a frozen executable: most
    Python modules are compressed inside the CArchive and their bytes may not
    be visible in the outer executable.  Treat every unreadable or non-bytes
    entry as a verification failure rather than silently skipping it.
    """

    candidate = executable.expanduser().resolve()
    resources: list[tuple[str, bytes]] = []
    state: dict[str, Any] = {
        "findings": [],
        "errors": [],
        "entries_total": 0,
        "entries_scanned": 0,
        "bytes_scanned": 0,
        "containers_scanned": 0,
        "namespace_entries_scanned": 0,
        "directory_entries_scanned": 0,
        "config_module_entries": 0,
        "developer_config_overlay_marker_found": False,
    }

    if not candidate.is_file():
        return (
            {
                "ok": False,
                "entries_total": 0,
                "entries_scanned": 0,
                "bytes_scanned": 0,
                "containers_scanned": 0,
                "namespace_entries_scanned": 0,
                "directory_entries_scanned": 0,
                "config_module_entries": 0,
                "developer_config_overlay_marker_found": False,
                "findings": [],
                "errors": ["PyInstaller CArchive executable is missing"],
            },
            {},
        )

    try:
        from PyInstaller.archive.readers import CArchiveReader  # noqa: PLC0415

        reader = CArchiveReader(str(candidate))
        names = sorted(reader.toc, key=lambda item: str(item))
        for name in names:
            normalized_name = _normalized_archive_name(name)
            display_name = f"{display_prefix.rstrip('/')}/{normalized_name}"
            if not _reserve_archive_entry(state, display_name):
                continue
            try:
                extracted = reader.extract(name)
            except Exception:  # noqa: BLE001 - unreadable payload must fail closed
                state["errors"].append(f"archive entry could not be extracted: {name}")
                continue
            if not isinstance(extracted, (bytes, bytearray, memoryview)):
                state["errors"].append(f"archive entry did not return bytes: {name}")
                continue
            payload = bytes(extracted)
            _scan_archive_payload(
                state,
                display_name=display_name,
                payload=payload,
                depth=0,
            )
            resources.append((str(name), payload))
            if _is_carchive_pyz_entry(name, reader.toc[name]):
                _scan_embedded_pyz(
                    state,
                    carchive_reader=reader,
                    name=name,
                    display_name=display_name,
                    depth=1,
                )
    except Exception as exc:  # noqa: BLE001 - native verifier emits stable failure
        state["errors"].append(f"PyInstaller CArchive could not be read: {type(exc).__name__}")

    if require_developer_config_overlay:
        if state["config_module_entries"] != 1:
            state["errors"].append("developer candidate must contain exactly one config module")
        if not state["developer_config_overlay_marker_found"]:
            state["errors"].append("developer config overlay marker is missing from frozen config")

    if state["entries_scanned"] != state["entries_total"] and not state["errors"]:
        state["errors"].append("PyInstaller CArchive scan did not cover every entry")
    result = {
        "ok": (not state["findings"] and not state["errors"] and state["entries_scanned"] == state["entries_total"]),
        "entries_total": state["entries_total"],
        "entries_scanned": state["entries_scanned"],
        "bytes_scanned": state["bytes_scanned"],
        "containers_scanned": state["containers_scanned"],
        "namespace_entries_scanned": state["namespace_entries_scanned"],
        "directory_entries_scanned": state["directory_entries_scanned"],
        "config_module_entries": state["config_module_entries"],
        "developer_config_overlay_marker_found": state["developer_config_overlay_marker_found"],
        "findings": state["findings"],
        "errors": state["errors"],
    }
    return result, selected_resource_bytes(resources)


def archive_inventory_contract(inventory_text: str) -> dict[str, Any]:
    folded = str(inventory_text or "").casefold()
    missing_modules = sorted(
        module
        for module in REQUIRED_ARCHIVE_MODULES
        if re.search(rf"(?m)^\s*{re.escape(module.casefold())}\s*$", folded) is None
    )
    forbidden_entries = sorted(name for name in FORBIDDEN_ARCHIVE_NAMES if name.casefold() in folded)
    return {
        "ok": not missing_modules and not forbidden_entries,
        "required_modules": list(REQUIRED_ARCHIVE_MODULES),
        "missing_required_modules": missing_modules,
        "forbidden_entries": forbidden_entries,
    }


def validate_developer_payload(
    resources: Mapping[str, bytes],
    *,
    expected_platform: str,
    expected_version: str = "",
    expected_build: str = "",
    expected_commit: str = "",
) -> dict[str, Any]:
    """Validate sanitized configuration and embedded source identity."""

    errors: list[str] = []
    required = {
        "DEVELOPER_ONLY_BUILD.txt",
        "DEVELOPER_BUILD_METADATA.json",
        "customer_aliases.defaults.json",
        "team_config.json",
    }
    missing = sorted(required.difference(resources))
    if missing:
        errors.append("required sanitized developer resources are missing")

    marker = resources.get("DEVELOPER_ONLY_BUILD.txt", b"").decode("utf-8", "replace")
    if "Synthetic configuration only" not in marker or "Not production-ready" not in marker:
        errors.append("developer-only marker is missing or invalid")

    try:
        metadata = json.loads(resources.get("DEVELOPER_BUILD_METADATA.json", b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        metadata = {}
        errors.append("developer build metadata is invalid JSON")
    expected_metadata = {
        "schema_version": "developer-build-metadata/v2",
        "developer_only": True,
        "production_ready": False,
        "sanitized_configuration": True,
        "sanitized_config_overlay_required": True,
        "prebaked_corpus_included": False,
        "bundled_credentials_included": False,
        "target_platform": expected_platform,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            errors.append(f"developer build metadata mismatch: {key}")
    for key, expected in (
        ("version", expected_version),
        ("build", expected_build),
        ("source_commit_sha", expected_commit),
    ):
        if expected and str(metadata.get(key) or "") != expected:
            errors.append(f"developer build metadata mismatch: {key}")

    try:
        aliases = json.loads(resources.get("customer_aliases.defaults.json", b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        aliases = {}
        errors.append("developer customer aliases are invalid JSON")
    if aliases.get("groups") != []:
        errors.append("developer customer aliases are not empty")

    try:
        team = json.loads(resources.get("team_config.json", b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        team = {}
        errors.append("developer team configuration is invalid JSON")
    roster = team.get("team_roster") if isinstance(team, dict) else None
    if not isinstance(roster, list) or not roster:
        errors.append("developer team configuration has no synthetic roster")
    else:
        for row in roster:
            email = str(row.get("email") or "") if isinstance(row, dict) else ""
            if not email.casefold().endswith("@example.invalid"):
                errors.append("developer team configuration contains a non-synthetic email")
                break

    content_findings: list[dict[str, str]] = []
    for name, payload in resources.items():
        content_findings.extend(scan_blob(name, payload))
    if content_findings:
        errors.append("developer resources contain credential-shaped content")

    return {
        "ok": not errors,
        "metadata": metadata,
        "required_resources": sorted(required),
        "missing_resources": missing,
        "credential_findings": content_findings,
        "errors": errors,
    }


def selected_resource_bytes(
    names_and_payloads: Sequence[tuple[str, bytes]],
) -> dict[str, bytes]:
    """Normalize archive paths and return the four developer resources."""

    selected: dict[str, bytes] = {}
    required_names = {
        "DEVELOPER_ONLY_BUILD.txt",
        "DEVELOPER_BUILD_METADATA.json",
        "customer_aliases.defaults.json",
        "team_config.json",
    }
    for raw_name, payload in names_and_payloads:
        normalized = str(raw_name).replace("\\", "/").rsplit("/", 1)[-1]
        if normalized in required_names:
            selected[normalized] = payload
    return selected


__all__ = [
    "FORBIDDEN_ARCHIVE_NAMES",
    "FORBIDDEN_RESOURCE_NAMES",
    "REQUIRED_ARCHIVE_MODULES",
    "archive_inventory_contract",
    "redact_known_internal_values",
    "resource_path_findings",
    "scan_blob",
    "scan_file",
    "scan_pyinstaller_carchive",
    "scan_tree",
    "selected_resource_bytes",
    "sha256_file",
    "sha256_tree",
    "validate_developer_payload",
]
