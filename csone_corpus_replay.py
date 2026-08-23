"""Privacy-preserving replay of real CSOne workbook shape and distributions.

The replay uses the production workbook loader, then immediately projects the
result into an allow-listed pseudonymous schema.  It preserves report-relevant
status, severity, technology, date, identifier-missingness, and BEMS/TAC shape
while discarding customer names, emails, free text, and source identifiers.
Nothing in this module is imported by the production application or build.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Mapping, Optional
from zipfile import BadZipFile, ZipFile

import pandas as pd

from data_normalization import (
    classify_case_type,
    normalize_priority_label,
    normalize_status_label,
)
from local_acceptance_lab import LocalAcceptanceBundle, SCHEMA_VERSION, SOURCE_MODE


DATE_COLUMNS = (
    "Date/Time Opened",
    "Date/Time Closed",
)
SOURCE_CATEGORY_COLUMNS = (
    "Severity",
    "Case Status",
    "Tech.",
    "Sub Technology",
    "Sub Tech.",
    "Service Tier",
    "Product: Product Name",
    "Highest Priority",
    "Problem Code",
    "Resolution Code",
    "Case Origin",
    "# of Case Owner Changes",
)
PSEUDONYMOUS_POLICY_VERSION = "csone-replay-privacy/v1"
PREPARED_REPLAY_SCHEMA_VERSION = "prepared-csone-replay/v2"
STATUS_COVERAGE_SCHEMA_VERSION = "csone-status-coverage/v1"
MAX_PREPARED_REPLAY_BYTES = 16 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MAX_SOURCE_CATEGORY_LENGTH = 256
_MAX_OUTPUT_TEXT_LENGTH = 160
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SAFE_SEVERITIES = frozenset({"P1", "P2", "P3", "P4", "Unknown"})
_SAFE_STATUSES = frozenset({"Open", "Closed", "Unknown"})
_STATUS_COVERAGE_KEYS = frozenset(
    {
        "schema_version",
        "row_count",
        "raw_missing_count",
        "raw_missing_ratio",
        "normalized_unknown_count",
        "normalized_unknown_ratio",
        "populated_unclassified_count",
        "classified_count",
        "source_state",
    }
)
_SAFE_TECHNOLOGIES = frozenset(
    {
        "Webex Calling",
        "Webex Meetings & Messaging",
        "Webex Contact Center",
        "Webex Contact Center Enterprise",
        "Cisco UCCE",
        "Cisco UCCX",
        "Contact Center / Unclassified",
        "Other / Unclassified",
        "Not provided",
    }
)
_SAFE_SERVICE_TIERS = frozenset(
    {"Premium", "Enhanced", "Standard", "Other / Unclassified", "Not provided"}
)
_SAFE_CASE_ORIGINS = frozenset(
    {
        "Web",
        "Email",
        "Phone",
        "Chat",
        "Partner",
        "Automated",
        "Other / Unclassified",
        "Not provided",
    }
)
_SAFE_CASE_TYPE_TEXT = frozenset(
    {
        "provisioning request",
        "break-fix technical incident",
        "standard support inquiry",
    }
)
_TECHNOLOGY_SOURCE_COLUMNS = (
    "Sub Technology",
    "Sub Tech.",
    "Tech.",
    "Product: Product Name",
)
_CASE_TYPE_SOURCE_COLUMNS = (
    "Case Type",
    "CASE_TYPE",
    "CASE_TYPE_C",
    "Request Type",
    "REQUEST_TYPE",
    "REQUEST_TYPE_C",
    "Category",
    "CATEGORY",
    "CATEGORY_C",
    "Title",
    "title",
    "Problem Description",
    "DESCRIPTION_C",
    "SUBJECT",
    "SUBJECT_C",
    "Case Status",
    "Status",
)
_TECHNOLOGY_PATTERNS = (
    ("Cisco UCCX", (r"\buccx\b", r"unified contact center express")),
    ("Cisco UCCE", (r"\bucce\b", r"unified contact center enterprise")),
    (
        "Webex Contact Center Enterprise",
        (
            r"webex contact cent(?:er|re) enterprise",
            r"\bwxcce\b",
            r"contact center enterprise",
        ),
    ),
    (
        "Webex Contact Center",
        (r"webex contact cent(?:er|re)", r"\bwxcc\b"),
    ),
    (
        "Webex Meetings & Messaging",
        (
            r"webex meet",
            r"webex messag",
            r"webex app",
            r"\bmeeting(?:s)?\b",
            r"\bmessaging\b",
        ),
    ),
    (
        "Webex Calling",
        (
            r"webex call",
            r"\bcalling\b",
            r"\bbroadworks\b",
        ),
    ),
)
_TEXT_PRESENCE_REPLACEMENTS = {
    "Problem Description": "Pseudonymized problem detail present.",
    "CSE Action Plan": "Pseudonymized action plan present.",
    "Last Cisco Update": "Pseudonymized provider update present.",
    "Resolution Summary": "Pseudonymized resolution detail present.",
    "Problem Details": "Pseudonymized problem evidence present.",
    "Customer Activity": "Pseudonymized customer activity present.",
}
_SYNTHETIC_SLOTS = (
    ("ACC-001", "Acme Corporation", "Alex Rivera", "SUB-001"),
    ("ACC-002", "Beta Industries", "Alex Rivera", "SUB-002"),
    ("ACC-001", "Acme Corporation", "Morgan Lee", "SUB-001"),
    ("ACC-003", "Gamma Public Sector", "Morgan Lee", "SUB-003"),
)
_PSEUDONYMOUS_OUTPUT_COLUMNS = (
    "SR Number",
    "Case Number",
    "case_id",
    "ACCOUNT_ID_C",
    "Customer",
    "Customer Name: Customer Name",
    "customer",
    "FIXTURE_MEMBER",
    "Subscription Reference Id",
    "Transaction ID",
    "bemscsc_refs",
    "bems_ref",
    "Severity",
    "Case Status",
    "Tech.",
    "Sub Technology",
    "Sub Tech.",
    "Service Tier",
    "Product: Product Name",
    "Highest Priority",
    "Problem Code",
    "Resolution Code",
    "Case Origin",
    "# of Case Owner Changes",
    "Technology",
    "severity",
    "status",
    "Date/Time Opened",
    "Date/Time Closed",
    "Title",
    *_TEXT_PRESENCE_REPLACEMENTS,
    "Current Contact Email",
    "Case Owner: Full Name",
    "LOCAL_ACCEPTANCE_RECORD_ID",
)
REPRESENTATIVE_POSITIONS = (0.0, 0.5, 1.0)
REQUIRED_REPLAY_STRATA = ("old", "mid", "new")
_REPLAY_FILE_ID_COLUMN = "__adoptiq_replay_file_id"
_REPLAY_STRATA_COLUMN = "__adoptiq_replay_strata"
_FRAME_EXPORT_ATTRS = (
    "exported_at_utc",
    "exported_at",
    "report_generated_at_utc",
    "report_generated_at",
    "data_as_of_utc",
)
_FRAME_EXPORT_COLUMNS = (
    "Exported At",
    "Export Date",
    "Report Generated At",
    "Data As Of",
)


@dataclass(frozen=True)
class CorpusInputLimits:
    """Hard pre-loader ceilings for an explicitly selected local corpus."""

    max_workbooks: int = 512
    max_workbook_bytes: int = 256 * 1024 * 1024
    max_aggregate_bytes: int = 1024 * 1024 * 1024
    max_zip_entries: int = 50_000
    max_zip_entry_expanded_bytes: int = 512 * 1024 * 1024
    max_zip_total_expanded_bytes: int = 2 * 1024 * 1024 * 1024
    max_zip_expansion_ratio: float = 500.0

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_workbooks,
            self.max_workbook_bytes,
            self.max_aggregate_bytes,
            self.max_zip_entries,
            self.max_zip_entry_expanded_bytes,
            self.max_zip_total_expanded_bytes,
        )
        if any(type(value) is not int or value < 1 for value in integer_limits):
            raise ValueError("CSOne corpus input limits must be positive integers")
        if not isinstance(self.max_zip_expansion_ratio, (int, float)) or not (
            1.0 <= float(self.max_zip_expansion_ratio) <= 10_000.0
        ):
            raise ValueError("CSOne corpus expansion limit is invalid")


DEFAULT_CORPUS_INPUT_LIMITS = CorpusInputLimits()


@dataclass(frozen=True)
class PreparedCsoneReplay:
    """Immutable, canonical, privacy-validated replay transport.

    Only the canonical JSON bytes cross the process boundary.  Source workbook
    rows and paths never enter this object and pickle is deliberately unsupported.
    """

    payload: bytes
    payload_sha256: str
    frame_sha256: str
    coverage_sha256: str
    source_snapshot_sha256: str
    manifest_sha256: str
    as_of_utc: str
    max_rows: int

    @property
    def byte_length(self) -> int:
        return len(self.payload)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _coverage_ratio(count: int, row_count: int) -> float:
    return round(int(count) / int(row_count), 6) if row_count else 0.0


def _build_status_coverage(
    raw_missing_mask: pd.Series,
    normalized_statuses: pd.Series,
) -> dict[str, Any]:
    """Bind pre-normalization missingness to the safe replay projection."""

    raw_missing = raw_missing_mask.fillna(False).astype(bool)
    normalized = normalized_statuses.fillna("").astype(str).str.strip()
    if len(raw_missing) != len(normalized):
        raise ValueError("CSOne replay status coverage rows do not align")
    row_count = int(len(normalized))
    raw_missing_count = int(raw_missing.sum())
    normalized_unknown_count = int(normalized.eq("Unknown").sum())
    if raw_missing_count > normalized_unknown_count:
        raise ValueError("CSOne replay normalized status lost raw missingness")
    coverage = {
        "schema_version": STATUS_COVERAGE_SCHEMA_VERSION,
        "row_count": row_count,
        "raw_missing_count": raw_missing_count,
        "raw_missing_ratio": _coverage_ratio(raw_missing_count, row_count),
        "normalized_unknown_count": normalized_unknown_count,
        "normalized_unknown_ratio": _coverage_ratio(
            normalized_unknown_count,
            row_count,
        ),
        "populated_unclassified_count": (
            normalized_unknown_count - raw_missing_count
        ),
        "classified_count": row_count - normalized_unknown_count,
        "source_state": "partial" if normalized_unknown_count else "available",
    }
    _validate_status_coverage(coverage, normalized_statuses=normalized)
    return coverage


def _validate_status_coverage(
    coverage: Mapping[str, Any],
    *,
    normalized_statuses: pd.Series,
) -> dict[str, Any]:
    """Fail closed unless exact counts, ratios, and safe rows reconcile."""

    if not isinstance(coverage, Mapping) or set(coverage) != _STATUS_COVERAGE_KEYS:
        raise ValueError("prepared CSOne replay status coverage inventory is invalid")
    if coverage.get("schema_version") != STATUS_COVERAGE_SCHEMA_VERSION:
        raise ValueError("prepared CSOne replay status coverage schema is invalid")
    count_keys = (
        "row_count",
        "raw_missing_count",
        "normalized_unknown_count",
        "populated_unclassified_count",
        "classified_count",
    )
    if any(type(coverage.get(key)) is not int for key in count_keys):
        raise ValueError("prepared CSOne replay status coverage count is invalid")
    row_count = coverage["row_count"]
    raw_missing_count = coverage["raw_missing_count"]
    normalized_unknown_count = coverage["normalized_unknown_count"]
    populated_unclassified_count = coverage["populated_unclassified_count"]
    classified_count = coverage["classified_count"]
    observed_unknown_count = int(
        normalized_statuses.fillna("").astype(str).str.strip().eq("Unknown").sum()
    )
    if (
        row_count != len(normalized_statuses)
        or not 0 <= raw_missing_count <= normalized_unknown_count <= row_count
        or populated_unclassified_count
        != normalized_unknown_count - raw_missing_count
        or classified_count != row_count - normalized_unknown_count
        or normalized_unknown_count != observed_unknown_count
    ):
        raise ValueError("prepared CSOne replay status coverage does not reconcile")
    for ratio_key, count in (
        ("raw_missing_ratio", raw_missing_count),
        ("normalized_unknown_ratio", normalized_unknown_count),
    ):
        ratio = coverage.get(ratio_key)
        if type(ratio) is not float or ratio != _coverage_ratio(count, row_count):
            raise ValueError("prepared CSOne replay status coverage ratio is invalid")
    expected_state = "partial" if normalized_unknown_count else "available"
    if coverage.get("source_state") != expected_state:
        raise ValueError("prepared CSOne replay status coverage state is invalid")
    return dict(coverage)


def _status_source_attrs(coverage: Mapping[str, Any]) -> dict[str, Any]:
    state = str(coverage.get("source_state") or "partial")
    detail = (
        "TAC replay status coverage: "
        f"{int(coverage.get('classified_count') or 0)}/"
        f"{int(coverage.get('row_count') or 0)} classified; "
        f"{int(coverage.get('raw_missing_count') or 0)} raw source status missing; "
        f"{int(coverage.get('populated_unclassified_count') or 0)} populated status "
        "unclassified."
    )
    return {
        "source_state": state,
        "partial": state == "partial",
        "fetch_error_partial": state == "partial",
        "source_mode_detail": detail,
        "source_unavailable_detail": detail,
        "tac_status_coverage": dict(coverage),
        "status_coverage_sha256": _sha256_json(coverage),
    }


def _require_sha256(value: Any, label: str) -> str:
    text = str(value or "").strip().casefold()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"prepared CSOne replay requires valid {label}")
    return text


def _inventory_stat_contract(
    corpus_dir: Path,
    *,
    limits: CorpusInputLimits = DEFAULT_CORPUS_INPUT_LIMITS,
) -> dict[str, Any]:
    """Return a path-free exact inventory/content binding for one snapshot."""

    root = corpus_dir.expanduser().resolve()
    entries: list[dict[str, Any]] = []
    for path in discover_corpus_workbooks(root, limits=limits):
        contract = _workbook_content_contract(
            path,
            max_bytes=limits.max_workbook_bytes,
        )
        relative_identity = hashlib.sha256(path.name.encode("utf-8", "replace")).hexdigest()
        entries.append(
            {
                "identity_sha256": relative_identity,
                **contract,
            }
        )
    payload = {
        "workbook_count": len(entries),
        "total_bytes": sum(item["size_bytes"] for item in entries),
        "entries": sorted(entries, key=lambda item: item["identity_sha256"]),
    }
    return {
        "workbook_count": payload["workbook_count"],
        "total_bytes": payload["total_bytes"],
        "sha256": _sha256_json(payload),
    }


def _workbook_content_contract(
    path: Path,
    *,
    max_bytes: int = DEFAULT_CORPUS_INPUT_LIMITS.max_workbook_bytes,
) -> dict[str, Any]:
    """Stream one regular workbook and bind content plus stable file identity."""

    requested = path.expanduser()
    before = requested.lstat()
    if not stat.S_ISREG(before.st_mode) or requested.is_symlink():
        raise ValueError("CSOne corpus input rejected: non_regular_workbook")
    if not 1 <= before.st_size <= int(max_bytes):
        raise ValueError("CSOne corpus input rejected: workbook_size")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(requested, flags)
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise ValueError("CSOne corpus workbook identity changed before hashing")
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = requested.lstat()
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(
        getattr(before, field) != getattr(after_fd, field)
        or getattr(before, field) != getattr(after_path, field)
        for field in identity_fields
    ):
        raise ValueError("CSOne corpus workbook changed while hashing")
    return {
        "mode": int(stat.S_IMODE(before.st_mode)),
        "size_bytes": int(before.st_size),
        "mtime_ns": int(before.st_mtime_ns),
        "content_sha256": digest.hexdigest(),
    }


def _validate_xlsx_archive(path: Path, limits: CorpusInputLimits) -> None:
    """Reject pathological XLSX containers before invoking the row loader."""

    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
    except (BadZipFile, OSError, ValueError) as exc:
        raise ValueError("CSOne corpus input rejected: invalid_xlsx_archive") from exc
    if len(entries) > limits.max_zip_entries:
        raise ValueError("CSOne corpus input rejected: zip_entry_count")
    expanded_total = 0
    compressed_total = 0
    for entry in entries:
        expanded = int(entry.file_size)
        compressed = int(entry.compress_size)
        if expanded < 0 or compressed < 0:
            raise ValueError("CSOne corpus input rejected: invalid_zip_size")
        if entry.flag_bits & 0x1:
            raise ValueError("CSOne corpus input rejected: encrypted_zip_entry")
        if expanded > limits.max_zip_entry_expanded_bytes:
            raise ValueError("CSOne corpus input rejected: zip_entry_expanded_size")
        if expanded and expanded / max(compressed, 1) > limits.max_zip_expansion_ratio:
            raise ValueError("CSOne corpus input rejected: zip_expansion_ratio")
        expanded_total += expanded
        compressed_total += compressed
        if expanded_total > limits.max_zip_total_expanded_bytes:
            raise ValueError("CSOne corpus input rejected: zip_total_expanded_size")
    if (
        expanded_total
        and expanded_total / max(compressed_total, 1)
        > limits.max_zip_expansion_ratio
    ):
        raise ValueError("CSOne corpus input rejected: zip_expansion_ratio")


def discover_corpus_workbooks(
    corpus_dir: Path,
    *,
    limits: CorpusInputLimits = DEFAULT_CORPUS_INPUT_LIMITS,
) -> list[Path]:
    resolved = corpus_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("CSOne corpus directory is not readable")
    workbooks: list[Path] = []
    aggregate_bytes = 0
    try:
        candidates = sorted(
            path
            for path in resolved.iterdir()
            if path.suffix.casefold() == ".xlsx"
            and not path.name.startswith("~$")
        )
        for path in candidates:
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise ValueError("CSOne corpus input rejected: non_regular_workbook")
            if not 1 <= metadata.st_size <= limits.max_workbook_bytes:
                raise ValueError("CSOne corpus input rejected: workbook_size")
            aggregate_bytes += int(metadata.st_size)
            if aggregate_bytes > limits.max_aggregate_bytes:
                raise ValueError("CSOne corpus input rejected: aggregate_size")
            workbooks.append(path)
            if len(workbooks) > limits.max_workbooks:
                raise ValueError("CSOne corpus input rejected: workbook_count")
    except OSError as exc:
        raise ValueError("CSOne corpus input rejected: inventory_unreadable") from exc
    if not workbooks:
        raise ValueError("CSOne corpus directory contains no xlsx workbooks")
    return workbooks


def _parse_declared_timestamp_series(value: Any) -> tuple[pd.Series, int]:
    """Parse only declared date shapes and surface nonblank failures.

    Real CSOne exports use the explicit US month/day clock below.  ISO-8601 and
    native date objects cover workbook metadata.  Avoiding pandas' inference
    removes its per-workbook warning without making malformed values disappear.
    """

    if isinstance(value, pd.Series):
        values = value.copy()
    elif isinstance(value, pd.DatetimeIndex):
        values = pd.Series(value)
    elif isinstance(value, (list, tuple)):
        values = pd.Series(list(value), dtype="object")
    else:
        values = pd.Series([value], dtype="object")

    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    nonblank = values.map(
        lambda item: not (
            item is None
            or (isinstance(item, float) and pd.isna(item))
            or str(item).strip() == ""
        )
    )
    native = values.map(lambda item: isinstance(item, (datetime, date, pd.Timestamp)))
    if native.any():
        parsed.loc[native] = pd.to_datetime(values.loc[native], errors="coerce", utc=True)

    text = values.astype(str).str.strip()
    remaining = nonblank & ~native
    us_clock = remaining & text.str.fullmatch(
        r"\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s+[APap][Mm]"
    )
    if us_clock.any():
        parsed.loc[us_clock] = pd.to_datetime(
            text.loc[us_clock],
            format="%m/%d/%Y %I:%M %p",
            errors="coerce",
            utc=True,
        )

    iso = remaining & ~us_clock & text.str.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    )
    if iso.any():
        # ISO8601 is an explicit pandas parser, not the inference path that
        # emitted thousands of warning bytes in corpus-backed acceptance.
        try:
            parsed.loc[iso] = pd.to_datetime(
                text.loc[iso], format="ISO8601", errors="coerce", utc=True
            )
        except (TypeError, ValueError):  # pandas < 2 compatibility
            def parse_iso_compat(item: Any) -> Any:
                try:
                    parsed_value = pd.Timestamp(
                        datetime.fromisoformat(str(item).replace("Z", "+00:00"))
                    )
                    return (
                        parsed_value.tz_localize("UTC")
                        if parsed_value.tzinfo is None
                        else parsed_value.tz_convert("UTC")
                    )
                except (TypeError, ValueError, OverflowError):
                    return pd.NaT

            parsed.loc[iso] = text.loc[iso].map(parse_iso_compat)

    invalid_count = int((nonblank & parsed.isna()).sum())
    return parsed, invalid_count


def _timestamp_summary(value: Any) -> tuple[Optional[int], int]:
    parsed, invalid_count = _parse_declared_timestamp_series(value)
    populated = parsed.dropna()
    if populated.empty:
        return None, invalid_count
    return int(populated.max().value), invalid_count


def _timestamp_value(value: Any) -> Optional[int]:
    return _timestamp_summary(value)[0]


def _workbook_core_timestamps(path: Path) -> dict[str, Optional[int]]:
    """Read bounded XLSX core metadata without loading workbook row values."""

    result: dict[str, Optional[int]] = {
        "workbook_modified": None,
        "workbook_created": None,
    }
    try:
        with ZipFile(path) as archive:
            info = archive.getinfo("docProps/core.xml")
            if info.file_size > 128 * 1024:
                return result
            core_xml = archive.read(info)
    except (BadZipFile, KeyError, OSError):
        return result
    for local_name in ("modified", "created"):
        match = re.search(
            rb"<(?:[A-Za-z_][\w.-]*:)?"
            + local_name.encode("ascii")
            + rb"(?:\s[^>]*)?>([^<]{1,96})</(?:[A-Za-z_][\w.-]*:)?"
            + local_name.encode("ascii")
            + rb"\s*>",
            core_xml,
            flags=re.IGNORECASE,
        )
        if match:
            result[f"workbook_{local_name}"] = _timestamp_value(
                match.group(1).decode("ascii", "ignore")
            )
    return result


def _frame_export_timestamp(frame: pd.DataFrame) -> Optional[int]:
    attrs = dict(getattr(frame, "attrs", {}) or {})
    for name in _FRAME_EXPORT_ATTRS:
        value = _timestamp_value(attrs.get(name))
        if value is not None:
            return value
    for name in _FRAME_EXPORT_COLUMNS:
        if name not in frame.columns:
            continue
        value = _timestamp_value(frame[name])
        if value is not None:
            return value
    return None


def _case_observation_timestamp(frame: pd.DataFrame) -> Optional[int]:
    values = [_timestamp_value(frame[column]) for column in DATE_COLUMNS if column in frame.columns]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _case_date_parse_failure_count(frame: pd.DataFrame) -> int:
    return sum(
        _timestamp_summary(frame[column])[1]
        for column in DATE_COLUMNS
        if column in frame.columns
    )


def _schema_sha256(frame: pd.DataFrame) -> str:
    return hashlib.sha256(
        "\n".join(map(str, frame.columns)).encode("utf-8")
    ).hexdigest()


def _profile_corpus_workbooks(
    corpus_dir: Path,
    *,
    loader: Callable[[str], pd.DataFrame],
    limits: CorpusInputLimits = DEFAULT_CORPUS_INPUT_LIMITS,
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for path in discover_corpus_workbooks(corpus_dir, limits=limits):
        _validate_xlsx_archive(path, limits)
        content_sha256 = _workbook_content_contract(
            path,
            max_bytes=limits.max_workbook_bytes,
        )["content_sha256"]
        loaded = loader(str(path))
        if not isinstance(loaded, pd.DataFrame):
            raise TypeError("production CSOne loader must return a DataFrame")
        if (
            _workbook_content_contract(
                path,
                max_bytes=limits.max_workbook_bytes,
            )["content_sha256"]
            != content_sha256
        ):
            raise ValueError("CSOne corpus source changed during profile load")
        ids = _clean_series(loaded, "SR Number")
        substantive = (
            _clean_series(loaded, "Title").ne("")
            | _clean_series(loaded, "Severity").ne("")
            | _clean_series(loaded, "Date/Time Opened").ne("")
        )
        core = _workbook_core_timestamps(path)
        profiles.append(
            {
                "path": path,
                "row_count": int(len(loaded)),
                "column_count": int(len(loaded.columns)),
                "schema_sha256": _schema_sha256(loaded),
                "content_sha256": content_sha256,
                "excluded_non_record_rows": int(
                    loaded.attrs.get("excluded_non_record_rows") or 0
                ),
                "footer_like_rows_remaining": int((ids.eq("") & ~substantive).sum()),
                "missing_record_id_rows": int(ids.eq("").sum()),
                "frame_export_metadata": _frame_export_timestamp(loaded),
                "workbook_modified": core["workbook_modified"],
                "workbook_created": core["workbook_created"],
                "case_observation_max": _case_observation_timestamp(loaded),
                "date_parse_failure_count": _case_date_parse_failure_count(loaded),
                "filesystem_mtime": int(path.stat().st_mtime_ns),
                # Filename is only a final deterministic tie-break and is
                # never retained in the sanitized coverage contract.
                "tie_breaker": path.name.casefold(),
            }
        )
        del loaded
    return profiles


def _chronology_basis(profiles: list[dict[str, Any]]) -> tuple[str, int, bool]:
    nonempty = [profile for profile in profiles if profile["row_count"] > 0]
    needed = min(len(nonempty), len(REQUIRED_REPLAY_STRATA))
    for field in (
        "frame_export_metadata",
        "workbook_modified",
        "workbook_created",
        "case_observation_max",
        "filesystem_mtime",
    ):
        values = [profile.get(field) for profile in nonempty]
        if values and all(value is not None for value in values):
            distinct = len(set(values))
            if distinct >= needed:
                return field, distinct, needed >= len(REQUIRED_REPLAY_STRATA)
    return "filename_order", 0, False


def _select_replay_profiles(
    profiles: list[dict[str, Any]],
    *,
    max_rows: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    nonempty = [profile for profile in profiles if profile["row_count"] > 0]
    basis, distinct_values, chronology_established = _chronology_basis(profiles)
    ordered = sorted(
        nonempty,
        key=lambda profile: (
            profile.get(basis) if basis != "filename_order" else 0,
            profile["tie_breaker"],
        ),
    )
    strata_by_position: dict[int, set[str]] = defaultdict(set)
    if ordered:
        position_indexes = {
            label: round((len(ordered) - 1) * position)
            for label, position in zip(REQUIRED_REPLAY_STRATA, REPRESENTATIVE_POSITIONS)
        }
        for label, index in position_indexes.items():
            strata_by_position[index].add(label)
        high_volume_index = max(
            range(len(ordered)),
            key=lambda index: (
                ordered[index]["row_count"],
                ordered[index]["column_count"],
                -index,
            ),
        )
        strata_by_position[high_volume_index].add("high_volume")

        schema_counts = Counter(profile["schema_sha256"] for profile in ordered)
        if len(schema_counts) > 1:
            median_columns = median(profile["column_count"] for profile in ordered)
            schema_edge_index = min(
                range(len(ordered)),
                key=lambda index: (
                    schema_counts[ordered[index]["schema_sha256"]],
                    -abs(ordered[index]["column_count"] - median_columns),
                    index,
                ),
            )
            strata_by_position[schema_edge_index].add("schema_edge")

    selected: list[dict[str, Any]] = []
    selected_files: list[dict[str, Any]] = []
    for position in sorted(strata_by_position):
        profile = ordered[position]
        labels = sorted(strata_by_position[position])
        profile = dict(profile)
        profile["strata"] = labels
        profile["file_id"] = f"corpus-file-{len(selected) + 1:03d}"
        selected.append(profile)
        selected_files.append(
            {
                "chronology_position": position + 1,
                "strata": labels,
                "source_row_count": profile["row_count"],
                "column_count": profile["column_count"],
                "replay_row_count": 0,
                "_file_id": profile["file_id"],
            }
        )

    chronology_files = {
        position
        for position, labels in strata_by_position.items()
        if labels & set(REQUIRED_REPLAY_STRATA)
    }
    schema_variant_count = len(
        {profile["schema_sha256"] for profile in nonempty}
    )
    breadth_reasons: list[str] = []
    if len(nonempty) < len(REQUIRED_REPLAY_STRATA):
        breadth_reasons.append("fewer_than_three_nonempty_workbooks")
    if not chronology_established:
        breadth_reasons.append("chronology_metadata_not_distinct")
    if len(chronology_files) < len(REQUIRED_REPLAY_STRATA):
        breadth_reasons.append("old_mid_new_not_distinct")
    if len(selected) > max(int(max_rows), 1):
        breadth_reasons.append("max_rows_below_selected_file_count")
    covered_strata = sorted(
        {label for labels in strata_by_position.values() for label in labels}
    )
    coverage = {
        "candidate_workbook_count": len(profiles),
        "nonempty_workbook_count": len(nonempty),
        "empty_workbook_count": len(profiles) - len(nonempty),
        "selected_workbook_count": len(selected),
        "candidate_source_row_count": sum(
            profile["row_count"] for profile in nonempty
        ),
        "selected_source_row_count": sum(
            profile["row_count"] for profile in selected
        ),
        "replay_row_count": 0,
        "chronology_basis": basis,
        "chronology_distinct_value_count": distinct_values,
        "chronology_established": chronology_established,
        "schema_variant_count": schema_variant_count,
        "required_strata": list(REQUIRED_REPLAY_STRATA),
        "covered_strata": covered_strata,
        "breadth_ok": not breadth_reasons,
        "breadth_reasons": breadth_reasons,
        "selected_files": selected_files,
        "strata": {
            label: {
                "selected_file_count": sum(
                    label in item["strata"] for item in selected_files
                ),
                "source_row_count": sum(
                    item["source_row_count"]
                    for item in selected_files
                    if label in item["strata"]
                ),
                "replay_row_count": 0,
            }
            for label in covered_strata
        },
    }
    return selected, coverage


def representative_workbooks(
    corpus_dir: Path,
    *,
    input_limits: CorpusInputLimits = DEFAULT_CORPUS_INPUT_LIMITS,
) -> list[Path]:
    workbooks = discover_corpus_workbooks(corpus_dir, limits=input_limits)
    metadata: list[dict[str, Any]] = []
    for path in workbooks:
        _validate_xlsx_archive(path, input_limits)
        core = _workbook_core_timestamps(path)
        metadata.append(
            {
                "path": path,
                "workbook_modified": core["workbook_modified"],
                "workbook_created": core["workbook_created"],
                "filesystem_mtime": int(path.stat().st_mtime_ns),
                "tie_breaker": path.name.casefold(),
            }
        )
    basis = "filesystem_mtime"
    for candidate in ("workbook_modified", "workbook_created", "filesystem_mtime"):
        values = [item.get(candidate) for item in metadata]
        if all(value is not None for value in values) and len(set(values)) >= min(
            len(metadata), len(REQUIRED_REPLAY_STRATA)
        ):
            basis = candidate
            break
    ordered = sorted(
        metadata,
        key=lambda item: (item.get(basis), item["tie_breaker"]),
    )
    indexes = {
        round((len(ordered) - 1) * position)
        for position in REPRESENTATIVE_POSITIONS
    }
    return [ordered[index]["path"] for index in sorted(indexes)]


def _clean_series(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series("", index=frame.index, dtype="object")
    return frame[name].fillna("").astype(str).str.strip()


def _classification_text(value: Any) -> str:
    """Return bounded plain text suitable only for lossy classification."""

    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null"}:
        return ""
    if len(text) > _MAX_SOURCE_CATEGORY_LENGTH:
        return ""
    if _CONTROL_CHARACTER_RE.search(text):
        return ""
    if text.lstrip().startswith(_FORMULA_PREFIXES):
        return ""
    return text


def _raw_status_missing(value: Any) -> bool:
    """Classify only true source absence before safety/canonical filtering."""

    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        return True
    text = str(value).strip()
    return not text or text.casefold() in {"nan", "none", "null"}


def _canonical_priority(value: Any) -> str:
    return normalize_priority_label(_classification_text(value))


def _canonical_status(value: Any) -> str:
    return normalize_status_label(_classification_text(value))


def _canonical_technology_value(values: Iterable[Any]) -> str:
    observed_plain_text = False
    for raw_value in values:
        text = _classification_text(raw_value)
        if not text:
            continue
        observed_plain_text = True
        folded = text.casefold()
        for label, patterns in _TECHNOLOGY_PATTERNS:
            if any(re.search(pattern, folded) for pattern in patterns):
                return label
        if "contact center" in folded:
            return "Contact Center / Unclassified"
    return "Other / Unclassified" if observed_plain_text else "Not provided"


def _canonical_technology_series(frame: pd.DataFrame) -> pd.Series:
    columns = [name for name in _TECHNOLOGY_SOURCE_COLUMNS if name in frame.columns]
    if not columns:
        return pd.Series("Not provided", index=frame.index, dtype="object")
    return frame[columns].apply(
        lambda row: _canonical_technology_value(row.tolist()),
        axis=1,
    )


def _safe_source_case_types(frame: pd.DataFrame) -> pd.Series:
    columns = [name for name in _CASE_TYPE_SOURCE_COLUMNS if name in frame.columns]
    if not columns:
        return pd.Series("unknown", index=frame.index, dtype="object")
    safe = pd.DataFrame(index=frame.index)
    for column in columns:
        safe[column] = frame[column].map(_classification_text)
    return safe.apply(classify_case_type, axis=1)


def _canonical_service_tier(value: Any) -> str:
    text = _classification_text(value)
    if not text:
        return "Not provided"
    folded = text.casefold()
    if any(token in folded for token in ("premium", "platinum", "gold")):
        return "Premium"
    if any(token in folded for token in ("enhanced", "solution support", "tier 2")):
        return "Enhanced"
    if any(token in folded for token in ("standard", "basic", "tier 1")):
        return "Standard"
    return "Other / Unclassified"


def _canonical_case_origin(value: Any) -> str:
    text = _classification_text(value)
    if not text:
        return "Not provided"
    folded = text.casefold()
    for label, tokens in (
        ("Email", ("email", "e-mail")),
        ("Phone", ("phone", "telephone", "voice")),
        ("Chat", ("chat", "messaging")),
        ("Partner", ("partner", "reseller")),
        ("Automated", ("automated", "monitoring", "system")),
        ("Web", ("web", "portal")),
    ):
        if any(token in folded for token in tokens):
            return label
    return "Other / Unclassified"


def _bounded_category_bucket(value: Any, *, namespace: str, label: str) -> str:
    """Collapse arbitrary low-entropy categories into eight non-injective bins."""

    text = _classification_text(value)
    if not text:
        return "Not provided"
    digest = hashlib.blake2b(
        f"{namespace}\0{text}".encode("utf-8", "replace"),
        digest_size=2,
        person=b"AdoptIQReplay",
    ).digest()
    bucket = int.from_bytes(digest, "big") % 8 + 1
    return f"{label} category {bucket:02d}"


def _owner_change_bucket(value: Any) -> Any:
    if isinstance(value, bool):
        return pd.NA
    text = _classification_text(value)
    if not re.fullmatch(r"\d+(?:\.0+)?", text):
        return pd.NA
    try:
        number = int(float(text))
    except (TypeError, ValueError, OverflowError):
        return pd.NA
    return min(number, 3)


def _unsafe_output_text(value: str) -> bool:
    return bool(
        len(value) > _MAX_OUTPUT_TEXT_LENGTH
        or _CONTROL_CHARACTER_RE.search(value)
        or value.lstrip().startswith(_FORMULA_PREFIXES)
    )


def _require_values(
    frame: pd.DataFrame,
    column: str,
    allowed: Iterable[str],
) -> None:
    values = set(frame[column].fillna("").astype(str))
    unexpected = values - set(allowed)
    if unexpected:
        raise ValueError(f"pseudonymous CSOne policy rejected {column}")


def _require_pattern(frame: pd.DataFrame, column: str, pattern: str) -> None:
    values = frame[column].fillna("").astype(str)
    if not values.str.fullmatch(pattern).all():
        raise ValueError(f"pseudonymous CSOne policy rejected {column}")


def validate_pseudonymous_csone_frame(
    frame: pd.DataFrame,
    *,
    as_of_utc: str,
) -> dict[str, Any]:
    """Fail closed unless every retained value satisfies the replay policy.

    The returned proof is deliberately aggregate-only: it contains no source
    path, workbook name, record identifier, column name, or cell value.
    """

    if not isinstance(frame, pd.DataFrame):
        raise ValueError("pseudonymous CSOne policy requires a DataFrame")
    if tuple(frame.columns) != _PSEUDONYMOUS_OUTPUT_COLUMNS:
        raise ValueError("pseudonymous CSOne policy rejected output schema")

    text_cells = 0
    for column in frame.columns:
        if column in DATE_COLUMNS or column == "# of Case Owner Changes":
            continue
        values = frame[column].fillna("").astype(str)
        text_cells += int(len(values))
        if values.map(_unsafe_output_text).any():
            raise ValueError("pseudonymous CSOne policy rejected unsafe output text")

    for column in ("SR Number", "Case Number", "case_id"):
        _require_pattern(frame, column, r"(?:SIM-SR-\d{6})?")
    for column in ("Transaction ID", "bemscsc_refs", "bems_ref"):
        _require_pattern(frame, column, r"(?:SIM-TXN-\d{6})?")
    _require_pattern(frame, "LOCAL_ACCEPTANCE_RECORD_ID", r"CSREAL-\d{6}")
    _require_values(frame, "ACCOUNT_ID_C", {slot[0] for slot in _SYNTHETIC_SLOTS})
    for column in ("Customer", "Customer Name: Customer Name", "customer"):
        _require_values(frame, column, {"", *(slot[1] for slot in _SYNTHETIC_SLOTS)})
    _require_values(frame, "FIXTURE_MEMBER", {slot[2] for slot in _SYNTHETIC_SLOTS})
    _require_values(
        frame,
        "Subscription Reference Id",
        {slot[3] for slot in _SYNTHETIC_SLOTS},
    )
    _require_values(
        frame,
        "Current Contact Email",
        {"", "fixture.contact1@example.invalid"},
    )
    _require_values(
        frame,
        "Case Owner: Full Name",
        {slot[2] for slot in _SYNTHETIC_SLOTS},
    )

    for column in ("Severity", "Highest Priority", "severity"):
        _require_values(frame, column, _SAFE_SEVERITIES)
    for column in ("Case Status", "status"):
        _require_values(frame, column, _SAFE_STATUSES)
    for column in (
        "Tech.",
        "Sub Technology",
        "Sub Tech.",
        "Product: Product Name",
        "Technology",
    ):
        _require_values(frame, column, _SAFE_TECHNOLOGIES)
    _require_values(frame, "Service Tier", _SAFE_SERVICE_TIERS)
    _require_values(frame, "Case Origin", _SAFE_CASE_ORIGINS)
    _require_pattern(frame, "Problem Code", r"(?:Problem category 0[1-8]|Not provided)")
    _require_pattern(
        frame,
        "Resolution Code",
        r"(?:Resolution category 0[1-8]|Not provided)",
    )

    owner_changes = frame["# of Case Owner Changes"]
    if not pd.api.types.is_integer_dtype(owner_changes.dtype):
        raise ValueError("pseudonymous CSOne policy rejected owner-change dtype")
    parsed_owner_changes = pd.to_numeric(owner_changes, errors="coerce")
    if (owner_changes.notna() & parsed_owner_changes.isna()).any():
        raise ValueError("pseudonymous CSOne policy rejected owner-change value")
    populated_owner_changes = parsed_owner_changes.dropna()
    if not populated_owner_changes.isin((0, 1, 2, 3)).all():
        raise ValueError("pseudonymous CSOne policy rejected owner-change category")

    target = pd.to_datetime(as_of_utc, errors="coerce", utc=True)
    if pd.isna(target):
        raise ValueError("pseudonymous CSOne policy requires a valid as-of")
    lower_bound = target - pd.DateOffset(years=10)
    date_cells = 0
    for column in DATE_COLUMNS:
        if not pd.api.types.is_datetime64_any_dtype(frame[column].dtype):
            raise ValueError("pseudonymous CSOne policy rejected date dtype")
        parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
        if (frame[column].notna() & parsed.isna()).any():
            raise ValueError("pseudonymous CSOne policy rejected date value")
        populated = parsed.dropna()
        date_cells += int(len(populated))
        if (populated.gt(target) | populated.lt(lower_bound)).any():
            raise ValueError("pseudonymous CSOne policy rejected date bounds")
        if any(value != value.floor("D") for value in populated):
            raise ValueError("pseudonymous CSOne policy rejected date precision")

    case_type_text = frame.apply(classify_case_type, axis=1).map(
        {
            "provisioning_request": "provisioning request",
            "break_fix_technical": "break-fix technical incident",
            "unknown": "standard support inquiry",
        }
    )
    if not set(case_type_text).issubset(_SAFE_CASE_TYPE_TEXT):
        raise ValueError("pseudonymous CSOne policy rejected case-type projection")
    expected_titles = pd.Series(
        [
            f"Pseudonymized {tech} {case_type} ({severity})"
            for tech, case_type, severity in zip(
                frame["Technology"],
                case_type_text,
                frame["Severity"],
            )
        ],
        index=frame.index,
        dtype="object",
    )
    if not frame["Title"].astype(str).equals(expected_titles):
        raise ValueError("pseudonymous CSOne policy rejected title projection")
    for column, replacement in _TEXT_PRESENCE_REPLACEMENTS.items():
        _require_values(frame, column, {"", replacement})

    for aliases in (
        ("SR Number", "Case Number", "case_id"),
        ("Transaction ID", "bemscsc_refs", "bems_ref"),
        ("Customer", "Customer Name: Customer Name", "customer"),
        ("Severity", "severity"),
        ("Case Status", "status"),
        (
            "Tech.",
            "Sub Technology",
            "Sub Tech.",
            "Product: Product Name",
            "Technology",
        ),
        ("FIXTURE_MEMBER", "Case Owner: Full Name"),
    ):
        first = frame[aliases[0]].reset_index(drop=True)
        if any(
            not first.equals(frame[column].reset_index(drop=True))
            for column in aliases[1:]
        ):
            raise ValueError("pseudonymous CSOne policy rejected alias coherence")

    bucketed_cells = int(
        frame["Problem Code"].ne("Not provided").sum()
        + frame["Resolution Code"].ne("Not provided").sum()
    )
    return {
        "schema_version": PSEUDONYMOUS_POLICY_VERSION,
        "validated": True,
        "raw_values_retained": False,
        "row_count": int(len(frame)),
        "column_count": int(len(frame.columns)),
        "text_cell_count": text_cells,
        "date_cell_count": date_cells,
        "bucketed_cell_count": bucketed_cells,
    }


def _source_key(frame: pd.DataFrame) -> pd.Series:
    candidates = (
        "SR Number",
        "Case Number",
        "case_id",
        "Transaction ID",
    )
    result = pd.Series("", index=frame.index, dtype="object")
    for column in candidates:
        values = _clean_series(frame, column)
        result = result.where(result.ne(""), values)
    fallback = pd.Series(
        [f"row-{index}" for index in range(len(frame))],
        index=frame.index,
        dtype="object",
    )
    return result.where(result.ne(""), fallback)


def _stable_order(frame: pd.DataFrame) -> pd.Series:
    keys = _source_key(frame)
    return keys.map(
        lambda value: hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()
    )


def _stratified_limit(frame: pd.DataFrame, max_rows: int) -> pd.DataFrame:
    limit = max(int(max_rows), 1)
    if len(frame) <= limit:
        return frame.copy()
    # Keep only a skinny positional selection table while the full loader
    # frame is resident. Copying every object column here multiplied memory on
    # the largest real exports before any row had been discarded.
    severity = _clean_series(frame, "Severity").str.casefold().replace("", "missing")
    status = _clean_series(frame, "Case Status").str.casefold()
    status = status.where(status.ne(""), "missing")
    technology = _clean_series(frame, "Tech.").str.casefold().replace("", "missing")
    id_state = _clean_series(frame, "SR Number").ne("").map(
        {True: "identified", False: "missing_id"}
    )
    file_id = _clean_series(frame, _REPLAY_FILE_ID_COLUMN).replace(
        "", "single-source"
    )
    selection = pd.DataFrame(
        {
            "__source_position": range(len(frame)),
            "__replay_order": _stable_order(frame).to_numpy(),
            "__replay_file_id": file_id.to_numpy(),
            "__replay_stratum": (
                file_id
                + "|"
                + severity
                + "|"
                + status
                + "|"
                + technology
                + "|"
                + id_state
            ).to_numpy(),
        }
    )
    groups = list(selection.groupby("__replay_stratum", sort=True, dropna=False))
    group_by_key = {str(key): group for key, group in groups}
    allocations = {key: 0 for key in group_by_key}

    # Reserve one row per selected corpus file before distributing by case
    # shape.  This is what prevents a small or schema-edge workbook from being
    # crowded out by the highest-volume export under a bounded max_rows.
    groups_by_file: dict[str, list[str]] = defaultdict(list)
    for key, group in groups:
        source_file_id = str(group["__replay_file_id"].iloc[0] or "single-source")
        groups_by_file[source_file_id].append(str(key))
    for file_id in sorted(groups_by_file):
        if sum(allocations.values()) >= limit:
            break
        candidate = sorted(
            groups_by_file[file_id],
            key=lambda key: (-len(group_by_key[key]), key),
        )[0]
        allocations[candidate] = 1

    # Give uncovered category strata one row each, rarest first. If max_rows
    # is smaller than the stratum count this remains deterministic and never
    # exceeds the operator's explicit cap.
    uncovered = sorted(
        (key for key, value in allocations.items() if value == 0),
        key=lambda key: (len(group_by_key[key]), key),
    )
    for key in uncovered:
        if sum(allocations.values()) >= limit:
            break
        allocations[key] = 1

    # Distribute remaining capacity proportionally using the largest current
    # size-per-slot deficit.  Stable key ordering resolves exact ties.
    while sum(allocations.values()) < limit:
        candidates = [
            key
            for key, group in group_by_key.items()
            if allocations[key] < len(group)
        ]
        if not candidates:
            break
        candidate = sorted(
            candidates,
            key=lambda key: (
                -(len(group_by_key[key]) / (allocations[key] + 1)),
                key,
            ),
        )[0]
        allocations[candidate] += 1
    selected_positions = [
        group.sort_values("__replay_order", kind="stable").head(
            allocations.get(str(key), 0)
        )[["__source_position", "__replay_order"]]
        for key, group in groups
    ]
    ordered_positions = (
        pd.concat(selected_positions, ignore_index=True)
        .sort_values("__replay_order", kind="stable")
        .head(limit)
        ["__source_position"]
        .astype(int)
        .tolist()
    )
    sampled = frame.iloc[ordered_positions].copy().reset_index(drop=True)
    sampled.attrs.update(dict(getattr(frame, "attrs", {}) or {}))
    return sampled


def _enumerated_ids(values: pd.Series, prefix: str) -> pd.Series:
    cleaned = values.fillna("").astype(str).str.strip()
    unique = sorted(
        set(cleaned.loc[cleaned.ne("")]),
        key=lambda value: hashlib.sha256(value.encode("utf-8", "replace")).hexdigest(),
    )
    mapping = {value: f"{prefix}-{index:06d}" for index, value in enumerate(unique, 1)}
    return cleaned.map(mapping).fillna("")


def _shift_dates(
    source: pd.DataFrame,
    target_as_of_utc: str,
) -> dict[str, pd.Series]:
    parsed: dict[str, pd.Series] = {
        column: _parse_declared_timestamp_series(source[column])[0]
        for column in DATE_COLUMNS
        if column in source.columns
    }
    maxima = [values.max() for values in parsed.values() if values.notna().any()]
    target = pd.to_datetime(target_as_of_utc, errors="raise", utc=True)
    shift = target - max(maxima) if maxima else pd.Timedelta(0)
    lower_bound = target - pd.DateOffset(years=10)
    return {
        column: (values + shift)
        .dt.floor("D")
        .where(lambda shifted: shifted.between(lower_bound, target))
        for column, values in parsed.items()
    }


def _combined_replay_source(
    selected: list[dict[str, Any]],
    coverage: Mapping[str, Any],
    *,
    loader: Callable[[str], pd.DataFrame],
    max_rows: int,
    limits: CorpusInputLimits = DEFAULT_CORPUS_INPUT_LIMITS,
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    excluded_rows = 0
    for profile in selected:
        _validate_xlsx_archive(profile["path"], limits)
        reload_content_sha256 = _workbook_content_contract(
            profile["path"],
            max_bytes=limits.max_workbook_bytes,
        )["content_sha256"]
        if reload_content_sha256 != profile.get("content_sha256"):
            raise ValueError(
                "selected CSOne replay workbook content changed before reload"
            )
        loaded = loader(str(profile["path"]))
        if not isinstance(loaded, pd.DataFrame) or loaded.empty:
            raise ValueError("selected CSOne replay workbook did not reload nonempty")
        if (
            _workbook_content_contract(
                profile["path"],
                max_bytes=limits.max_workbook_bytes,
            )["content_sha256"]
            != reload_content_sha256
        ):
            raise ValueError("selected CSOne replay workbook changed during reload")
        if (
            len(loaded) != profile["row_count"]
            or _schema_sha256(loaded) != profile["schema_sha256"]
        ):
            raise ValueError("selected CSOne replay workbook changed during profiling")
        frame = loaded
        frame[_REPLAY_FILE_ID_COLUMN] = profile["file_id"]
        frame[_REPLAY_STRATA_COLUMN] = ";".join(profile["strata"])
        # Bound retained memory before loading the next workbook. The final
        # cross-file sampler below still applies the operator's global cap.
        parts.append(_stratified_limit(frame, max_rows))
        excluded_rows += int(profile.get("excluded_non_record_rows") or 0)
        del frame
        del loaded
    if not parts:
        raise ValueError("CSOne corpus contains no nonempty production-loader frames")
    combined = pd.concat(parts, ignore_index=True, sort=False)
    combined.attrs["excluded_non_record_rows"] = excluded_rows
    combined.attrs["corpus_replay_coverage"] = dict(coverage)
    return combined


def _sampled_corpus_coverage(
    sampled: pd.DataFrame,
    source_coverage: Mapping[str, Any],
) -> dict[str, Any]:
    coverage = dict(source_coverage or {})
    selected_files = [dict(item) for item in coverage.get("selected_files") or []]
    file_counts = (
        _clean_series(sampled, _REPLAY_FILE_ID_COLUMN).value_counts().to_dict()
        if _REPLAY_FILE_ID_COLUMN in sampled.columns
        else {}
    )
    for item in selected_files:
        item["replay_row_count"] = int(file_counts.get(item.get("_file_id"), 0))
    files_with_rows = sum(item["replay_row_count"] > 0 for item in selected_files)

    replay_rows_by_stratum: dict[str, int] = defaultdict(int)
    if _REPLAY_STRATA_COLUMN in sampled.columns:
        for value in _clean_series(sampled, _REPLAY_STRATA_COLUMN):
            for label in {part for part in value.split(";") if part}:
                replay_rows_by_stratum[label] += 1
    strata = {
        label: {
            **dict(values),
            "replay_row_count": int(replay_rows_by_stratum.get(label, 0)),
        }
        for label, values in (coverage.get("strata") or {}).items()
    }
    post_sample_reasons = list(coverage.get("breadth_reasons") or [])
    if files_with_rows < len(selected_files):
        post_sample_reasons.append("selected_workbook_missing_from_replay")
    missing_required = [
        label
        for label in REQUIRED_REPLAY_STRATA
        if int((strata.get(label) or {}).get("replay_row_count") or 0) <= 0
    ]
    if missing_required:
        post_sample_reasons.append("required_stratum_missing_from_replay")
    for item in selected_files:
        item.pop("_file_id", None)
    coverage.update(
        {
            "replay_row_count": int(len(sampled)),
            "selected_files_with_replay_rows": files_with_rows,
            "breadth_ok": not post_sample_reasons,
            "breadth_reasons": sorted(set(post_sample_reasons)),
            "selected_files": selected_files,
            "strata": strata,
        }
    )
    return coverage


def pseudonymize_csone_frame(
    source: pd.DataFrame,
    bundle: LocalAcceptanceBundle,
    *,
    max_rows: int = 600,
) -> pd.DataFrame:
    """Return an allow-listed, deterministic case frame with no raw identity."""

    if not isinstance(source, pd.DataFrame) or source.empty:
        raise ValueError("production CSOne loader returned no records")
    sampled = _stratified_limit(source, max_rows)
    raw_corpus_coverage = source.attrs.get("corpus_replay_coverage")
    corpus_coverage = (
        _sampled_corpus_coverage(sampled, raw_corpus_coverage)
        if isinstance(raw_corpus_coverage, Mapping)
        else None
    )
    # Derive this while the production evidence fields still exist, then
    # discard every raw narrative field.  Only the three-valued, non-sensitive
    # classification survives.  The pseudonymous title below encodes that
    # class so the normal production lifecycle normalizer must independently
    # reproduce the same result during report generation.
    source_case_types = _safe_source_case_types(sampled)
    source_keys = _source_key(sampled)
    order_hash = source_keys.map(
        lambda value: int(
            hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:12],
            16,
        )
    )
    slot_rows = [
        _SYNTHETIC_SLOTS[value % len(_SYNTHETIC_SLOTS)] for value in order_hash
    ]
    customer_missing = _clean_series(sampled, "Customer Name: Customer Name").eq("")

    result = pd.DataFrame(index=sampled.index)
    result["SR Number"] = _enumerated_ids(
        _clean_series(sampled, "SR Number").where(
            _clean_series(sampled, "SR Number").ne(""),
            _clean_series(sampled, "Case Number"),
        ),
        "SIM-SR",
    )
    result["Case Number"] = result["SR Number"]
    result["case_id"] = result["SR Number"]
    result["ACCOUNT_ID_C"] = [slot[0] for slot in slot_rows]
    mapped_customer = pd.Series([slot[1] for slot in slot_rows], index=result.index)
    mapped_customer = mapped_customer.where(~customer_missing, "")
    for column in ("Customer", "Customer Name: Customer Name", "customer"):
        result[column] = mapped_customer
    result["FIXTURE_MEMBER"] = [slot[2] for slot in slot_rows]
    result["Subscription Reference Id"] = [slot[3] for slot in slot_rows]

    transaction = _enumerated_ids(
        _clean_series(sampled, "Transaction ID"), "SIM-TXN"
    )
    result["Transaction ID"] = transaction
    result["bemscsc_refs"] = transaction
    result["bems_ref"] = transaction

    result["Severity"] = sampled.get(
        "Severity", pd.Series("", index=sampled.index, dtype="object")
    ).map(_canonical_priority)
    source_status_values = sampled.get(
        "Case Status", pd.Series("", index=sampled.index, dtype="object")
    )
    raw_status_missing = source_status_values.map(_raw_status_missing)
    result["Case Status"] = source_status_values.map(_canonical_status)
    technology = _canonical_technology_series(sampled)
    result["Tech."] = technology
    result["Sub Technology"] = technology
    result["Sub Tech."] = technology
    result["Service Tier"] = sampled.get(
        "Service Tier", pd.Series("", index=sampled.index, dtype="object")
    ).map(_canonical_service_tier)
    result["Product: Product Name"] = technology
    result["Highest Priority"] = sampled.get(
        "Highest Priority", pd.Series("", index=sampled.index, dtype="object")
    ).map(_canonical_priority)
    result["Problem Code"] = sampled.get(
        "Problem Code", pd.Series("", index=sampled.index, dtype="object")
    ).map(
        lambda value: _bounded_category_bucket(
            value,
            namespace="problem-code",
            label="Problem",
        )
    )
    result["Resolution Code"] = sampled.get(
        "Resolution Code", pd.Series("", index=sampled.index, dtype="object")
    ).map(
        lambda value: _bounded_category_bucket(
            value,
            namespace="resolution-code",
            label="Resolution",
        )
    )
    result["Case Origin"] = sampled.get(
        "Case Origin", pd.Series("", index=sampled.index, dtype="object")
    ).map(_canonical_case_origin)
    result["# of Case Owner Changes"] = sampled.get(
        "# of Case Owner Changes",
        pd.Series(pd.NA, index=sampled.index, dtype="object"),
    ).map(_owner_change_bucket).astype("Int64")
    result["Technology"] = technology
    result["severity"] = result["Severity"]
    result["status"] = result["Case Status"]

    shifted = _shift_dates(sampled, bundle.as_of_utc)
    for column in DATE_COLUMNS:
        result[column] = shifted.get(
            column, pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]")
        )

    safe_case_type_text = source_case_types.map(
        {
            "provisioning_request": "provisioning request",
            "break_fix_technical": "break-fix technical incident",
            "unknown": "standard support inquiry",
        }
    ).fillna("standard support inquiry")
    # Keep this local pre-build path usable on the macOS system Python 3.9 as
    # well as the supported build Python 3.11+.  ``zip(strict=True)`` is 3.10+
    # only; the explicit invariant preserves its fail-loud behavior without
    # silently truncating a mismatched replay projection.
    if not (
        len(result["Technology"])
        == len(safe_case_type_text)
        == len(result["Severity"])
    ):
        raise ValueError("CSOne replay classification columns do not align")
    result["Title"] = [
        f"Pseudonymized {tech} {case_type} ({sev})"
        for tech, case_type, sev in zip(
            result["Technology"],
            safe_case_type_text,
            result["Severity"],
        )
    ]
    for column, replacement_text in _TEXT_PRESENCE_REPLACEMENTS.items():
        present = _clean_series(sampled, column).ne("")
        result[column] = present.map({True: replacement_text, False: ""})
    result["Current Contact Email"] = [
        "fixture.contact1@example.invalid" if index % 2 == 0 else ""
        for index in range(len(result))
    ]
    result["Case Owner: Full Name"] = result["FIXTURE_MEMBER"]
    result["LOCAL_ACCEPTANCE_RECORD_ID"] = [
        f"CSREAL-{index:06d}" for index in range(1, len(result) + 1)
    ]
    result = result.reset_index(drop=True)
    replay_case_types = result.apply(classify_case_type, axis=1)
    expected_case_types = source_case_types.reset_index(drop=True)
    if not replay_case_types.equals(expected_case_types):
        raise ValueError(
            "pseudonymous CSOne replay does not preserve production case-type classification"
        )
    privacy_contract = validate_pseudonymous_csone_frame(
        result,
        as_of_utc=bundle.as_of_utc,
    )
    case_type_distribution = {
        str(label): int(count)
        for label, count in expected_case_types.value_counts().sort_index().items()
    }
    status_coverage = _build_status_coverage(
        raw_status_missing.reset_index(drop=True),
        result["Case Status"],
    )
    result.attrs.update(
        {
            "sanitized": True,
            "source_mode": SOURCE_MODE,
            "source_state": "available",
            "source_dataset": "tac_cases",
            "data_as_of_utc": bundle.as_of_utc,
            "live_validation_performed": False,
            "primary_key": "SR Number",
            "corpus_replay": True,
            "raw_values_retained": privacy_contract["raw_values_retained"],
            "privacy_contract": privacy_contract,
            "source_row_count": int(
                (corpus_coverage or {}).get("selected_source_row_count")
                or len(source)
            ),
            "replay_row_count": int(len(result)),
            "excluded_non_record_rows": int(
                source.attrs.get("excluded_non_record_rows") or 0
            ),
            "case_type_distribution": case_type_distribution,
            "case_type_distribution_reconciled": True,
            **_status_source_attrs(status_coverage),
        }
    )
    if corpus_coverage is not None:
        result.attrs["corpus_replay_coverage"] = corpus_coverage
    return result


def _canonical_count(frame: pd.DataFrame, primary_key: str) -> int:
    keys = frame[primary_key].fillna("").astype(str).str.strip()
    return int(keys.loc[keys.ne("")].nunique()) + int(keys.eq("").sum())


def _warnings(frame: pd.DataFrame, primary_key: str) -> tuple[str, ...]:
    keys = frame[primary_key].fillna("").astype(str).str.strip()
    warnings: list[str] = []
    if keys.loc[keys.ne("")].duplicated().any():
        warnings.append("duplicate_source_id")
    if keys.eq("").any():
        warnings.append("missing_source_id")
    return tuple(warnings)


def _transport_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.loc[:, list(_PSEUDONYMOUS_OUTPUT_COLUMNS)].itertuples(
        index=False, name=None
    ):
        record: dict[str, Any] = {}
        for column, value in zip(_PSEUDONYMOUS_OUTPUT_COLUMNS, row):
            if column in DATE_COLUMNS:
                parsed = pd.to_datetime(value, errors="coerce", utc=True)
                record[column] = (
                    None
                    if pd.isna(parsed)
                    else parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
                )
            elif column == "# of Case Owner Changes":
                record[column] = None if pd.isna(value) else int(value)
            else:
                record[column] = "" if pd.isna(value) else str(value)
        records.append(record)
    return records


def prepared_frame_sha256(frame: pd.DataFrame) -> str:
    """Fingerprint the exact pseudonymous TAC transport projection."""

    if not isinstance(frame, pd.DataFrame):
        raise ValueError("prepared replay frame fingerprint requires a DataFrame")
    if any(column not in frame.columns for column in _PSEUDONYMOUS_OUTPUT_COLUMNS):
        raise ValueError("prepared replay frame fingerprint schema is incomplete")
    return _sha256_json(_transport_records(frame))


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("prepared CSOne replay contains duplicate JSON keys")
        result[key] = value
    return result


_PREPARED_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "sanitized",
        "privacy_policy_version",
        "manifest",
        "clock",
        "max_rows",
        "source_snapshot",
        "loader_contract",
        "coverage",
        "instrumentation",
        "tac",
        "hashes",
    }
)
_PREPARED_LOADER_KEYS = frozenset(
    {
        "candidate_workbook_count",
        "representative_workbook_count",
        "all_nonempty",
        "no_footer_rows_remaining",
        "consistent_schema",
        "chronology_basis",
        "chronology_distinct_value_count",
        "chronology_established",
        "schema_variant_count",
        "covered_strata",
        "breadth_ok",
        "breadth_reasons",
        "date_parse_failure_count",
        "results",
    }
)
_PREPARED_COVERAGE_KEYS = frozenset(
    {
        "candidate_workbook_count",
        "nonempty_workbook_count",
        "empty_workbook_count",
        "candidate_source_row_count",
        "selected_workbook_count",
        "selected_source_row_count",
        "replay_row_count",
        "schema_variant_count",
        "chronology_basis",
        "chronology_distinct_value_count",
        "chronology_established",
        "required_strata",
        "covered_strata",
        "strata",
        "selected_files",
        "selected_files_with_replay_rows",
        "breadth_ok",
        "breadth_reasons",
        "strict_breadth",
    }
)
_PREPARED_LOADER_RESULT_KEYS = frozenset(
    {
        "strata",
        "row_count",
        "column_count",
        "excluded_non_record_rows",
        "footer_like_rows_remaining",
        "missing_record_id_rows",
        "date_parse_failure_count",
        "schema_sha256",
    }
)
_PREPARED_SELECTED_FILE_KEYS = frozenset(
    {
        "chronology_position",
        "strata",
        "source_row_count",
        "column_count",
        "replay_row_count",
    }
)
_PREPARED_STRATUM_KEYS = frozenset(
    {"selected_file_count", "source_row_count", "replay_row_count"}
)
_ALLOWED_REPLAY_STRATA = frozenset(
    {*REQUIRED_REPLAY_STRATA, "high_volume", "schema_edge"}
)
_ALLOWED_CHRONOLOGY_BASES = frozenset(
    {
        "frame_export_metadata",
        "workbook_modified",
        "workbook_created",
        "case_observation_max",
        "filesystem_mtime",
        "filename_order",
    }
)
_ALLOWED_BREADTH_REASONS = frozenset(
    {
        "fewer_than_three_nonempty_workbooks",
        "chronology_metadata_not_distinct",
        "old_mid_new_not_distinct",
        "max_rows_below_selected_file_count",
        "selected_workbook_missing_from_replay",
        "required_stratum_missing_from_replay",
    }
)
_MAX_CONTRACT_COUNT = (1 << 53) - 1


def _bounded_contract_int(
    value: Any,
    *,
    minimum: int = 0,
    maximum: int = _MAX_CONTRACT_COUNT,
) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _enum_list_contract(
    value: Any,
    *,
    allowed: frozenset[str],
    allow_empty: bool = True,
) -> bool:
    return bool(
        isinstance(value, list)
        and (allow_empty or value)
        and all(isinstance(item, str) and item in allowed for item in value)
        and len(value) == len(set(value))
    )


def _validate_prepared_loader_coverage_contract(
    loader: Mapping[str, Any],
    coverage: Mapping[str, Any],
    *,
    replay_row_count: int,
) -> None:
    """Reject every non-aggregate or unbounded nested replay field."""

    candidate_count = loader.get("candidate_workbook_count")
    representative_count = loader.get("representative_workbook_count")
    if not _bounded_contract_int(candidate_count, minimum=1, maximum=10_000):
        raise ValueError("prepared CSOne replay loader count is invalid")
    if not _bounded_contract_int(
        representative_count,
        minimum=1,
        maximum=len(_ALLOWED_REPLAY_STRATA),
    ):
        raise ValueError("prepared CSOne replay representative count is invalid")
    if not _bounded_contract_int(
        loader.get("chronology_distinct_value_count"),
        maximum=candidate_count,
    ) or not _bounded_contract_int(
        loader.get("schema_variant_count"),
        minimum=1,
        maximum=candidate_count,
    ):
        raise ValueError("prepared CSOne replay loader aggregate is invalid")
    if loader.get("chronology_basis") not in _ALLOWED_CHRONOLOGY_BASES:
        raise ValueError("prepared CSOne replay chronology basis is invalid")
    if type(loader.get("chronology_established")) is not bool:
        raise ValueError("prepared CSOne replay chronology flag is invalid")
    if not _enum_list_contract(
        loader.get("covered_strata"),
        allowed=_ALLOWED_REPLAY_STRATA,
        allow_empty=False,
    ) or not _enum_list_contract(
        loader.get("breadth_reasons"),
        allowed=_ALLOWED_BREADTH_REASONS,
    ):
        raise ValueError("prepared CSOne replay loader enum is invalid")
    if loader.get("breadth_ok") is not (not loader["breadth_reasons"]):
        raise ValueError("prepared CSOne replay loader breadth is inconsistent")
    if loader.get("date_parse_failure_count") != 0:
        raise ValueError("prepared CSOne replay contains unparseable declared dates")

    loader_results = loader.get("results")
    if not isinstance(loader_results, list) or len(loader_results) != representative_count:
        raise ValueError("prepared CSOne replay loader result inventory is invalid")
    for result in loader_results:
        if not isinstance(result, dict) or set(result) != _PREPARED_LOADER_RESULT_KEYS:
            raise ValueError("prepared CSOne replay loader result schema is invalid")
        if not _enum_list_contract(
            result.get("strata"),
            allowed=_ALLOWED_REPLAY_STRATA,
            allow_empty=False,
        ):
            raise ValueError("prepared CSOne replay loader result strata are invalid")
        if not all(
            _bounded_contract_int(result.get(key), minimum=minimum)
            for key, minimum in (
                ("row_count", 1),
                ("column_count", 1),
                ("excluded_non_record_rows", 0),
                ("footer_like_rows_remaining", 0),
                ("missing_record_id_rows", 0),
                ("date_parse_failure_count", 0),
            )
        ):
            raise ValueError("prepared CSOne replay loader result count is invalid")
        if (
            result["date_parse_failure_count"] != 0
            or result["footer_like_rows_remaining"] > result["row_count"]
            or result["missing_record_id_rows"] > result["row_count"]
            or not isinstance(result.get("schema_sha256"), str)
            or _SHA256_RE.fullmatch(result["schema_sha256"]) is None
        ):
            raise ValueError("prepared CSOne replay loader result is invalid")

    coverage_count_keys = (
        "candidate_workbook_count",
        "nonempty_workbook_count",
        "empty_workbook_count",
        "candidate_source_row_count",
        "selected_workbook_count",
        "selected_source_row_count",
        "replay_row_count",
        "schema_variant_count",
        "chronology_distinct_value_count",
        "selected_files_with_replay_rows",
    )
    if any(
        not _bounded_contract_int(coverage.get(key)) for key in coverage_count_keys
    ):
        raise ValueError("prepared CSOne replay coverage aggregate is invalid")
    for key in ("chronology_established", "breadth_ok", "strict_breadth"):
        if type(coverage.get(key)) is not bool:
            raise ValueError("prepared CSOne replay coverage flag is invalid")
    if coverage.get("chronology_basis") not in _ALLOWED_CHRONOLOGY_BASES:
        raise ValueError("prepared CSOne replay coverage chronology is invalid")
    if coverage.get("required_strata") != list(REQUIRED_REPLAY_STRATA):
        raise ValueError("prepared CSOne replay required strata are invalid")
    if not _enum_list_contract(
        coverage.get("covered_strata"),
        allowed=_ALLOWED_REPLAY_STRATA,
        allow_empty=False,
    ) or not _enum_list_contract(
        coverage.get("breadth_reasons"),
        allowed=_ALLOWED_BREADTH_REASONS,
    ):
        raise ValueError("prepared CSOne replay coverage enum is invalid")
    if coverage.get("breadth_ok") is not (not coverage["breadth_reasons"]):
        raise ValueError("prepared CSOne replay coverage breadth is inconsistent")

    selected_files = coverage.get("selected_files")
    if not isinstance(selected_files, list) or len(selected_files) != representative_count:
        raise ValueError("prepared CSOne replay selected-file inventory is invalid")
    positions: list[int] = []
    for selected, result in zip(selected_files, loader_results):
        if not isinstance(selected, dict) or set(selected) != _PREPARED_SELECTED_FILE_KEYS:
            raise ValueError("prepared CSOne replay selected-file schema is invalid")
        if not _enum_list_contract(
            selected.get("strata"),
            allowed=_ALLOWED_REPLAY_STRATA,
            allow_empty=False,
        ) or selected["strata"] != result["strata"]:
            raise ValueError("prepared CSOne replay selected-file strata are invalid")
        if not all(
            _bounded_contract_int(selected.get(key), minimum=minimum)
            for key, minimum in (
                ("chronology_position", 1),
                ("source_row_count", 1),
                ("column_count", 1),
                ("replay_row_count", 0),
            )
        ):
            raise ValueError("prepared CSOne replay selected-file count is invalid")
        if (
            selected["chronology_position"] > candidate_count
            or selected["source_row_count"] != result["row_count"]
            or selected["column_count"] != result["column_count"]
            or selected["replay_row_count"] > selected["source_row_count"]
        ):
            raise ValueError("prepared CSOne replay selected-file contract is invalid")
        positions.append(selected["chronology_position"])
    if positions != sorted(set(positions)):
        raise ValueError("prepared CSOne replay selected-file positions are invalid")

    covered_strata = coverage["covered_strata"]
    strata = coverage.get("strata")
    if not isinstance(strata, dict) or set(strata) != set(covered_strata):
        raise ValueError("prepared CSOne replay stratum inventory is invalid")
    for label, contract in strata.items():
        if (
            label not in _ALLOWED_REPLAY_STRATA
            or not isinstance(contract, dict)
            or set(contract) != _PREPARED_STRATUM_KEYS
            or any(
                not _bounded_contract_int(contract.get(key))
                for key in _PREPARED_STRATUM_KEYS
            )
        ):
            raise ValueError("prepared CSOne replay stratum contract is invalid")
        matching = [item for item in selected_files if label in item["strata"]]
        if (
            contract["selected_file_count"] != len(matching)
            or contract["source_row_count"]
            != sum(item["source_row_count"] for item in matching)
            or contract["replay_row_count"]
            != sum(item["replay_row_count"] for item in matching)
        ):
            raise ValueError("prepared CSOne replay stratum counts do not reconcile")

    if (
        coverage["candidate_workbook_count"] != candidate_count
        or coverage["nonempty_workbook_count"] + coverage["empty_workbook_count"]
        != candidate_count
        or coverage["selected_workbook_count"] != representative_count
        or coverage["selected_workbook_count"] > coverage["nonempty_workbook_count"]
        or coverage["selected_source_row_count"]
        != sum(item["source_row_count"] for item in selected_files)
        or coverage["candidate_source_row_count"]
        < coverage["selected_source_row_count"]
        or coverage["replay_row_count"]
        != sum(item["replay_row_count"] for item in selected_files)
        or coverage["replay_row_count"] != replay_row_count
        or coverage["selected_files_with_replay_rows"]
        != sum(item["replay_row_count"] > 0 for item in selected_files)
        or coverage["schema_variant_count"] != loader["schema_variant_count"]
        or coverage["chronology_basis"] != loader["chronology_basis"]
        or coverage["chronology_distinct_value_count"]
        != loader["chronology_distinct_value_count"]
        or coverage["chronology_established"] != loader["chronology_established"]
        or coverage["covered_strata"] != loader["covered_strata"]
        or not set(loader["breadth_reasons"]).issubset(coverage["breadth_reasons"])
    ):
        raise ValueError("prepared CSOne replay coverage counts do not reconcile")


def _decode_prepared_payload(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_PREPARED_REPLAY_BYTES:
        raise ValueError("prepared CSOne replay byte length is out of bounds")
    try:
        decoded = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("prepared CSOne replay JSON is malformed") from exc
    if not isinstance(decoded, dict) or set(decoded) != _PREPARED_ROOT_KEYS:
        raise ValueError("prepared CSOne replay root inventory is invalid")
    if decoded.get("schema_version") != PREPARED_REPLAY_SCHEMA_VERSION:
        raise ValueError("prepared CSOne replay schema version is invalid")
    if decoded.get("sanitized") is not True:
        raise ValueError("prepared CSOne replay must declare sanitized=true")
    if decoded.get("privacy_policy_version") != PSEUDONYMOUS_POLICY_VERSION:
        raise ValueError("prepared CSOne replay privacy policy is invalid")
    if _canonical_json_bytes(decoded) != payload:
        raise ValueError("prepared CSOne replay JSON is not canonical")
    return decoded


def _validated_prepared_frames(
    prepared: PreparedCsoneReplay,
    *,
    expected_as_of_utc: str,
    expected_manifest_sha256: str,
    expected_max_rows: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    expected_payload_sha = _require_sha256(prepared.payload_sha256, "payload SHA-256")
    actual_payload_sha = hashlib.sha256(prepared.payload).hexdigest()
    if actual_payload_sha != expected_payload_sha:
        raise ValueError("prepared CSOne replay payload SHA-256 mismatch")
    root = _decode_prepared_payload(prepared.payload)
    manifest = root.get("manifest")
    clock = root.get("clock")
    source_snapshot = root.get("source_snapshot")
    instrumentation = root.get("instrumentation")
    tac_contract = root.get("tac")
    hashes = root.get("hashes")
    if not all(
        isinstance(value, dict)
        for value in (manifest, clock, source_snapshot, instrumentation, tac_contract, hashes)
    ):
        raise ValueError("prepared CSOne replay object contract is invalid")
    if set(manifest) != {"schema_version", "schema_fingerprint", "sha256"}:
        raise ValueError("prepared CSOne replay manifest inventory is invalid")
    manifest_sha = _require_sha256(manifest.get("sha256"), "manifest SHA-256")
    if manifest_sha != _require_sha256(expected_manifest_sha256, "expected manifest SHA-256"):
        raise ValueError("prepared CSOne replay manifest identity mismatch")
    if manifest_sha != _require_sha256(
        prepared.manifest_sha256, "prepared manifest SHA-256"
    ):
        raise ValueError("prepared CSOne replay object manifest identity mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("prepared CSOne replay manifest schema is invalid")
    _require_sha256(
        manifest.get("schema_fingerprint"), "manifest schema fingerprint"
    )
    if set(clock) != {"as_of_utc"} or str(clock.get("as_of_utc") or "") != str(
        expected_as_of_utc
    ):
        raise ValueError("prepared CSOne replay clock mismatch")
    if prepared.as_of_utc != str(clock.get("as_of_utc") or ""):
        raise ValueError("prepared CSOne replay object clock mismatch")
    if type(root.get("max_rows")) is not int or root["max_rows"] != int(expected_max_rows):
        raise ValueError("prepared CSOne replay max_rows mismatch")
    if prepared.max_rows != root["max_rows"]:
        raise ValueError("prepared CSOne replay object max_rows mismatch")
    if set(source_snapshot) != {
        "before_sha256",
        "after_sha256",
        "workbook_count",
        "total_bytes",
    }:
        raise ValueError("prepared CSOne replay source snapshot inventory is invalid")
    if (
        type(source_snapshot.get("workbook_count")) is not int
        or source_snapshot["workbook_count"] < 1
        or type(source_snapshot.get("total_bytes")) is not int
        or source_snapshot["total_bytes"] < 0
    ):
        raise ValueError("prepared CSOne replay source snapshot count is invalid")
    source_before_sha = _require_sha256(
        source_snapshot.get("before_sha256"), "source before-snapshot SHA-256"
    )
    source_after_sha = _require_sha256(
        source_snapshot.get("after_sha256"), "source after-snapshot SHA-256"
    )
    if source_before_sha != source_after_sha:
        raise ValueError("prepared CSOne replay source snapshot changed")
    source_sha = source_before_sha
    if source_sha != prepared.source_snapshot_sha256:
        raise ValueError("prepared CSOne replay source snapshot SHA-256 mismatch")
    if set(instrumentation) != {
        "preparation_count",
        "profile_loader_calls",
        "selected_reload_calls",
        "total_loader_calls",
    }:
        raise ValueError("prepared CSOne replay instrumentation inventory is invalid")
    counts = [instrumentation.get(key) for key in sorted(instrumentation)]
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError("prepared CSOne replay instrumentation count is invalid")
    if instrumentation["preparation_count"] != 1 or instrumentation["total_loader_calls"] != (
        instrumentation["profile_loader_calls"]
        + instrumentation["selected_reload_calls"]
    ):
        raise ValueError("prepared CSOne replay loader-call contract is invalid")
    if set(tac_contract) != {
        "columns",
        "dtypes",
        "records",
        "row_count",
        "status_coverage",
    }:
        raise ValueError("prepared CSOne replay TAC inventory is invalid")
    if tac_contract.get("columns") != list(_PSEUDONYMOUS_OUTPUT_COLUMNS):
        raise ValueError("prepared CSOne replay TAC schema is invalid")
    expected_dtypes = {
        column: (
            "datetime64[ns, UTC]"
            if column in DATE_COLUMNS
            else "Int64"
            if column == "# of Case Owner Changes"
            else "string"
        )
        for column in _PSEUDONYMOUS_OUTPUT_COLUMNS
    }
    if tac_contract.get("dtypes") != expected_dtypes:
        raise ValueError("prepared CSOne replay TAC dtype contract is invalid")
    records = tac_contract.get("records")
    row_count = tac_contract.get("row_count")
    if (
        not isinstance(records, list)
        or type(row_count) is not int
        or not 1 <= row_count <= int(expected_max_rows)
        or len(records) != row_count
        or any(not isinstance(item, dict) or set(item) != set(_PSEUDONYMOUS_OUTPUT_COLUMNS) for item in records)
    ):
        raise ValueError("prepared CSOne replay TAC row contract is invalid")
    status_coverage_raw = tac_contract.get("status_coverage")
    if not isinstance(status_coverage_raw, Mapping):
        raise ValueError("prepared CSOne replay status coverage is invalid")

    loader_contract = root.get("loader_contract")
    coverage = root.get("coverage")
    if not isinstance(loader_contract, dict) or not isinstance(coverage, dict):
        raise ValueError("prepared CSOne replay coverage contract is invalid")
    if set(loader_contract) != _PREPARED_LOADER_KEYS:
        raise ValueError("prepared CSOne replay loader inventory is invalid")
    if set(coverage) != _PREPARED_COVERAGE_KEYS:
        raise ValueError("prepared CSOne replay coverage inventory is invalid")
    loader_count_fields = (
        "candidate_workbook_count",
        "representative_workbook_count",
        "chronology_distinct_value_count",
        "schema_variant_count",
        "date_parse_failure_count",
    )
    coverage_count_fields = (
        "candidate_workbook_count",
        "nonempty_workbook_count",
        "empty_workbook_count",
        "candidate_source_row_count",
        "selected_workbook_count",
        "selected_source_row_count",
        "replay_row_count",
        "schema_variant_count",
        "chronology_distinct_value_count",
    )
    if any(
        type(loader_contract.get(key)) is not int or loader_contract[key] < 0
        for key in loader_count_fields
    ) or any(
        type(coverage.get(key)) is not int or coverage[key] < 0
        for key in coverage_count_fields
    ):
        raise ValueError("prepared CSOne replay coverage count is invalid")
    loader_results = loader_contract.get("results")
    if (
        not isinstance(loader_results, list)
        or len(loader_results) != loader_contract["representative_workbook_count"]
        or loader_contract["candidate_workbook_count"]
        != coverage["candidate_workbook_count"]
        or coverage["selected_workbook_count"]
        != loader_contract["representative_workbook_count"]
        or coverage["nonempty_workbook_count"]
        + coverage["empty_workbook_count"]
        != coverage["candidate_workbook_count"]
    ):
        raise ValueError("prepared CSOne replay coverage counts do not reconcile")
    for key in ("all_nonempty", "no_footer_rows_remaining", "consistent_schema", "breadth_ok"):
        if type(loader_contract.get(key)) is not bool:
            raise ValueError("prepared CSOne replay loader boolean is invalid")
    for key in ("chronology_established", "breadth_ok", "strict_breadth"):
        if type(coverage.get(key)) is not bool:
            raise ValueError("prepared CSOne replay coverage boolean is invalid")
    _validate_prepared_loader_coverage_contract(
        loader_contract,
        coverage,
        replay_row_count=row_count,
    )
    strict_breadth = coverage.get("strict_breadth") is True
    if strict_breadth and (
        loader_contract.get("breadth_ok") is not True
        or coverage.get("breadth_ok") is not True
    ):
        raise ValueError("prepared CSOne replay breadth is insufficient")
    if instrumentation["profile_loader_calls"] != loader_contract.get(
        "candidate_workbook_count"
    ) or instrumentation["selected_reload_calls"] != loader_contract.get(
        "representative_workbook_count"
    ):
        raise ValueError("prepared CSOne replay loader inventory mismatch")

    frame_sha = _sha256_json(records)
    coverage_sha = _sha256_json(coverage)
    loader_sha = _sha256_json(loader_contract)
    if set(hashes) != {
        "frame_sha256",
        "coverage_sha256",
        "loader_contract_sha256",
        "bems_sha256",
        "status_coverage_sha256",
    }:
        raise ValueError("prepared CSOne replay hash inventory is invalid")
    if (
        frame_sha != _require_sha256(hashes.get("frame_sha256"), "frame SHA-256")
        or coverage_sha != _require_sha256(hashes.get("coverage_sha256"), "coverage SHA-256")
        or loader_sha
        != _require_sha256(hashes.get("loader_contract_sha256"), "loader contract SHA-256")
        or frame_sha != prepared.frame_sha256
        or coverage_sha != prepared.coverage_sha256
    ):
        raise ValueError("prepared CSOne replay aggregate hash mismatch")

    tac = pd.DataFrame.from_records(records, columns=_PSEUDONYMOUS_OUTPUT_COLUMNS)
    for column in DATE_COLUMNS:
        values = tac[column]
        invalid_shape = values.map(
            lambda value: value is not None
            and not bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", str(value)))
        )
        if invalid_shape.any():
            raise ValueError("prepared CSOne replay date transport is invalid")
        tac[column] = pd.to_datetime(values, format="%Y-%m-%dT%H:%M:%SZ", errors="coerce", utc=True)
    owner = pd.to_numeric(tac["# of Case Owner Changes"], errors="coerce").astype("Int64")
    if (tac["# of Case Owner Changes"].notna() & owner.isna()).any():
        raise ValueError("prepared CSOne replay owner-change transport is invalid")
    tac["# of Case Owner Changes"] = owner
    status_coverage = _validate_status_coverage(
        status_coverage_raw,
        normalized_statuses=tac["Case Status"],
    )
    status_coverage_sha = _sha256_json(status_coverage)
    if status_coverage_sha != _require_sha256(
        hashes.get("status_coverage_sha256"),
        "status coverage SHA-256",
    ):
        raise ValueError("prepared CSOne replay status coverage SHA-256 mismatch")
    privacy = validate_pseudonymous_csone_frame(tac, as_of_utc=expected_as_of_utc)
    tac.attrs.update(
        {
            "sanitized": True,
            "source_mode": SOURCE_MODE,
            "source_state": "available",
            "source_dataset": "tac_cases",
            "data_as_of_utc": expected_as_of_utc,
            "live_validation_performed": False,
            "primary_key": "SR Number",
            "corpus_replay": True,
            "prepared_replay": True,
            "raw_values_retained": False,
            "privacy_contract": privacy,
            "source_row_count": int(coverage.get("selected_source_row_count") or 0),
            "replay_row_count": len(tac),
            "excluded_non_record_rows": sum(
                int(item.get("excluded_non_record_rows") or 0)
                for item in loader_contract.get("results") or []
                if isinstance(item, dict)
            ),
            "corpus_replay_coverage": coverage,
            "corpus_loader_contract": loader_contract,
            "prepared_replay_sha256": actual_payload_sha,
            "prepared_frame_sha256": frame_sha,
            "prepared_coverage_sha256": coverage_sha,
            "prepared_source_snapshot_sha256": source_sha,
            "consumer_loader_calls": 0,
            **_status_source_attrs(status_coverage),
        }
    )
    case_types = tac.apply(classify_case_type, axis=1)
    tac.attrs["case_type_distribution"] = {
        str(label): int(count) for label, count in case_types.value_counts().sort_index().items()
    }
    tac.attrs["case_type_distribution_reconciled"] = True
    bems = tac.loc[tac["Transaction ID"].fillna("").astype(str).str.strip().ne("")].copy().reset_index(drop=True)
    bems_records = _transport_records(bems)
    if _sha256_json(bems_records) != _require_sha256(hashes.get("bems_sha256"), "BEMS SHA-256"):
        raise ValueError("prepared CSOne replay BEMS derivation mismatch")
    bems_privacy = validate_pseudonymous_csone_frame(bems, as_of_utc=expected_as_of_utc)
    bems.attrs.update(tac.attrs)
    bems.attrs.update(
        {
            "source_dataset": "bems_cases",
            "primary_key": "Transaction ID",
            "privacy_contract": bems_privacy,
        }
    )
    return tac, bems, root


def prepare_csone_replay(
    bundle: LocalAcceptanceBundle,
    corpus_dir: Path,
    *,
    loader: Callable[[str], pd.DataFrame],
    max_rows: int = 600,
    manifest_sha256: str,
    strict_breadth: bool = True,
    input_limits: CorpusInputLimits = DEFAULT_CORPUS_INPUT_LIMITS,
) -> PreparedCsoneReplay:
    """Load a corpus once and return an immutable pseudonymous transport."""

    limit = int(max_rows)
    if not 1 <= limit <= 10000:
        raise ValueError("prepared CSOne replay max_rows must be between 1 and 10000")
    manifest_digest = _require_sha256(manifest_sha256, "manifest SHA-256")
    before = _inventory_stat_contract(corpus_dir, limits=input_limits)
    calls = {"profile": 0, "reload": 0}
    phase = "profile"

    def counted_loader(path: str) -> pd.DataFrame:
        calls[phase] += 1
        return loader(path)

    profiles = _profile_corpus_workbooks(
        corpus_dir,
        loader=counted_loader,
        limits=input_limits,
    )
    selected, coverage = _select_replay_profiles(profiles, max_rows=limit)
    loader_contract = _loader_contract_from_profiles(profiles, selected, coverage)
    if loader_contract.get("date_parse_failure_count") != 0:
        raise ValueError(
            "strict CSOne replay rejected unparseable declared date values"
        )
    coverage["strict_breadth"] = bool(strict_breadth)
    if strict_breadth and not coverage["breadth_ok"]:
        raise ValueError(
            "strict CSOne corpus replay breadth unavailable ("
            + ",".join(coverage["breadth_reasons"])
            + ")"
        )
    phase = "reload"
    loaded = _combined_replay_source(
        selected,
        coverage,
        loader=counted_loader,
        max_rows=limit,
        limits=input_limits,
    )
    tac = pseudonymize_csone_frame(loaded, bundle, max_rows=limit)
    replay_coverage = dict(tac.attrs.get("corpus_replay_coverage") or {})
    if strict_breadth and replay_coverage.get("breadth_ok") is not True:
        raise ValueError(
            "strict CSOne corpus replay breadth unavailable ("
            + ",".join(replay_coverage.get("breadth_reasons") or ())
            + ")"
        )
    del loaded
    del profiles
    del selected
    after = _inventory_stat_contract(corpus_dir, limits=input_limits)
    if before != after:
        raise ValueError("CSOne corpus source snapshot changed during preparation")
    records = _transport_records(tac)
    status_coverage = _validate_status_coverage(
        tac.attrs.get("tac_status_coverage") or {},
        normalized_statuses=tac["Case Status"],
    )
    bems = tac.loc[tac["Transaction ID"].fillna("").astype(str).str.strip().ne("")].copy().reset_index(drop=True)
    validate_pseudonymous_csone_frame(bems, as_of_utc=bundle.as_of_utc)
    instrumentation = {
        "preparation_count": 1,
        "profile_loader_calls": calls["profile"],
        "selected_reload_calls": calls["reload"],
        "total_loader_calls": calls["profile"] + calls["reload"],
    }
    if (
        calls["profile"] != loader_contract["candidate_workbook_count"]
        or calls["reload"] != loader_contract["representative_workbook_count"]
    ):
        raise ValueError("CSOne corpus loader-call instrumentation mismatch")
    dtypes = {
        column: (
            "datetime64[ns, UTC]"
            if column in DATE_COLUMNS
            else "Int64"
            if column == "# of Case Owner Changes"
            else "string"
        )
        for column in _PSEUDONYMOUS_OUTPUT_COLUMNS
    }
    root = {
        "schema_version": PREPARED_REPLAY_SCHEMA_VERSION,
        "sanitized": True,
        "privacy_policy_version": PSEUDONYMOUS_POLICY_VERSION,
        "manifest": {
            "schema_version": SCHEMA_VERSION,
            "schema_fingerprint": bundle.schema_fingerprint,
            "sha256": manifest_digest,
        },
        "clock": {"as_of_utc": bundle.as_of_utc},
        "max_rows": limit,
        "source_snapshot": {
            "before_sha256": before["sha256"],
            "after_sha256": after["sha256"],
            "workbook_count": before["workbook_count"],
            "total_bytes": before["total_bytes"],
        },
        "loader_contract": loader_contract,
        "coverage": replay_coverage,
        "instrumentation": instrumentation,
        "tac": {
            "columns": list(_PSEUDONYMOUS_OUTPUT_COLUMNS),
            "dtypes": dtypes,
            "records": records,
            "row_count": len(records),
            "status_coverage": status_coverage,
        },
        "hashes": {
            "frame_sha256": _sha256_json(records),
            "coverage_sha256": _sha256_json(replay_coverage),
            "loader_contract_sha256": _sha256_json(loader_contract),
            "bems_sha256": _sha256_json(_transport_records(bems)),
            "status_coverage_sha256": _sha256_json(status_coverage),
        },
    }
    payload = _canonical_json_bytes(root)
    if len(payload) > MAX_PREPARED_REPLAY_BYTES:
        raise ValueError("prepared CSOne replay exceeds the transport byte limit")
    prepared = PreparedCsoneReplay(
        payload=payload,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        frame_sha256=root["hashes"]["frame_sha256"],
        coverage_sha256=root["hashes"]["coverage_sha256"],
        source_snapshot_sha256=before["sha256"],
        manifest_sha256=manifest_digest,
        as_of_utc=bundle.as_of_utc,
        max_rows=limit,
    )
    _validated_prepared_frames(
        prepared,
        expected_as_of_utc=bundle.as_of_utc,
        expected_manifest_sha256=manifest_digest,
        expected_max_rows=limit,
    )
    return prepared


def prepared_replay_from_bytes(
    payload: bytes,
    *,
    expected_length: int,
    expected_sha256: str,
    expected_as_of_utc: str,
    expected_manifest_sha256: str,
    expected_max_rows: int,
) -> PreparedCsoneReplay:
    """Validate an untrusted bounded stdin payload before app installation."""

    if type(expected_length) is not int or expected_length != len(payload):
        raise ValueError("prepared CSOne replay byte length mismatch")
    payload_sha = _require_sha256(expected_sha256, "expected payload SHA-256")
    if hashlib.sha256(payload).hexdigest() != payload_sha:
        raise ValueError("prepared CSOne replay payload SHA-256 mismatch")
    root = _decode_prepared_payload(payload)
    prepared = PreparedCsoneReplay(
        payload=bytes(payload),
        payload_sha256=payload_sha,
        frame_sha256=_require_sha256(root.get("hashes", {}).get("frame_sha256"), "frame SHA-256"),
        coverage_sha256=_require_sha256(root.get("hashes", {}).get("coverage_sha256"), "coverage SHA-256"),
        source_snapshot_sha256=_require_sha256(
            root.get("source_snapshot", {}).get("before_sha256"),
            "source before-snapshot SHA-256",
        ),
        manifest_sha256=_require_sha256(root.get("manifest", {}).get("sha256"), "manifest SHA-256"),
        as_of_utc=str(root.get("clock", {}).get("as_of_utc") or ""),
        max_rows=root.get("max_rows") if type(root.get("max_rows")) is int else -1,
    )
    _validated_prepared_frames(
        prepared,
        expected_as_of_utc=expected_as_of_utc,
        expected_manifest_sha256=expected_manifest_sha256,
        expected_max_rows=expected_max_rows,
    )
    return prepared


def apply_prepared_csone_replay(
    bundle: LocalAcceptanceBundle,
    prepared: PreparedCsoneReplay,
    *,
    manifest_sha256: str,
) -> LocalAcceptanceBundle:
    """Deep-copy one validated transport into a scenario without loader access."""

    tac, bems, root = _validated_prepared_frames(
        prepared,
        expected_as_of_utc=bundle.as_of_utc,
        expected_manifest_sha256=manifest_sha256,
        expected_max_rows=prepared.max_rows,
    )
    if root["manifest"]["schema_fingerprint"] != bundle.schema_fingerprint:
        raise ValueError("prepared CSOne replay bundle schema fingerprint mismatch")
    frames = dict(bundle.frames)
    frames["tac_cases"] = tac.copy(deep=True)
    frames["tac_cases"].attrs.update(tac.attrs)
    frames["bems_cases"] = bems.copy(deep=True)
    frames["bems_cases"].attrs.update(bems.attrs)
    counts = dict(bundle.expected_counts)
    counts.update({"tac_cases": len(tac), "bems_cases": len(bems)})
    canonical = dict(bundle.expected_canonical_counts)
    canonical.update(
        {
            "tac_cases": _canonical_count(tac, "SR Number"),
            "bems_cases": _canonical_count(bems, "Transaction ID"),
        }
    )
    warning_codes = dict(bundle.warning_codes)
    tac_state = str(tac.attrs.get("source_state") or "available")
    bems_state = str(bems.attrs.get("source_state") or tac_state)
    warning_codes.update(
        {
            "tac_cases": tuple(
                dict.fromkeys(
                    (
                        *_warnings(tac, "SR Number"),
                        *(() if tac_state == "available" else (f"source_{tac_state}",)),
                    )
                )
            ),
            "bems_cases": tuple(
                dict.fromkeys(
                    (
                        *_warnings(bems, "Transaction ID"),
                        *(
                            ()
                            if bems_state == "available"
                            else (f"source_{bems_state}",)
                        ),
                    )
                )
            ),
        }
    )
    source_states = dict(bundle.source_states)
    source_states.update({"tac_cases": tac_state, "bems_cases": bems_state})
    replayed = replace(
        bundle,
        frames=frames,
        expected_counts=counts,
        expected_canonical_counts=canonical,
        warning_codes=warning_codes,
        source_states=source_states,
    )
    replayed.assert_reconciled()
    return replayed


def prepared_replay_summary(prepared: PreparedCsoneReplay) -> dict[str, Any]:
    """Return aggregate-only evidence for the acceptance projector."""

    root = _decode_prepared_payload(prepared.payload)
    tac, bems, _ = _validated_prepared_frames(
        prepared,
        expected_as_of_utc=prepared.as_of_utc,
        expected_manifest_sha256=prepared.manifest_sha256,
        expected_max_rows=prepared.max_rows,
    )
    return {
        "schema_version": "csone-corpus-replay/v4",
        "sanitized": True,
        "do_not_commit": True,
        "source_mode": SOURCE_MODE,
        "live_snowflake_validation_performed": False,
        "source_rows_exported": False,
        "source_values_exported": False,
        "raw_values_retained": False,
        "production_accuracy_claimed": False,
        "all_passed": True,
        "loader_contract": root["loader_contract"],
        "prepared_replay": {
            "payload_sha256": prepared.payload_sha256,
            "frame_sha256": prepared.frame_sha256,
            "coverage_sha256": prepared.coverage_sha256,
            "source_snapshot_sha256": prepared.source_snapshot_sha256,
            "status_coverage_sha256": root["hashes"][
                "status_coverage_sha256"
            ],
            "byte_length": prepared.byte_length,
            "instrumentation": root["instrumentation"],
        },
        "replay": {
            "row_count": len(tac),
            "bems_row_count": len(bems),
            "source_row_count": int(tac.attrs.get("source_row_count") or 0),
            "excluded_non_record_rows": int(tac.attrs.get("excluded_non_record_rows") or 0),
            "pseudonym_contract_ok": True,
            "privacy_contract": tac.attrs["privacy_contract"],
            "missing_record_id_rows": int(tac["SR Number"].fillna("").astype(str).str.strip().eq("").sum()),
            "missing_status_rows": int(
                root["tac"]["status_coverage"]["normalized_unknown_count"]
            ),
            "raw_missing_status_rows": int(
                root["tac"]["status_coverage"]["raw_missing_count"]
            ),
            "status_coverage": root["tac"]["status_coverage"],
            "status_coverage_sha256": root["hashes"][
                "status_coverage_sha256"
            ],
            "case_type_distribution": dict(tac.attrs.get("case_type_distribution") or {}),
            "case_type_distribution_reconciled": True,
            "corpus_coverage": root["coverage"],
            "frame_sha256": prepared.frame_sha256,
        },
    }


def replay_bundle_from_corpus(
    bundle: LocalAcceptanceBundle,
    corpus_dir: Path,
    *,
    loader: Callable[[str], pd.DataFrame],
    max_rows: int = 600,
    strict_breadth: bool = False,
    manifest_sha256: str | None = None,
    input_limits: CorpusInputLimits = DEFAULT_CORPUS_INPUT_LIMITS,
) -> LocalAcceptanceBundle:
    """Compatibility wrapper: prepare once, then apply without loader access."""

    manifest_digest = manifest_sha256 or hashlib.sha256(
        f"{SCHEMA_VERSION}:{bundle.schema_fingerprint}:{bundle.as_of_utc}".encode("utf-8")
    ).hexdigest()
    prepared = prepare_csone_replay(
        bundle,
        corpus_dir,
        loader=loader,
        max_rows=max_rows,
        manifest_sha256=manifest_digest,
        strict_breadth=strict_breadth,
        input_limits=input_limits,
    )
    return apply_prepared_csone_replay(
        bundle, prepared, manifest_sha256=manifest_digest
    )


def validate_representative_loaders(
    corpus_dir: Path,
    *,
    loader: Callable[[str], pd.DataFrame],
    max_rows: int = 600,
    input_limits: CorpusInputLimits = DEFAULT_CORPUS_INPUT_LIMITS,
) -> dict[str, Any]:
    """Profile selected corpus strata while retaining only aggregate metadata."""

    profiles = _profile_corpus_workbooks(
        corpus_dir,
        loader=loader,
        limits=input_limits,
    )
    selected, coverage = _select_replay_profiles(
        profiles,
        max_rows=max(int(max_rows), 1),
    )
    return _loader_contract_from_profiles(profiles, selected, coverage)


def _loader_contract_from_profiles(
    profiles: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    coverage: Mapping[str, Any],
) -> dict[str, Any]:
    results = [
        {
            "strata": list(profile.get("strata") or ()),
            "row_count": profile["row_count"],
            "column_count": profile["column_count"],
            "excluded_non_record_rows": profile["excluded_non_record_rows"],
            "footer_like_rows_remaining": profile["footer_like_rows_remaining"],
            "missing_record_id_rows": profile["missing_record_id_rows"],
            "date_parse_failure_count": profile["date_parse_failure_count"],
            "schema_sha256": profile["schema_sha256"],
        }
        for profile in selected
    ]
    return {
        "candidate_workbook_count": coverage["candidate_workbook_count"],
        "representative_workbook_count": len(results),
        "all_nonempty": all(item["row_count"] > 0 for item in results),
        "no_footer_rows_remaining": all(
            item["footer_like_rows_remaining"] == 0 for item in results
        ),
        "consistent_schema": len({item["schema_sha256"] for item in results}) == 1,
        "chronology_basis": coverage["chronology_basis"],
        "chronology_distinct_value_count": coverage[
            "chronology_distinct_value_count"
        ],
        "chronology_established": coverage["chronology_established"],
        "schema_variant_count": coverage["schema_variant_count"],
        "covered_strata": coverage["covered_strata"],
        "breadth_ok": coverage["breadth_ok"],
        "breadth_reasons": coverage["breadth_reasons"],
        "date_parse_failure_count": sum(
            int(profile.get("date_parse_failure_count") or 0) for profile in profiles
        ),
        "results": results,
    }
