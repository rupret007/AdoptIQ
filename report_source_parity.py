"""Privacy-safe, exact cross-family Source Data parity contracts.

Compact, Comprehensive, Leader, and Renewal are different presentations of
the same scoped canonical facts.  This module projects their paired Source
Data workbooks into deterministic signatures without retaining source values.

Only explicitly typed legacy family facts are excluded.  Everything else in
the canonical 17-sheet contract participates in parity.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Iterator
from zipfile import BadZipFile, ZipFile

import pandas as pd

from decision_report_delivery import SOURCE_DATA_SHEET_NAMES


PARITY_SHEET_NAMES = tuple(SOURCE_DATA_SHEET_NAMES)
PARITY_PROJECTED_FIELDS = (
    "count",
    "row_count",
    "missing_identity_count",
    "duplicate_identity_count",
    "identity_sha256",
    "semantic_sha256",
    "attribution_sha256",
    "attributed_record_count",
    "source_state",
    "source_state_sha256",
)

# Canonical Source Data workbooks are ordinarily only a few megabytes.  These
# ceilings leave ample room for real manager portfolios while preventing an
# artifact path or pathological OOXML archive from turning the parity gate into
# an unbounded parser/decompression workload.
PARITY_MAX_WORKBOOK_BYTES = 128 * 1024 * 1024
PARITY_MAX_ZIP_ENTRIES = 4096
PARITY_MAX_ZIP_ENTRY_BYTES = 64 * 1024 * 1024
PARITY_MAX_ZIP_EXPANDED_BYTES = 512 * 1024 * 1024
PARITY_MAX_ZIP_COMPRESSION_RATIO = 250.0

_SOURCE_STATE_SHEETS = (
    "Subscriptions",
    "Action_Plans",
    "Adoption_Barriers",
    "Customer_Pulse",
    "TAC_Cases",
    "BEMS",
    "Success_Priorities",
    "External_Incidents",
    "External_Bugs",
    "Defect_Correlations",
)
_ALLOWED_SOURCE_STATES = frozenset(
    {"available", "zero", "partial", "stale", "failed", "unavailable", "unknown"}
)
_ALLOWED_DATA_AS_OF_STATES = frozenset(
    {"available", "partial", "stale", "failed", "unavailable", "unknown"}
)
_ALLOWED_SCOPE_TYPES = frozenset({"team", "member", "customer", "comprehensive"})
_ALLOWED_LIVE_VALIDATION = frozenset({"yes", "no", "not independently attested"})
_ALLOWED_DATA_MODES = frozenset(
    {"application source path", "guarded offline fixture"}
)
_REPORT_FAMILY_BY_TYPE = {
    "compact": "compact",
    "comprehensive": "comprehensive",
    "leader": "leader",
    "renewal": "renewal",
    "renewal portfolio": "renewal",
    "renewal individual": "renewal",
}
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_FAMILY_FACT_MARKER = "family-specific reported fact"
_FAMILY_EVIDENCE_ROLE = "legacy_family_reported_fact"
_FAMILY_KEY_PREFIX = "legacy.family."
_CROSS_FAMILY_PARITY_COLUMN = "Cross_Family_Parity"
_FAMILY_PRESENTATION_MARKER = "family_specific_presentation_fact"
_ACCOUNT_REVIEW_KEY_RE = re.compile(r"^summary\.account\..+\.review_focus$")
_MEMBER_INTERVENTION_KEY_RE = re.compile(
    r"^summary\.member\..+\.manager_intervention$"
)
_SUBSCRIPTION_FAMILY_COLUMNS = frozenset(
    {
        "Legacy_Record_Type",
        "Legacy_Report_Family",
        "Legacy_Source_Sheet",
        "Legacy_Source_Row_Number",
        "Legacy_Source_Field",
        "Legacy_Fact_Label",
        "Legacy_Fact_Value",
        "Metric_Key",
    }
)
_REPORT_INFO_REQUIRED_CANONICAL_ITEMS = (
    "Manager",
    "Technology",
    "Scope_Type",
    "Scope_Value",
    "Days",
    "Data_As_Of_UTC",
    "Data_As_Of_State",
    "Retrieval_Attempted_At_UTC",
    "Evaluation_As_Of_UTC",
    "Data_Mode",
    "Live_Source_Validation",
    "Due_Soon_Days",
    "Action_Plan_Age_Bands",
    "Activity_Total_State",
    "TAC_Case_Type_Classified",
    "TAC_Case_Type_Not_Derivable",
    "TAC_Case_Type_Coverage_Pct",
    "Partial_Data_Warning_Count",
)


class ParityContractError(ValueError):
    """A workbook cannot participate in exact cross-family parity."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _fail(kind: str) -> None:
    raise ParityContractError(kind)


def _path_within_root(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_zip_contract(stream: BinaryIO) -> None:
    """Reject malformed or expansion-heavy OOXML before pandas/openpyxl."""

    try:
        stream.seek(0)
        with ZipFile(stream) as archive:
            entries = archive.infolist()
            if not entries:
                _fail("workbook_zip_empty")
            if len(entries) > PARITY_MAX_ZIP_ENTRIES:
                _fail("workbook_zip_entry_count_exceeded")
            expanded_total = 0
            for entry in entries:
                member = PurePosixPath(entry.filename.replace("\\", "/"))
                if member.is_absolute() or ".." in member.parts:
                    _fail("workbook_zip_member_path_invalid")
                if entry.flag_bits & 0x1:
                    _fail("workbook_zip_encrypted_entry")
                if entry.file_size < 0 or entry.compress_size < 0:
                    _fail("workbook_zip_size_invalid")
                if entry.file_size > PARITY_MAX_ZIP_ENTRY_BYTES:
                    _fail("workbook_zip_entry_size_exceeded")
                expanded_total += int(entry.file_size)
                if expanded_total > PARITY_MAX_ZIP_EXPANDED_BYTES:
                    _fail("workbook_zip_expanded_size_exceeded")
                if entry.file_size:
                    ratio = entry.file_size / max(int(entry.compress_size), 1)
                    if ratio > PARITY_MAX_ZIP_COMPRESSION_RATIO:
                        _fail("workbook_zip_compression_ratio_exceeded")
    except ParityContractError:
        raise
    except (BadZipFile, OSError, RuntimeError, ValueError):
        _fail("workbook_zip_invalid")
    finally:
        try:
            stream.seek(0)
        except (OSError, ValueError):
            pass


@contextmanager
def _bounded_workbook_stream(
    path: Path | str,
    *,
    allowed_root: Path | str | None,
) -> Iterator[BinaryIO]:
    """Open one stable, bounded, non-symlink artifact under its declared root."""

    requested = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    try:
        root = Path(
            allowed_root if allowed_root is not None else requested.parent
        ).expanduser().resolve(strict=True)
        parent = requested.parent.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail("workbook_path_invalid")
    if not root.is_dir():
        _fail("workbook_allowed_root_invalid")
    if not _path_within_root(parent, root):
        _fail("workbook_outside_allowed_root")

    try:
        before = requested.lstat()
    except OSError:
        _fail("workbook_missing")
    if stat.S_ISLNK(before.st_mode):
        _fail("workbook_symlink_rejected")
    if not stat.S_ISREG(before.st_mode):
        _fail("workbook_not_regular_file")
    if not 1 <= before.st_size <= PARITY_MAX_WORKBOOK_BYTES:
        _fail("workbook_size_out_of_bounds")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(requested, flags)
    except OSError:
        _fail("workbook_open_failed")
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_size != before.st_size
            ):
                _fail("workbook_identity_changed")
            _validate_zip_contract(stream)
            yield stream
            after_fd = os.fstat(stream.fileno())
    except ParityContractError:
        raise
    except OSError:
        _fail("workbook_read_failed")

    try:
        after_path = requested.lstat()
    except OSError:
        _fail("workbook_identity_changed")
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(
        getattr(before, field) != getattr(after_fd, field)
        or getattr(before, field) != getattr(after_path, field)
        for field in identity_fields
    ):
        _fail("workbook_identity_changed")


def validate_ooxml_artifact(
    path: Path | str,
    *,
    allowed_root: Path | str | None = None,
) -> None:
    """Validate one bounded OOXML artifact without parsing document content."""

    with _bounded_workbook_stream(path, allowed_root=allowed_root):
        return


def _text(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    try:
        if bool(pd.isna(value)):
            return default
    except (TypeError, ValueError):
        pass
    result = str(value).strip()
    return result if result else default


def _utc_text(value: Any, *, required: bool = False, kind: str) -> str:
    raw = _text(value)
    if not raw:
        if required:
            _fail(kind)
        return ""
    parsed = pd.to_datetime(raw, utc=True, errors="coerce")
    if pd.isna(parsed):
        _fail(kind)
    return parsed.isoformat()


def _digest_cell(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime, date)):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        return {"datetime": timestamp.isoformat()}
    if isinstance(value, bool):
        return value
    if hasattr(value, "item"):
        try:
            return _digest_cell(value.item())
        except Exception:  # noqa: BLE001 - normalization fails closed below
            pass
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(format(value, ".15g")) if isinstance(value, float) else Decimal(str(value))
        if not number.is_finite():
            return None
        return {"number": format(number.normalize(), "f")}
    if isinstance(value, str):
        return value if value else None
    return str(value)


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sheet_content_sha256(frame: pd.DataFrame) -> str:
    """Hash an already-exported worksheet independent of row/column order."""

    prepared = frame.copy()
    if prepared.shape[1] == 0:
        prepared = pd.DataFrame(columns=["Record_ID"])
    columns = sorted(str(column) for column in prepared.columns)
    normalized_rows = [
        json.dumps(
            {
                column: _digest_cell(row.get(column))
                for column in columns
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        for _, row in prepared.iterrows()
    ]
    return _sha256_json({"columns": columns, "rows": sorted(normalized_rows)})


def _type_preserving_workbook_frames(stream: BinaryIO) -> dict[str, pd.DataFrame]:
    """Read public cells without pandas' mixed bool/number coercion.

    ``pd.read_excel(..., dtype=object)`` still performs column-level type
    inference.  In a family-fact column that legitimately contains both
    booleans and numbers, that can turn an Excel boolean into ``0``/``1`` (or
    a numeric zero into ``False``).  Report_Info hashes are generated and
    post-write validated from the actual OOXML cell types, so parity must use
    those same types or an intact workbook can falsely look tampered.
    """

    from openpyxl import load_workbook  # noqa: PLC0415

    stream.seek(0)
    workbook = load_workbook(stream, read_only=True, data_only=False)
    try:
        if tuple(workbook.sheetnames) != PARITY_SHEET_NAMES:
            _fail("source_data_sheet_inventory_mismatch")
        frames: dict[str, pd.DataFrame] = {}
        for sheet_name in PARITY_SHEET_NAMES:
            rows = workbook[sheet_name].iter_rows(values_only=True)
            headers = next(rows, None)
            if headers is None:
                frames[sheet_name] = pd.DataFrame()
                continue
            frames[sheet_name] = pd.DataFrame(
                list(rows),
                columns=list(headers),
            )
        return frames
    finally:
        workbook.close()
        stream.seek(0)


def _normalize_identity(value: Any) -> str:
    return _text(value)


def _chart_identity(row: Mapping[str, Any]) -> str:
    metric_key = _text(row.get("Metric_Key"))
    if metric_key:
        return metric_key
    parts = (
        _text(row.get("Chart_ID")),
        _text(row.get("Series")),
        _text(row.get("Category")),
        _text(row.get("Period_Start")),
        _text(row.get("Source")),
        _text(row.get("Date_Field")),
    )
    return _sha256_json(parts) if any(parts) else ""


def _evidence_identity(row: Mapping[str, Any]) -> str:
    parts = (
        _text(row.get("Evidence_Key")),
        _text(row.get("Evidence_Role")),
        _text(row.get("Source_Sheet")),
        _text(row.get("Source_Row_SHA256")),
        _text(row.get("Record_ID")),
        _text(row.get("Chart_ID")),
        _text(row.get("Chart_Series")),
        _text(row.get("Chart_Category")),
    )
    return _sha256_json(parts) if any(parts) else ""


def _row_identity(sheet_name: str, row: Mapping[str, Any]) -> str:
    if sheet_name == "Chart_Data":
        return _chart_identity(row)
    if sheet_name == "Evidence_Links":
        return _evidence_identity(row)
    if sheet_name in {"Metric_Lineage", "Risk_Components", "Member_Summary", "Account_Summary"}:
        return _normalize_identity(row.get("Metric_Key"))
    return _normalize_identity(row.get("Record_ID"))


def _report_family(value: Any) -> str:
    family = _REPORT_FAMILY_BY_TYPE.get(_text(value).casefold(), "")
    if not family:
        _fail("report_info_report_type_invalid")
    return family


def _family_row_mask(
    frame: pd.DataFrame,
    sheet_name: str,
    *,
    report_family: str,
) -> pd.Series:
    mask = pd.Series(False, index=frame.index, dtype=bool)
    if sheet_name == "Subscriptions":
        markers = (
            frame.get("Legacy_Record_Type", pd.Series("", index=frame.index))
            .fillna("")
            .astype(str)
            .str.strip()
            .str.casefold()
        )
        unknown = markers.loc[~markers.isin({"", _FAMILY_FACT_MARKER})]
        if not unknown.empty:
            _fail("subscriptions_unknown_family_fact_marker")
        mask = markers.eq(_FAMILY_FACT_MARKER)
        keys = frame.get("Metric_Key", pd.Series("", index=frame.index)).fillna("").astype(str)
        if bool((mask & ~keys.str.startswith(_FAMILY_KEY_PREFIX)).any()):
            _fail("subscriptions_family_fact_marker_key_mismatch")
        if bool((~mask & keys.str.startswith(_FAMILY_KEY_PREFIX)).any()):
            _fail("subscriptions_untyped_family_fact")
        declared_families = (
            frame.get("Legacy_Report_Family", pd.Series("", index=frame.index))
            .fillna("")
            .astype(str)
            .str.strip()
            .str.casefold()
        )
        expected_prefix = f"legacy.family.{report_family}."
        if bool((mask & declared_families.ne(report_family)).any()):
            _fail("subscriptions_family_fact_report_family_mismatch")
        if bool((mask & ~keys.str.casefold().str.startswith(expected_prefix)).any()):
            _fail("subscriptions_family_fact_key_family_mismatch")
        if bool((~mask & declared_families.ne("")).any()):
            _fail("subscriptions_untyped_report_family")
    elif sheet_name == "Metric_Lineage":
        grouping = frame.get("Grouping", pd.Series("", index=frame.index)).fillna("").astype(str)
        mask = grouping.str.strip().str.casefold().eq("legacy family-specific reported fact")
        keys = frame.get("Metric_Key", pd.Series("", index=frame.index)).fillna("").astype(str)
        if bool((mask & ~keys.str.startswith(_FAMILY_KEY_PREFIX)).any()):
            _fail("lineage_family_fact_marker_key_mismatch")
        if bool((~mask & keys.str.startswith(_FAMILY_KEY_PREFIX)).any()):
            _fail("lineage_untyped_family_fact")
        if bool(
            (
                mask
                & ~keys.str.casefold().str.startswith(
                    f"legacy.family.{report_family}."
                )
            ).any()
        ):
            _fail("lineage_family_fact_report_family_mismatch")
    elif sheet_name == "Evidence_Links":
        roles = frame.get("Evidence_Role", pd.Series("", index=frame.index)).fillna("").astype(str)
        mask = roles.str.strip().str.casefold().eq(_FAMILY_EVIDENCE_ROLE)
        keys = frame.get("Evidence_Key", pd.Series("", index=frame.index)).fillna("").astype(str)
        if bool((mask & ~keys.str.startswith(_FAMILY_KEY_PREFIX)).any()):
            _fail("evidence_family_fact_marker_key_mismatch")
        if bool((~mask & keys.str.startswith(_FAMILY_KEY_PREFIX)).any()):
            _fail("evidence_untyped_family_fact")
        if bool(
            (
                mask
                & ~keys.str.casefold().str.startswith(
                    f"legacy.family.{report_family}."
                )
            ).any()
        ):
            _fail("evidence_family_fact_report_family_mismatch")
    if sheet_name not in {"Metric_Lineage", "Evidence_Links"}:
        return mask

    parity_markers = (
        frame.get(_CROSS_FAMILY_PARITY_COLUMN, pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
    )
    unknown_markers = parity_markers.loc[
        ~parity_markers.isin({"", _FAMILY_PRESENTATION_MARKER})
    ]
    if not unknown_markers.empty:
        _fail("unknown_cross_family_parity_marker")
    presentation_mask = parity_markers.eq(_FAMILY_PRESENTATION_MARKER)
    keys = (
        frame.get(
            "Metric_Key" if sheet_name == "Metric_Lineage" else "Evidence_Key",
            pd.Series("", index=frame.index),
        )
        .fillna("")
        .astype(str)
        .str.strip()
    )
    account_rows = keys.str.match(_ACCOUNT_REVIEW_KEY_RE)
    member_rows = keys.str.match(_MEMBER_INTERVENTION_KEY_RE)
    typed_keys = account_rows | member_rows
    if bool((presentation_mask & ~typed_keys).any()):
        _fail("family_presentation_marker_key_mismatch")
    if bool((~presentation_mask & typed_keys).any()):
        _fail("untyped_family_presentation_fact")
    if bool((presentation_mask & mask).any()):
        _fail("conflicting_family_fact_markers")

    if sheet_name == "Metric_Lineage":
        functions = frame.get(
            "Canonical_Function", pd.Series("", index=frame.index)
        ).fillna("").astype(str).str.strip()
        units = frame.get("Unit", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip().str.casefold()
        groupings = (
            frame.get("Grouping", pd.Series("", index=frame.index))
            .fillna("")
            .astype(str)
            .str.strip()
            .str.casefold()
        )
        account_valid = (
            account_rows
            & functions.eq(
                "decision_report_delivery._comprehensive_account_evidence_rows"
            )
            & units.eq("review focus")
            & groupings.eq("account")
        )
        member_valid = (
            member_rows
            & functions.eq("decision_report_delivery._leader_intervention_rows")
            & units.eq("manager intervention")
            & groupings.eq("team member")
        )
    else:
        roles = frame.get("Evidence_Role", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
        types = frame.get("Evidence_Type", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
        sheets = frame.get("Source_Sheet", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
        units = frame.get("Unit", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip().str.casefold()
        account_valid = (
            account_rows
            & roles.eq("derived_summary_row")
            & types.eq("summary")
            & sheets.eq("Account_Summary")
            & units.eq("review focus")
        )
        member_valid = (
            member_rows
            & roles.eq("derived_summary_row")
            & types.eq("summary")
            & sheets.eq("Member_Summary")
            & units.eq("manager intervention")
        )
    if bool((presentation_mask & ~(account_valid | member_valid)).any()):
        _fail("family_presentation_marker_contract_mismatch")
    if bool((presentation_mask & account_rows).any()) and report_family != "comprehensive":
        _fail("account_presentation_fact_wrong_report_family")
    if bool((presentation_mask & member_rows).any()) and report_family != "leader":
        _fail("member_presentation_fact_wrong_report_family")
    mask = mask | presentation_mask
    return mask


def _shared_frame(
    frame: pd.DataFrame,
    sheet_name: str,
    *,
    report_family: str,
) -> pd.DataFrame:
    shared = frame.loc[
        ~_family_row_mask(
            frame,
            sheet_name,
            report_family=report_family,
        )
    ].copy()
    if sheet_name == "Subscriptions":
        unknown_legacy_columns = {
            str(column)
            for column in shared.columns
            if str(column).startswith("Legacy_") and str(column) not in _SUBSCRIPTION_FAMILY_COLUMNS
        }
        if unknown_legacy_columns:
            _fail("subscriptions_unknown_family_fact_column")
        shared = shared.drop(
            columns=[column for column in shared.columns if str(column) in _SUBSCRIPTION_FAMILY_COLUMNS],
            errors="ignore",
        )
    return shared


def _state_values(frame: pd.DataFrame, report_state: str) -> list[str]:
    states = [report_state] if report_state else []
    for column in frame.columns:
        column_name = str(column)
        if not (
            column_name == "Source_State"
            or column_name.endswith("_Source_State")
            or column_name in {"Coverage_State", "Trend_State"}
        ):
            continue
        states.extend(
            _text(value).casefold()
            for value in frame[column].tolist()
            if _text(value)
        )
    invalid = sorted({state for state in states if state not in _ALLOWED_SOURCE_STATES})
    if invalid:
        _fail("invalid_source_state")
    return sorted(states)


def _sheet_signature(
    frame: pd.DataFrame,
    *,
    sheet_name: str,
    report_state: str,
    scope_type: str,
    scope_value: str,
    report_family: str,
) -> dict[str, Any]:
    shared = _shared_frame(
        frame,
        sheet_name,
        report_family=report_family,
    )
    normalized_rows: list[str] = []
    identities: list[str] = []
    attribution_by_identity: dict[str, set[str]] = {}
    columns = sorted(str(column) for column in shared.columns)
    for _, series in shared.iterrows():
        row = {str(column): series.get(column) for column in shared.columns}
        if "Scope_Type" in row:
            row_scope_type = _text(row.get("Scope_Type")).casefold()
            if row_scope_type != scope_type:
                _fail("row_scope_type_mismatch")
            row["Scope_Type"] = scope_type
        if "Scope_Value" in row:
            row_scope_value = _text(row.get("Scope_Value")).casefold()
            if row_scope_value != scope_value:
                _fail("row_scope_value_mismatch")
            # Never retain a manager/member/customer/subscription label in the
            # returned signature, even transiently in a normalized row.
            row["Scope_Value"] = _sha256_json(scope_value)
        identity = _row_identity(sheet_name, row)
        identities.append(identity)
        if identity:
            attribution_text = _text(row.get("Attributed_Team_Members"))
            labels = {
                token.strip().casefold()
                for token in re.split(r"\s*(?:;|\|)\s*", attribution_text)
                if token.strip()
            }
            attribution_by_identity.setdefault(identity, set()).update(labels)
        normalized = {column: _digest_cell(row.get(column)) for column in columns}
        normalized_rows.append(json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str))

    nonblank_identities = [identity for identity in identities if identity]
    distinct_identities = sorted(set(nonblank_identities))
    attribution_lines = [
        f"{identity}\t{';'.join(sorted(attribution_by_identity.get(identity, set())))}"
        for identity in distinct_identities
    ]
    state_values = _state_values(shared, report_state)
    return {
        "count": len(distinct_identities),
        "row_count": len(shared),
        "missing_identity_count": len(identities) - len(nonblank_identities),
        "duplicate_identity_count": len(nonblank_identities) - len(distinct_identities),
        "identity_sha256": hashlib.sha256("\n".join(distinct_identities).encode("utf-8")).hexdigest(),
        "semantic_sha256": _sha256_json({"columns": columns, "rows": sorted(normalized_rows)}),
        "attribution_sha256": hashlib.sha256("\n".join(attribution_lines).encode("utf-8")).hexdigest(),
        "attributed_record_count": sum(bool(attribution_by_identity.get(identity)) for identity in distinct_identities),
        "source_state": report_state,
        "source_state_sha256": _sha256_json(state_values),
    }


def _strict_report_info(
    frame: pd.DataFrame,
) -> tuple[
    dict[str, Any],
    dict[str, str],
    dict[str, str],
    dict[str, str],
]:
    if list(frame.columns) != ["Item", "Value", "Detail"]:
        _fail("report_info_schema_mismatch")
    items = [_text(value) for value in frame["Item"].tolist()]
    if not items or any(not item for item in items):
        _fail("report_info_blank_item")
    folded = [item.casefold() for item in items]
    if len(folded) != len(set(folded)):
        _fail("report_info_duplicate_item")
    values = {item: frame.iloc[index].get("Value") for index, item in enumerate(items)}

    required = {
        "Report_Type",
        "Fact_Contract_SHA256",
        *_REPORT_INFO_REQUIRED_CANONICAL_ITEMS,
        *(f"Sheet_SHA256:{name}" for name in PARITY_SHEET_NAMES if name != "Report_Info"),
        *(f"Source_State:{name}" for name in _SOURCE_STATE_SHEETS),
    }
    if not required.issubset(values):
        _fail("report_info_required_item_missing")
    sheet_hash_items = {item for item in values if item.startswith("Sheet_SHA256:")}
    expected_sheet_hash_items = {
        f"Sheet_SHA256:{name}" for name in PARITY_SHEET_NAMES if name != "Report_Info"
    }
    if sheet_hash_items != expected_sheet_hash_items:
        _fail("report_info_sheet_hash_inventory_mismatch")
    source_state_items = {item for item in values if item.startswith("Source_State:")}
    expected_source_state_items = {f"Source_State:{name}" for name in _SOURCE_STATE_SHEETS}
    if source_state_items != expected_source_state_items:
        _fail("report_info_source_state_inventory_mismatch")
    for item in {"Fact_Contract_SHA256", *expected_sheet_hash_items}:
        if not _HEX_SHA256_RE.fullmatch(_text(values.get(item))):
            _fail("report_info_invalid_sha256")

    report_family = _report_family(values.get("Report_Type"))
    manager = _text(values.get("Manager"))
    technology = _text(values.get("Technology"))
    scope_type = _text(values.get("Scope_Type")).casefold()
    raw_scope_value = _text(values.get("Scope_Value"))
    if not manager or not technology:
        _fail("report_info_scope_metadata_missing")
    if scope_type not in _ALLOWED_SCOPE_TYPES:
        _fail("report_info_scope_type_invalid")
    if not raw_scope_value:
        _fail("report_info_scope_value_missing")
    try:
        days_number = float(values.get("Days"))
    except (TypeError, ValueError):
        _fail("report_info_days_invalid")
    if not math.isfinite(days_number) or not days_number.is_integer() or not 1 <= days_number <= 365:
        _fail("report_info_days_invalid")

    data_as_of_state = _text(values.get("Data_As_Of_State")).casefold()
    if data_as_of_state not in _ALLOWED_DATA_AS_OF_STATES:
        _fail("report_info_data_as_of_state_invalid")
    data_as_of = _utc_text(
        values.get("Data_As_Of_UTC"),
        required=data_as_of_state in {"available", "stale"},
        kind="report_info_data_as_of_invalid",
    )
    evaluation_as_of = _utc_text(
        values.get("Evaluation_As_Of_UTC"),
        required=True,
        kind="report_info_evaluation_as_of_invalid",
    )
    retrieval_attempted = _utc_text(
        values.get("Retrieval_Attempted_At_UTC"),
        kind="report_info_retrieval_attempted_invalid",
    )
    if data_as_of and pd.Timestamp(data_as_of) > pd.Timestamp(evaluation_as_of):
        _fail("report_info_future_data_as_of")
    data_mode = _text(values.get("Data_Mode")).casefold()
    if data_mode not in _ALLOWED_DATA_MODES:
        _fail("report_info_data_mode_invalid")
    live_validation = _text(values.get("Live_Source_Validation")).casefold()
    if live_validation not in _ALLOWED_LIVE_VALIDATION:
        _fail("report_info_live_validation_invalid")

    source_states = {
        name: _text(values.get(f"Source_State:{name}")).casefold()
        for name in _SOURCE_STATE_SHEETS
    }
    if any(state not in _ALLOWED_SOURCE_STATES for state in source_states.values()):
        _fail("report_info_source_state_invalid")
    warning_count = values.get("Partial_Data_Warning_Count")
    if type(warning_count) is not int or warning_count < 0:
        _fail("report_info_warning_count_invalid")
    warning_items = {
        item
        for item in values
        if re.fullmatch(r"Partial_Data_Warning_[1-9]\d*", item)
    }
    expected_warning_items = {
        f"Partial_Data_Warning_{index}"
        for index in range(1, warning_count + 1)
    }
    if warning_items != expected_warning_items:
        _fail("report_info_warning_inventory_mismatch")
    scope_value = raw_scope_value.casefold()
    normalized_manager = manager.casefold()
    normalized_technology = technology.casefold()
    scope_contract = {
        "manager": normalized_manager,
        "technology": normalized_technology,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "days": str(int(days_number)),
        "data_mode": data_mode,
        "live_source_validation": live_validation,
    }
    metadata = {
        "report_family": report_family,
        "scope_contract_sha256": _sha256_json(scope_contract),
        "scope_type": scope_type,
        "days": str(int(days_number)),
        "data_as_of_utc": data_as_of,
        "data_as_of_state": data_as_of_state,
        "retrieval_attempted_at_utc": retrieval_attempted,
        "evaluation_as_of_utc": evaluation_as_of,
        "data_mode": data_mode,
        "live_source_validation": live_validation,
    }
    rows_by_item = {
        item: frame.iloc[index]
        for index, item in enumerate(items)
    }
    excluded_items = {
        "Report_Type",
        "Fact_Contract_SHA256",
        *expected_sheet_hash_items,
    }
    shared: dict[str, dict[str, str]] = {}
    for item in items:
        if item in excluded_items:
            continue
        raw_value = values[item]
        if item == "Manager":
            safe_value = _sha256_json(normalized_manager)
        elif item == "Technology":
            safe_value = _sha256_json(normalized_technology)
        elif item == "Scope_Value":
            safe_value = _sha256_json(scope_value)
        elif item == "Scope_Type":
            safe_value = scope_type
        elif item == "Days":
            safe_value = metadata["days"]
        elif item == "Data_As_Of_State":
            safe_value = data_as_of_state
        elif item.startswith("Source_State:"):
            safe_value = source_states[item.removeprefix("Source_State:")]
        elif item == "Data_Mode":
            safe_value = data_mode
        elif item == "Live_Source_Validation":
            safe_value = live_validation
        elif item.endswith("_UTC"):
            safe_value = "<present>" if _text(raw_value) else ""
        else:
            safe_value = _sha256_json(_digest_cell(raw_value))
        shared[item] = {
            "value": safe_value,
            "detail_sha256": _sha256_json(
                _text(rows_by_item[item].get("Detail"))
            ),
        }
    declared_hashes = {
        name: _text(values[f"Sheet_SHA256:{name}"]).casefold()
        for name in PARITY_SHEET_NAMES
        if name != "Report_Info"
    }
    return metadata, shared, declared_hashes, {
        "scope_type": scope_type,
        "scope_value": scope_value,
    }


def _report_info_signature(
    shared: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    identities = sorted(shared)
    state_values = sorted(
        str(value.get("value") or "")
        for key, value in shared.items()
        if key == "Data_As_Of_State" or key.startswith("Source_State:")
    )
    semantic = [
        {
            "Item": key,
            "Value": dict(shared[key]),
        }
        for key in identities
    ]
    return {
        "count": len(identities),
        "row_count": len(identities),
        "missing_identity_count": 0,
        "duplicate_identity_count": 0,
        "identity_sha256": hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest(),
        "semantic_sha256": _sha256_json(semantic),
        "attribution_sha256": hashlib.sha256(b"").hexdigest(),
        "attributed_record_count": 0,
        "source_state": shared["Data_As_Of_State"]["value"],
        "source_state_sha256": _sha256_json(state_values),
    }


def _typed_keys(
    frame: pd.DataFrame,
    *,
    key_column: str,
    mask: pd.Series,
    kind: str,
) -> set[str]:
    keys = (
        frame.get(key_column, pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    selected = keys.loc[mask].tolist()
    folded = [value.casefold() for value in selected]
    if any(not value for value in selected):
        _fail(f"{kind}_blank_key")
    if len(folded) != len(set(folded)):
        _fail(f"{kind}_duplicate_key")
    return set(folded)


def _validate_typed_exclusion_references(
    frames: Mapping[str, pd.DataFrame],
    *,
    report_family: str,
) -> None:
    """Bind every excluded fact to its exact family and evidence twin."""

    subscriptions = frames["Subscriptions"]
    lineage = frames["Metric_Lineage"]
    evidence = frames["Evidence_Links"]
    subscription_mask = _family_row_mask(
        subscriptions,
        "Subscriptions",
        report_family=report_family,
    )
    lineage_mask = _family_row_mask(
        lineage,
        "Metric_Lineage",
        report_family=report_family,
    )
    evidence_mask = _family_row_mask(
        evidence,
        "Evidence_Links",
        report_family=report_family,
    )
    lineage_grouping = (
        lineage.get("Grouping", pd.Series("", index=lineage.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
    )
    evidence_roles = (
        evidence.get("Evidence_Role", pd.Series("", index=evidence.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
    )
    lineage_parity = (
        lineage.get(
            _CROSS_FAMILY_PARITY_COLUMN,
            pd.Series("", index=lineage.index),
        )
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
    )
    evidence_parity = (
        evidence.get(
            _CROSS_FAMILY_PARITY_COLUMN,
            pd.Series("", index=evidence.index),
        )
        .fillna("")
        .astype(str)
        .str.strip()
        .str.casefold()
    )
    legacy_subscription_keys = _typed_keys(
        subscriptions,
        key_column="Metric_Key",
        mask=subscription_mask,
        kind="legacy_subscription",
    )
    legacy_lineage_keys = _typed_keys(
        lineage,
        key_column="Metric_Key",
        mask=lineage_grouping.eq("legacy family-specific reported fact"),
        kind="legacy_lineage",
    )
    legacy_evidence_keys = _typed_keys(
        evidence,
        key_column="Evidence_Key",
        mask=evidence_roles.eq(_FAMILY_EVIDENCE_ROLE),
        kind="legacy_evidence",
    )
    if not (
        legacy_subscription_keys
        == legacy_lineage_keys
        == legacy_evidence_keys
    ):
        _fail("legacy_family_fact_cross_sheet_mismatch")

    presentation_lineage_keys = _typed_keys(
        lineage,
        key_column="Metric_Key",
        mask=lineage_parity.eq(_FAMILY_PRESENTATION_MARKER),
        kind="presentation_lineage",
    )
    presentation_evidence_keys = _typed_keys(
        evidence,
        key_column="Evidence_Key",
        mask=evidence_parity.eq(_FAMILY_PRESENTATION_MARKER),
        kind="presentation_evidence",
    )
    if presentation_lineage_keys != presentation_evidence_keys:
        _fail("family_presentation_fact_cross_sheet_mismatch")

    # The combined masks above must be exactly the union of the two typed
    # contracts; this prevents a newly invented exclusion class from silently
    # escaping semantic parity.
    if int(lineage_mask.sum()) != len(
        legacy_lineage_keys | presentation_lineage_keys
    ):
        _fail("lineage_exclusion_inventory_mismatch")
    if int(evidence_mask.sum()) != len(
        legacy_evidence_keys | presentation_evidence_keys
    ):
        _fail("evidence_exclusion_inventory_mismatch")


def build_workbook_parity_signature(
    path: Path | str,
    *,
    allowed_root: Path | str | None = None,
) -> dict[str, Any]:
    """Read one exact canonical workbook and return only safe signatures."""

    try:
        with _bounded_workbook_stream(path, allowed_root=allowed_root) as stream:
            workbook_frames = _type_preserving_workbook_frames(stream)
            report_info = workbook_frames["Report_Info"]
            metadata, shared_info, declared_hashes, row_scope = (
                _strict_report_info(report_info)
            )
            frames = {
                sheet_name: workbook_frames[sheet_name]
                for sheet_name in PARITY_SHEET_NAMES[1:]
            }
            _validate_typed_exclusion_references(
                frames,
                report_family=metadata["report_family"],
            )
            signatures: dict[str, dict[str, Any]] = {
                "Report_Info": _report_info_signature(shared_info)
            }
            source_states = {
                key.removeprefix("Source_State:"): str(
                    value.get("value") or ""
                )
                for key, value in shared_info.items()
                if key.startswith("Source_State:")
            }
            for sheet_name in PARITY_SHEET_NAMES[1:]:
                frame = frames[sheet_name]
                signature = _sheet_signature(
                    frame,
                    sheet_name=sheet_name,
                    report_state=source_states.get(sheet_name, ""),
                    scope_type=row_scope["scope_type"],
                    scope_value=row_scope["scope_value"],
                    report_family=metadata["report_family"],
                )
                if sheet_content_sha256(frame) != declared_hashes[sheet_name]:
                    _fail("sheet_sha256_mismatch")
                signatures[sheet_name] = signature
    except ParityContractError:
        raise
    except Exception:  # noqa: BLE001 - emit a stable kind, never parser details
        raise ParityContractError("workbook_parse_failed") from None
    return {
        "metadata": metadata,
        "signatures": signatures,
        "required_sheets": list(PARITY_SHEET_NAMES),
        "projected_fields": list(PARITY_PROJECTED_FIELDS),
    }


__all__ = [
    "PARITY_PROJECTED_FIELDS",
    "PARITY_SHEET_NAMES",
    "ParityContractError",
    "build_workbook_parity_signature",
    "sheet_content_sha256",
    "validate_ooxml_artifact",
]
