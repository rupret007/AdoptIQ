"""Privacy-preserving replay of real CSOne workbook shape and distributions.

The replay uses the production workbook loader, then immediately projects the
result into an allow-listed pseudonymous schema.  It preserves report-relevant
status, severity, technology, date, identifier-missingness, and BEMS/TAC shape
while discarding customer names, emails, free text, and source identifiers.
Nothing in this module is imported by the production application or build.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import replace
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
from local_acceptance_lab import LocalAcceptanceBundle, SOURCE_MODE


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
_MAX_SOURCE_CATEGORY_LENGTH = 256
_MAX_OUTPUT_TEXT_LENGTH = 160
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SAFE_SEVERITIES = frozenset({"P1", "P2", "P3", "P4", "Unknown"})
_SAFE_STATUSES = frozenset({"Open", "Closed", "Unknown"})
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


def discover_corpus_workbooks(corpus_dir: Path) -> list[Path]:
    resolved = corpus_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("CSOne corpus directory is not readable")
    workbooks = sorted(
        path
        for path in resolved.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold() == ".xlsx"
        and not path.name.startswith("~$")
    )
    if not workbooks:
        raise ValueError("CSOne corpus directory contains no xlsx workbooks")
    return workbooks


def _timestamp_value(value: Any) -> Optional[int]:
    try:
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(parsed, pd.Series):
        parsed = parsed.dropna()
        if parsed.empty:
            return None
        parsed = parsed.max()
    if isinstance(parsed, pd.DatetimeIndex):
        parsed = parsed.dropna()
        if parsed.empty:
            return None
        parsed = parsed.max()
    try:
        if pd.isna(parsed):
            return None
        return int(pd.Timestamp(parsed).value)
    except (TypeError, ValueError, OverflowError):
        return None


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
    values = [
        value
        for column in DATE_COLUMNS
        if column in frame.columns
        for value in [_timestamp_value(frame[column])]
        if value is not None
    ]
    return max(values) if values else None


def _schema_sha256(frame: pd.DataFrame) -> str:
    return hashlib.sha256(
        "\n".join(map(str, frame.columns)).encode("utf-8")
    ).hexdigest()


def _profile_corpus_workbooks(
    corpus_dir: Path,
    *,
    loader: Callable[[str], pd.DataFrame],
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for path in discover_corpus_workbooks(corpus_dir):
        loaded = loader(str(path))
        if not isinstance(loaded, pd.DataFrame):
            raise TypeError("production CSOne loader must return a DataFrame")
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
                "excluded_non_record_rows": int(
                    loaded.attrs.get("excluded_non_record_rows") or 0
                ),
                "footer_like_rows_remaining": int((ids.eq("") & ~substantive).sum()),
                "missing_record_id_rows": int(ids.eq("").sum()),
                "frame_export_metadata": _frame_export_timestamp(loaded),
                "workbook_modified": core["workbook_modified"],
                "workbook_created": core["workbook_created"],
                "case_observation_max": _case_observation_timestamp(loaded),
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


def representative_workbooks(corpus_dir: Path) -> list[Path]:
    workbooks = discover_corpus_workbooks(corpus_dir)
    metadata: list[dict[str, Any]] = []
    for path in workbooks:
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
        column: pd.to_datetime(
            source.get(column), errors="coerce", utc=True, format="mixed"
        )
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
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    excluded_rows = 0
    for profile in selected:
        loaded = loader(str(profile["path"]))
        if not isinstance(loaded, pd.DataFrame) or loaded.empty:
            raise ValueError("selected CSOne replay workbook did not reload nonempty")
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
    result["Case Status"] = sampled.get(
        "Case Status", pd.Series("", index=sampled.index, dtype="object")
    ).map(_canonical_status)
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


def replay_bundle_from_corpus(
    bundle: LocalAcceptanceBundle,
    corpus_dir: Path,
    *,
    loader: Callable[[str], pd.DataFrame],
    max_rows: int = 600,
    strict_breadth: bool = False,
) -> LocalAcceptanceBundle:
    """Replace only TAC/BEMS fixture frames with a real-shape safe replay."""

    limit = max(int(max_rows), 1)
    profiles = _profile_corpus_workbooks(corpus_dir, loader=loader)
    selected, coverage = _select_replay_profiles(profiles, max_rows=limit)
    loader_contract = _loader_contract_from_profiles(profiles, selected, coverage)
    coverage["strict_breadth"] = bool(strict_breadth)
    if strict_breadth and not coverage["breadth_ok"]:
        reasons = ",".join(coverage["breadth_reasons"])
        raise ValueError(f"strict CSOne corpus replay breadth unavailable ({reasons})")
    loaded = _combined_replay_source(
        selected,
        coverage,
        loader=loader,
        max_rows=limit,
    )
    tac = pseudonymize_csone_frame(loaded, bundle, max_rows=limit)
    tac.attrs["corpus_loader_contract"] = loader_contract
    replay_coverage = dict(tac.attrs.get("corpus_replay_coverage") or {})
    if strict_breadth and not replay_coverage.get("breadth_ok"):
        reasons = ",".join(replay_coverage.get("breadth_reasons") or ())
        raise ValueError(f"strict CSOne corpus replay breadth unavailable ({reasons})")
    del loaded
    del profiles
    del selected
    bems = (
        tac.loc[tac["Transaction ID"].fillna("").astype(str).str.strip().ne("")]
        .copy()
        .reset_index(drop=True)
    )
    bems_privacy_contract = validate_pseudonymous_csone_frame(
        bems,
        as_of_utc=bundle.as_of_utc,
    )
    bems.attrs.update(tac.attrs)
    bems.attrs.update(
        {
            "source_dataset": "bems_cases",
            "primary_key": "Transaction ID",
            "raw_values_retained": bems_privacy_contract["raw_values_retained"],
            "privacy_contract": bems_privacy_contract,
        }
    )

    frames = dict(bundle.frames)
    frames["tac_cases"] = tac
    frames["bems_cases"] = bems
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
    warning_codes.update(
        {
            "tac_cases": _warnings(tac, "SR Number"),
            "bems_cases": _warnings(bems, "Transaction ID"),
        }
    )
    replayed = replace(
        bundle,
        frames=frames,
        expected_counts=counts,
        expected_canonical_counts=canonical,
        warning_codes=warning_codes,
    )
    replayed.assert_reconciled()
    return replayed


def validate_representative_loaders(
    corpus_dir: Path,
    *,
    loader: Callable[[str], pd.DataFrame],
    max_rows: int = 600,
) -> dict[str, Any]:
    """Profile selected corpus strata while retaining only aggregate metadata."""

    profiles = _profile_corpus_workbooks(corpus_dir, loader=loader)
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
        "results": results,
    }
