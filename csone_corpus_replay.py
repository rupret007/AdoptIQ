"""Privacy-preserving replay of real CSOne workbook shape and distributions.

The replay uses the production workbook loader, then immediately projects the
result into an allow-listed pseudonymous schema.  It preserves report-relevant
status, severity, technology, date, identifier-missingness, and BEMS/TAC shape
while discarding customer names, emails, free text, and source identifiers.
Nothing in this module is imported by the production application or build.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from data_normalization import classify_case_type
from local_acceptance_lab import LocalAcceptanceBundle, SOURCE_MODE


DATE_COLUMNS = (
    "Date/Time Opened",
    "Date/Time Closed",
)
SAFE_CATEGORY_COLUMNS = (
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
REPRESENTATIVE_POSITIONS = (0.0, 0.5, 1.0)


def discover_corpus_workbooks(corpus_dir: Path) -> list[Path]:
    resolved = corpus_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("CSOne corpus directory is not readable")
    workbooks = sorted(
        path
        for path in resolved.iterdir()
        if path.is_file()
        and path.suffix.casefold() == ".xlsx"
        and not path.name.startswith("~$")
    )
    if not workbooks:
        raise ValueError("CSOne corpus directory contains no xlsx workbooks")
    return workbooks


def representative_workbooks(corpus_dir: Path) -> list[Path]:
    workbooks = discover_corpus_workbooks(corpus_dir)
    indexes = {
        round((len(workbooks) - 1) * position)
        for position in REPRESENTATIVE_POSITIONS
    }
    return [workbooks[index] for index in sorted(indexes)]


def _clean_series(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series("", index=frame.index, dtype="object")
    return frame[name].fillna("").astype(str).str.strip()


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
    working = frame.copy()
    working["__replay_order"] = _stable_order(working)
    severity = _clean_series(working, "Severity").str.casefold().replace("", "missing")
    status = _clean_series(working, "Case Status").str.casefold()
    status = status.where(status.ne(""), "missing")
    technology = _clean_series(working, "Tech.").str.casefold().replace("", "missing")
    id_state = _clean_series(working, "SR Number").ne("").map(
        {True: "identified", False: "missing_id"}
    )
    working["__replay_stratum"] = (
        severity + "|" + status + "|" + technology + "|" + id_state
    )
    groups = list(working.groupby("__replay_stratum", sort=True, dropna=False))
    allocations: dict[str, int] = {}
    for key, group in groups:
        allocations[str(key)] = max(1, round(limit * len(group) / len(working)))
    while sum(allocations.values()) > limit:
        candidates = [key for key, value in allocations.items() if value > 1]
        if not candidates:
            break
        candidate = max(candidates, key=lambda key: allocations[key])
        allocations[candidate] -= 1
    while sum(allocations.values()) < limit:
        candidates = sorted(
            groups,
            key=lambda item: (
                len(item[1]) - allocations.get(str(item[0]), 0),
                str(item[0]),
            ),
            reverse=True,
        )
        candidate = next(
            (
                str(key)
                for key, group in candidates
                if allocations.get(str(key), 0) < len(group)
            ),
            None,
        )
        if candidate is None:
            break
        allocations[candidate] += 1
    selected = [
        group.sort_values("__replay_order", kind="stable").head(
            allocations.get(str(key), 0)
        )
        for key, group in groups
    ]
    return (
        pd.concat(selected, ignore_index=True)
        .sort_values("__replay_order", kind="stable")
        .head(limit)
        .drop(columns=["__replay_order", "__replay_stratum"])
        .reset_index(drop=True)
    )


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
    return {
        column: values + shift
        for column, values in parsed.items()
    }


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
    # Derive this while the production evidence fields still exist, then
    # discard every raw narrative field.  Only the three-valued, non-sensitive
    # classification survives.  The pseudonymous title below encodes that
    # class so the normal production lifecycle normalizer must independently
    # reproduce the same result during report generation.
    source_case_types = sampled.apply(classify_case_type, axis=1)
    source_keys = _source_key(sampled)
    order_hash = source_keys.map(
        lambda value: int(
            hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:12],
            16,
        )
    )
    slots = (
        ("ACC-001", "Acme Corporation", "Alex Rivera", "SUB-001"),
        ("ACC-002", "Beta Industries", "Alex Rivera", "SUB-002"),
        ("ACC-001", "Acme Corporation", "Morgan Lee", "SUB-001"),
        ("ACC-003", "Gamma Public Sector", "Morgan Lee", "SUB-003"),
    )
    slot_rows = [slots[value % len(slots)] for value in order_hash]
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

    for column in SAFE_CATEGORY_COLUMNS:
        result[column] = _clean_series(sampled, column)
    result["Technology"] = _clean_series(sampled, "Tech.")
    result["Sub Technology"] = _clean_series(sampled, "Sub Technology").where(
        _clean_series(sampled, "Sub Technology").ne(""),
        _clean_series(sampled, "Sub Tech."),
    )
    result["severity"] = result["Severity"]
    result["status"] = result["Case Status"]

    shifted = _shift_dates(sampled, bundle.as_of_utc)
    for column in DATE_COLUMNS:
        result[column] = shifted.get(
            column, pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]")
        )

    technology = result["Technology"].replace("", "Unclassified technology")
    severity = result["Severity"].replace("", "Unclassified severity")
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
    if not (len(technology) == len(safe_case_type_text) == len(severity)):
        raise ValueError("CSOne replay classification columns do not align")
    result["Title"] = [
        f"Pseudonymized {tech} {case_type} ({sev})"
        for tech, case_type, sev in zip(
            technology,
            safe_case_type_text,
            severity,
        )
    ]
    text_presence = {
        "Problem Description": "Pseudonymized problem detail present.",
        "CSE Action Plan": "Pseudonymized action plan present.",
        "Last Cisco Update": "Pseudonymized provider update present.",
        "Resolution Summary": "Pseudonymized resolution detail present.",
        "Problem Details": "Pseudonymized problem evidence present.",
        "Customer Activity": "Pseudonymized customer activity present.",
    }
    for column, replacement_text in text_presence.items():
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
            "raw_values_retained": False,
            "source_row_count": int(len(source)),
            "replay_row_count": int(len(result)),
            "excluded_non_record_rows": int(
                source.attrs.get("excluded_non_record_rows") or 0
            ),
            "case_type_distribution": case_type_distribution,
            "case_type_distribution_reconciled": True,
        }
    )
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
) -> LocalAcceptanceBundle:
    """Replace only TAC/BEMS fixture frames with a real-shape safe replay."""

    newest = discover_corpus_workbooks(corpus_dir)[-1]
    loaded = loader(str(newest))
    tac = pseudonymize_csone_frame(loaded, bundle, max_rows=max_rows)
    bems = tac.loc[tac["Transaction ID"].fillna("").astype(str).str.strip().ne("")].copy()
    bems.attrs.update(tac.attrs)
    bems.attrs.update({"source_dataset": "bems_cases", "primary_key": "Transaction ID"})

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
) -> dict[str, Any]:
    """Run the production loader on oldest/middle/newest without retaining rows."""

    results: list[dict[str, Any]] = []
    for path in representative_workbooks(corpus_dir):
        loaded = loader(str(path))
        ids = _clean_series(loaded, "SR Number")
        substantive = (
            _clean_series(loaded, "Title").ne("")
            | _clean_series(loaded, "Severity").ne("")
            | _clean_series(loaded, "Date/Time Opened").ne("")
        )
        footer_like = int((ids.eq("") & ~substantive).sum())
        results.append(
            {
                "row_count": int(len(loaded)),
                "column_count": int(len(loaded.columns)),
                "excluded_non_record_rows": int(
                    loaded.attrs.get("excluded_non_record_rows") or 0
                ),
                "footer_like_rows_remaining": footer_like,
                "missing_record_id_rows": int(ids.eq("").sum()),
                "schema_sha256": hashlib.sha256(
                    "\n".join(map(str, loaded.columns)).encode("utf-8")
                ).hexdigest(),
            }
        )
    return {
        "representative_workbook_count": len(results),
        "all_nonempty": all(item["row_count"] > 0 for item in results),
        "no_footer_rows_remaining": all(
            item["footer_like_rows_remaining"] == 0 for item in results
        ),
        "consistent_schema": len({item["schema_sha256"] for item in results}) == 1,
        "results": results,
    }
