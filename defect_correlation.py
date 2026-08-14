"""Exact, criteria-scoped correlation of Cisco CSC defects.

This module deliberately has no dependency on the report, Ask AI, or risk
engines.  Those consumers can therefore share one evidence contract without
quietly introducing different matching rules.

Only canonical Cisco software-defect identifiers (``CSC`` + two letters +
five digits) are extracted.  BEMS identifiers are engineering escalations,
not software defects, and can never enter this correlation path.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import math
import re
from typing import Any, Optional


CSC_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])CSC(?P<suffix>[A-Za-z]{2}\d{5})(?![A-Za-z0-9])",
    re.IGNORECASE,
)

DEFAULT_MAX_CORRELATIONS = 200
DEFAULT_MAX_PARENT_RECORDS = 50
DEFAULT_MAX_CONTEXT_BUGS = 100

TAC_SOURCE_SHEET = "TAC_Cases"
ADOPTION_BARRIER_SOURCE_SHEET = "Adoption_Barriers"
EXTERNAL_BUG_SOURCE_SHEET = "External_Bugs"

IdentityResolver = Callable[[Mapping[str, Any], str], Optional[Mapping[str, Any]]]


_CUSTOMER_ID_COLUMNS = (
    "customer_id",
    "CUSTOMER_ID",
    "Customer ID",
    "CUSTOMER_ID_C",
    "CUSTOMER__C",
    "CUSTOMER_UNIQUE_ID",
)
_ACCOUNT_ID_COLUMNS = (
    "account_id",
    "ACCOUNT_ID",
    "Account ID",
    "ACCOUNT_ID_C",
    "ACCOUNT__C",
    "ACCOUNT__C_ID",
    "DSM_ACCOUNT_ID_C",
    "ACCOUNTID",
    "Account Id",
    "AccountId",
    "SALESFORCE_ACCOUNT_ID",
    "SFDC_ACCOUNT_ID",
)
_CUSTOMER_NAME_COLUMNS = (
    "customer_name",
    "CUSTOMER_NAME",
    "Customer Name",
    "Customer",
    "customer_name_norm",
    "ACCOUNT_NAME",
    "Account Name",
    "BU_NAME",
    "RELATED_CUSTOMER__C",
    "CUSTOMER_BU_NAME__C",
)
_TAC_STABLE_ID_COLUMNS = (
    "SR Number",
    "SR_NUMBER",
    "SR_NUMBER_C",
    "SERVICE_REQUEST_NUMBER",
    "Case Number",
    "CASE_NUMBER",
    "case_id",
    "CASE_ID",
    "CASE_ID_C",
    "Record_ID",
    "record_id",
    "ID",
    "id",
)
_AB_STABLE_ID_COLUMNS = (
    "Barrier_ID",
    "BARRIER_ID",
    "Adoption Barrier ID",
    "ADOPTION_BARRIER_ID",
    "BARRIER_ID_C",
    "Record_ID",
    "record_id",
    "ID",
    "id",
)
_TITLE_COLUMNS = (
    "Title",
    "title",
    "SUBJECT_C",
    "Subject",
    "subject",
    "NAME",
    "Name",
    "headline",
)
_EXTERNAL_ID_COLUMNS = (
    "bug_id",
    "BUG_ID",
    "Bug ID",
    "defect_id",
    "DEFECT_ID",
    "Record_ID",
    "record_id",
    "ID",
    "id",
)
_EXTERNAL_STATUS_COLUMNS = ("status", "Status", "STATUS", "bug_status", "BUG_STATUS")
_EXTERNAL_SEVERITY_COLUMNS = (
    "severity",
    "Severity",
    "SEVERITY",
    "bug_severity",
    "BUG_SEVERITY",
)
_EXTERNAL_VERSION_COLUMNS = (
    "version",
    "Version",
    "VERSION",
    "fixed_version",
    "fixed_versions",
    "known_fixed_releases",
    "affected_version",
    "affected_versions",
    "affected_releases",
    "release",
    "releases",
)
_EXTERNAL_SOURCE_COLUMNS = ("source", "Source", "SOURCE", "source_system", "Source_System")


@dataclass
class _RowObservation:
    source_sheet: str
    source_position: int
    source_row_index: str
    stable_id: str
    title: str
    matched_fields: tuple[str, ...]
    csc_ids: tuple[str, ...]
    customer_id: str
    account_id: str
    customer_name: str
    identity_key: str
    association_method: str


def normalize_csc_id(value: Any) -> Optional[str]:
    """Return one canonical CSC ID when ``value`` is exactly that ID.

    Free text containing an ID belongs in :func:`extract_csc_ids`; this exact
    helper intentionally rejects prefixes, suffixes, BEMS IDs, and strings
    containing more than the identifier.
    """

    text = _clean_scalar(value)
    if not text:
        return None
    match = CSC_ID_PATTERN.fullmatch(text)
    if not match:
        return None
    return f"CSC{match.group('suffix').upper()}"


def extract_csc_ids(value: Any) -> tuple[str, ...]:
    """Extract sorted, unique, canonical CSC IDs from an arbitrary value.

    Nested mappings/sequences are walked deterministically.  Only the official
    CSC shape is accepted; in particular, a BEMS reference never becomes a
    software defect even when it happens to contain the letters ``CSC``.
    """

    found: set[str] = set()
    for text in _iter_text(value):
        for match in CSC_ID_PATTERN.finditer(text):
            found.add(f"CSC{match.group('suffix').upper()}")
    return tuple(sorted(found))


def build_defect_correlation_bundle(
    tac_rows: Any,
    adoption_barrier_rows: Any,
    external_bugs: Optional[Sequence[Mapping[str, Any]]],
    *,
    identity_resolver: Optional[IdentityResolver] = None,
    max_correlations: int = DEFAULT_MAX_CORRELATIONS,
    max_parent_records: int = DEFAULT_MAX_PARENT_RECORDS,
    max_context_bugs: int = DEFAULT_MAX_CONTEXT_BUGS,
    tac_source_sheet: str = TAC_SOURCE_SHEET,
    adoption_barrier_source_sheet: str = ADOPTION_BARRIER_SOURCE_SHEET,
    external_bug_source_sheet: str = EXTERNAL_BUG_SOURCE_SHEET,
) -> dict[str, Any]:
    """Build deterministic exact CSC correlations for already-scoped inputs.

    ``tac_rows`` and ``adoption_barrier_rows`` must already be constrained to
    the report/Ask criteria.  This helper never widens scope.  A caller that
    owns a canonical customer graph can provide ``identity_resolver``; it must
    return any of ``customer_id``, ``account_id``, ``customer_name``, and an
    optional ``identity_key``.  Without a resolver, exact IDs win, while an
    exact normalized name can attach an ID-less row to one unambiguous ID.

    Returned keys:

    * ``records``: bounded correlated records, one per customer identity + CSC
      ID, including parent row evidence and verified external metadata.
    * ``unmatched_external_bugs``: bounded public context rows that did not
      exactly match scoped TAC/AB evidence.  They are explicitly context-only.
    * ``coverage``: source availability, row counts, bounds, and truncation.

    No numeric risk weight is returned.  Consumers may use a verified match to
    improve explanation and action context, never as an independent score.
    """

    max_correlations = _positive_bound(max_correlations, "max_correlations")
    max_parent_records = _positive_bound(max_parent_records, "max_parent_records")
    max_context_bugs = _positive_bound(max_context_bugs, "max_context_bugs")

    tac_materialized, tac_coverage = _materialize_rows(tac_rows)
    ab_materialized, ab_coverage = _materialize_rows(adoption_barrier_rows)
    external_materialized, external_coverage = _materialize_rows(external_bugs)

    observations: list[_RowObservation] = []
    observations.extend(
        _observe_internal_rows(
            tac_materialized,
            source_sheet=tac_source_sheet,
            stable_id_columns=_TAC_STABLE_ID_COLUMNS,
            identity_resolver=identity_resolver,
        )
    )
    observations.extend(
        _observe_internal_rows(
            ab_materialized,
            source_sheet=adoption_barrier_source_sheet,
            stable_id_columns=_AB_STABLE_ID_COLUMNS,
            identity_resolver=identity_resolver,
        )
    )
    _associate_idless_rows_by_unique_name(observations)

    # A CSC reference without a resolvable customer identity is still useful
    # source evidence, but it is not an account-level correlation.  Earlier
    # versions grouped every such row under the literal identity ``unknown``
    # and promoted that synthetic account into reports, lineage, and Ask AI.
    # Quarantine those observations here, before grouping, while retaining
    # aggregate-only coverage so callers can disclose the incomplete join.
    unresolved_observations = [
        observation
        for observation in observations
        if observation.identity_key == "unknown"
        or observation.customer_name.casefold() == "unknown"
    ]
    resolved_observations = [
        observation
        for observation in observations
        if observation not in unresolved_observations
    ]
    unresolved_by_source: dict[str, int] = defaultdict(int)
    unresolved_by_reason: dict[str, int] = defaultdict(int)
    for observation in unresolved_observations:
        unresolved_by_source[observation.source_sheet] += 1
        reason = (
            observation.association_method
            if observation.association_method
            in {"canonical_resolution_unavailable", "unknown"}
            else "customer_identity_unavailable"
        )
        unresolved_by_reason[reason] += 1
    unresolved_csc_ids = {
        csc_id
        for observation in unresolved_observations
        for csc_id in observation.csc_ids
    }
    identity_resolution = {
        "state": "partial" if unresolved_observations else ("available" if observations else "zero"),
        "detail": (
            f"{len(unresolved_observations)} CSC-bearing source row(s) were withheld from "
            "account-level correlation because no canonical customer identity was available"
            if unresolved_observations
            else "All CSC-bearing source rows have a usable customer identity"
            if observations
            else "No CSC-bearing source rows required customer identity resolution"
        ),
        "observation_count": len(observations),
        "resolved_observation_count": len(resolved_observations),
        "quarantined_observation_count": len(unresolved_observations),
        "quarantined_csc_id_count": len(unresolved_csc_ids),
        "quarantined_by_source": dict(sorted(unresolved_by_source.items())),
        "quarantined_by_reason": dict(sorted(unresolved_by_reason.items())),
    }

    external_by_id = _index_external_bugs(
        external_materialized,
        source_sheet=external_bug_source_sheet,
    )

    grouped: dict[tuple[str, str], list[_RowObservation]] = defaultdict(list)
    for observation in resolved_observations:
        for csc_id in observation.csc_ids:
            grouped[(observation.identity_key, csc_id)].append(observation)

    sorted_groups = sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))
    records_truncated = len(sorted_groups) > max_correlations
    selected_groups = sorted_groups[:max_correlations]
    any_parent_truncated = False

    coverage = {
        "criteria_scope": "caller_supplied_exact_scope",
        "sources": {
            tac_source_sheet: tac_coverage,
            adoption_barrier_source_sheet: ab_coverage,
            external_bug_source_sheet: external_coverage,
        },
        "identity_resolution": identity_resolution,
        "limits": {
            "max_correlations": max_correlations,
            "max_parent_records_per_correlation": max_parent_records,
            "max_unmatched_external_bugs": max_context_bugs,
        },
        "candidate_correlation_count": len(sorted_groups),
        "returned_correlation_count": len(selected_groups),
        "correlations_truncated": records_truncated,
        "parent_records_truncated": False,
        "unmatched_external_bugs_truncated": False,
    }

    records: list[dict[str, Any]] = []
    # Match status is computed across *all* scoped internal groups, not only
    # the bounded presentation slice.  Otherwise a valid match beyond the
    # output cap would be mislabeled as an unmatched public/context-only bug.
    # Include quarantined observations here.  A matching external bug is not
    # "unmatched" merely because its internal parent row lacked a trustworthy
    # customer join; it must remain withheld rather than reappear as context.
    all_internal_csc_ids = {
        csc_id
        for observation in observations
        for csc_id in observation.csc_ids
    }
    for (_, csc_id), group in selected_groups:
        group.sort(key=lambda item: (item.source_sheet.casefold(), item.source_position, item.stable_id))
        parent_truncated = len(group) > max_parent_records
        any_parent_truncated = any_parent_truncated or parent_truncated
        parent_group = group[:max_parent_records]
        identity = _select_group_identity(group)
        external = external_by_id.get(csc_id)
        parent_records = [_parent_record(item) for item in parent_group]
        source_sheets = sorted({item.source_sheet for item in group}, key=str.casefold)
        provenance = [
            f"{item.source_sheet}:{item.stable_id}@{item.source_position}"
            for item in parent_group
        ]
        if external:
            provenance.extend(
                f"{external_bug_source_sheet}:{csc_id}@{position}"
                for position in external["source_positions"]
            )

        record = {
            "identity_key": identity["identity_key"],
            "customer_id": identity["customer_id"],
            "account_id": identity["account_id"],
            "customer_name": identity["customer_name"],
            "association_method": identity["association_method"],
            "csc_id": csc_id,
            "parent_source_sheets": source_sheets,
            "parent_records": parent_records,
            "parent_record_count": len(group),
            "parent_records_truncated": parent_truncated,
            "verified_external_match": bool(external),
            "verified_external_bug_id": csc_id if external else "",
            "verified_external_status": external["status"] if external else "",
            "verified_external_severity": external["severity"] if external else "",
            "verified_external_version": external["version"] if external else "",
            "verified_external_title": external["title"] if external else "",
            "verified_external_source_positions": external["source_positions"] if external else [],
            "coverage": coverage,
            "provenance": provenance,
            "action_context": _action_context(csc_id, external),
        }
        records.append(record)

    coverage["parent_records_truncated"] = any_parent_truncated

    unmatched_all = [
        _external_context_record(csc_id, external, external_bug_source_sheet)
        for csc_id, external in sorted(external_by_id.items())
        if csc_id not in all_internal_csc_ids
    ]
    unmatched_truncated = len(unmatched_all) > max_context_bugs
    unmatched_external_bugs = unmatched_all[:max_context_bugs]
    coverage["unmatched_external_bugs_truncated"] = unmatched_truncated
    coverage["unmatched_external_bug_count"] = len(unmatched_all)
    coverage["returned_unmatched_external_bug_count"] = len(unmatched_external_bugs)

    return {
        "records": records,
        "unmatched_external_bugs": unmatched_external_bugs,
        "coverage": coverage,
    }


def correlate_scoped_defects(
    tac_rows: Any,
    adoption_barrier_rows: Any,
    external_bugs: Optional[Sequence[Mapping[str, Any]]],
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Convenience wrapper returning only bounded correlation records."""

    return build_defect_correlation_bundle(
        tac_rows,
        adoption_barrier_rows,
        external_bugs,
        **kwargs,
    )["records"]


def _positive_bound(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _clean_scalar(value: Any, *, max_length: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    try:
        if value is not value:
            return ""
    except Exception:
        pass
    try:
        text = str(value).strip()
    except Exception:
        return ""
    if text.casefold() in {"", "nan", "nat", "none", "<na>"}:
        return ""
    return text[:max_length]


def _iter_text(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key in sorted(value, key=lambda item: str(item).casefold()):
            yield from _iter_text(value[key])
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _iter_text(item)
        return
    text = _clean_scalar(value, max_length=100_000)
    if text:
        yield text


def _mapping_from_row(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "to_dict"):
        try:
            mapped = row.to_dict()
            if isinstance(mapped, Mapping):
                return dict(mapped)
        except Exception:
            return {}
    return {}


def _materialize_rows(rows: Any) -> tuple[list[tuple[Any, dict[str, Any]]], dict[str, Any]]:
    if rows is None:
        return [], {"state": "unavailable", "row_count": None, "detail": "source not supplied"}

    attrs = getattr(rows, "attrs", {}) or {}
    fetch_error = _clean_scalar(attrs.get("fetch_error") if isinstance(attrs, Mapping) else "")
    materialized: list[tuple[Any, dict[str, Any]]] = []
    if hasattr(rows, "iterrows"):
        try:
            for index, row in rows.iterrows():
                materialized.append((index, _mapping_from_row(row)))
        except Exception:
            materialized = []
    elif isinstance(rows, Mapping):
        materialized = [(0, dict(rows))]
    else:
        try:
            for index, row in enumerate(rows):
                mapped = _mapping_from_row(row)
                if mapped:
                    materialized.append((index, mapped))
        except (TypeError, ValueError):
            materialized = []

    source_unavailable = bool(attrs.get("source_unavailable")) if isinstance(attrs, Mapping) else False
    is_partial = bool(
        attrs.get("fetch_error_partial") or attrs.get("partial")
    ) if isinstance(attrs, Mapping) else False
    is_stale = bool(
        attrs.get("stale") or attrs.get("is_stale") or attrs.get("_stale_storage")
    ) if isinstance(attrs, Mapping) else False
    if source_unavailable:
        state = "unavailable"
        detail = _clean_scalar(attrs.get("source_unavailable_detail")) or "source unavailable"
    elif fetch_error and not materialized:
        state = "failed"
        detail = fetch_error[:240]
    elif fetch_error or is_partial:
        state = "partial"
        detail = (
            fetch_error
            or _clean_scalar(attrs.get("source_mode_detail"))
            or "partial criteria-scoped source"
        )[:240]
    elif is_stale:
        state = "stale"
        detail = (_clean_scalar(attrs.get("source_mode_detail")) or "source marked stale")[:240]
    elif materialized:
        state = "available"
        detail = "criteria-scoped rows supplied"
    else:
        state = "zero"
        detail = "successful criteria-scoped source returned zero rows"
    return materialized, {"state": state, "row_count": len(materialized), "detail": detail}


def _casefold_lookup(row: Mapping[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for key, value in row.items():
        folded = str(key).strip().casefold()
        if folded and folded not in lookup:
            lookup[folded] = value
    return lookup


def _first_value(row: Mapping[str, Any], candidates: Sequence[str]) -> str:
    lookup = _casefold_lookup(row)
    for candidate in candidates:
        value = _clean_scalar(lookup.get(candidate.casefold()))
        if value:
            return value
    return ""


def _normalize_identity_value(value: Any) -> str:
    return re.sub(r"\s+", " ", _clean_scalar(value)).strip()


def _identity_from_row(
    row: Mapping[str, Any],
    source_sheet: str,
    identity_resolver: Optional[IdentityResolver],
) -> dict[str, str]:
    if identity_resolver is not None:
        candidate = identity_resolver(row, source_sheet)
        if candidate is not None and not isinstance(candidate, Mapping):
            raise TypeError("identity_resolver must return a mapping or None")
        if candidate is None:
            # Supplying a resolver makes that resolver authoritative. Falling
            # back to an unverified raw account/name after it declined the row
            # would publish a plausible-looking but noncanonical identity.
            return {
                "customer_id": "",
                "account_id": "",
                "customer_name": "",
                "identity_key": "unknown",
                "association_method": "canonical_resolution_unavailable",
            }
        resolved: Mapping[str, Any] = candidate
        customer_id = _normalize_identity_value(resolved.get("customer_id"))
        account_id = _normalize_identity_value(resolved.get("account_id"))
        customer_name = _normalize_identity_value(resolved.get("customer_name"))
    else:
        resolved = {}
        customer_id = _first_value(row, _CUSTOMER_ID_COLUMNS)
        account_id = _first_value(row, _ACCOUNT_ID_COLUMNS)
        customer_name = _first_value(row, _CUSTOMER_NAME_COLUMNS)
    supplied_key = _normalize_identity_value(resolved.get("identity_key"))
    if supplied_key:
        identity_key = f"caller:{supplied_key.casefold()}"
        method = "caller_supplied"
    elif account_id:
        identity_key = f"account:{account_id.casefold()}"
        method = "account_id"
    elif customer_id:
        identity_key = f"customer:{customer_id.casefold()}"
        method = "customer_id"
    elif customer_name:
        identity_key = f"name:{customer_name.casefold()}"
        method = "exact_name"
    else:
        identity_key = "unknown"
        method = "unknown"
        customer_name = "Unknown"
    return {
        "customer_id": customer_id,
        "account_id": account_id,
        "customer_name": customer_name or "Unknown",
        "identity_key": identity_key,
        "association_method": method,
    }


def _observe_internal_rows(
    rows: list[tuple[Any, dict[str, Any]]],
    *,
    source_sheet: str,
    stable_id_columns: Sequence[str],
    identity_resolver: Optional[IdentityResolver],
) -> list[_RowObservation]:
    observations: list[_RowObservation] = []
    for position, (row_index, row) in enumerate(rows, start=1):
        ids_by_field: dict[str, tuple[str, ...]] = {}
        for field in sorted(row, key=lambda item: str(item).casefold()):
            csc_ids = extract_csc_ids(row[field])
            if csc_ids:
                ids_by_field[str(field)] = csc_ids
        csc_ids = tuple(sorted({item for values in ids_by_field.values() for item in values}))
        if not csc_ids:
            continue
        stable_id = _first_value(row, stable_id_columns) or f"{source_sheet}:row:{position:06d}"
        identity = _identity_from_row(row, source_sheet, identity_resolver)
        observations.append(
            _RowObservation(
                source_sheet=source_sheet,
                source_position=position,
                source_row_index=_clean_scalar(row_index, max_length=160) or str(position - 1),
                stable_id=stable_id,
                title=_first_value(row, _TITLE_COLUMNS),
                matched_fields=tuple(sorted(ids_by_field, key=str.casefold)),
                csc_ids=csc_ids,
                customer_id=identity["customer_id"],
                account_id=identity["account_id"],
                customer_name=identity["customer_name"],
                identity_key=identity["identity_key"],
                association_method=identity["association_method"],
            )
        )
    return observations


def _associate_idless_rows_by_unique_name(observations: list[_RowObservation]) -> None:
    ids_by_name: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for observation in observations:
        if observation.customer_name == "Unknown":
            continue
        if observation.account_id:
            ids_by_name[observation.customer_name.casefold()].add(
                (f"account:{observation.account_id.casefold()}", "account_id", observation.account_id)
            )
        elif observation.customer_id:
            ids_by_name[observation.customer_name.casefold()].add(
                (f"customer:{observation.customer_id.casefold()}", "customer_id", observation.customer_id)
            )

    for observation in observations:
        if observation.account_id or observation.customer_id or observation.association_method == "caller_supplied":
            continue
        candidates = ids_by_name.get(observation.customer_name.casefold(), set())
        if len(candidates) != 1:
            continue
        identity_key, id_kind, id_value = next(iter(candidates))
        observation.identity_key = identity_key
        observation.association_method = "unique_name_to_id"
        if id_kind == "account_id":
            observation.account_id = id_value
        else:
            observation.customer_id = id_value


def _index_external_bugs(
    rows: list[tuple[Any, dict[str, Any]]],
    *,
    source_sheet: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for position, (_, row) in enumerate(rows, start=1):
        csc_id = normalize_csc_id(_first_value(row, _EXTERNAL_ID_COLUMNS))
        if not csc_id:
            continue
        values = {
            "status": _first_value(row, _EXTERNAL_STATUS_COLUMNS),
            "severity": _first_value(row, _EXTERNAL_SEVERITY_COLUMNS),
            "version": _first_value(row, _EXTERNAL_VERSION_COLUMNS),
            "title": _first_value(row, _TITLE_COLUMNS),
            "source": _first_value(row, _EXTERNAL_SOURCE_COLUMNS) or source_sheet,
        }
        existing = indexed.setdefault(
            csc_id,
            {
                "status": "",
                "severity": "",
                "version": "",
                "title": "",
                "source": source_sheet,
                "source_positions": [],
            },
        )
        for key, value in values.items():
            if value and not existing[key]:
                existing[key] = value
        existing["source_positions"].append(position)
    return indexed


def _select_group_identity(group: Sequence[_RowObservation]) -> dict[str, str]:
    ranked = sorted(
        group,
        key=lambda item: (
            0 if item.association_method == "caller_supplied" else 1,
            0 if item.account_id else 1,
            0 if item.customer_id else 1,
            0 if item.customer_name != "Unknown" else 1,
            item.source_sheet.casefold(),
            item.source_position,
        ),
    )
    winner = ranked[0]
    return {
        "identity_key": winner.identity_key,
        "customer_id": winner.customer_id,
        "account_id": winner.account_id,
        "customer_name": winner.customer_name,
        "association_method": winner.association_method,
    }


def _parent_record(observation: _RowObservation) -> dict[str, Any]:
    return {
        "source_sheet": observation.source_sheet,
        "source_position": observation.source_position,
        "source_row_index": observation.source_row_index,
        "stable_id": observation.stable_id,
        "customer_id": observation.customer_id,
        "account_id": observation.account_id,
        "customer_name": observation.customer_name,
        "title": observation.title,
        "matched_fields": list(observation.matched_fields),
    }


def _action_context(csc_id: str, external: Optional[Mapping[str, Any]]) -> str:
    if not external:
        return (
            f"Confirm {csc_id} in Cisco BST before making status, severity, or version claims; "
            "the scoped customer evidence establishes the reference only."
        )
    status = external.get("status") or "status unavailable"
    severity = external.get("severity") or "severity unavailable"
    version = external.get("version") or "version unavailable"
    return (
        f"Validate the customer remediation plan against verified {csc_id} metadata: "
        f"{status}; {severity}; {version}. Confirm the affected deployment version and owner."
    )


def _external_context_record(
    csc_id: str,
    external: Mapping[str, Any],
    source_sheet: str,
) -> dict[str, Any]:
    return {
        "csc_id": csc_id,
        "status": external.get("status", ""),
        "severity": external.get("severity", ""),
        "version": external.get("version", ""),
        "title": external.get("title", ""),
        "source": external.get("source", source_sheet),
        "source_positions": list(external.get("source_positions", [])),
        "context_only": True,
        "reason": (
            "No exact CSC reference exists in the criteria-scoped TAC or Adoption Barrier rows; "
            "do not infer customer impact."
        ),
    }


__all__ = [
    "ADOPTION_BARRIER_SOURCE_SHEET",
    "CSC_ID_PATTERN",
    "DEFAULT_MAX_CONTEXT_BUGS",
    "DEFAULT_MAX_CORRELATIONS",
    "DEFAULT_MAX_PARENT_RECORDS",
    "EXTERNAL_BUG_SOURCE_SHEET",
    "IdentityResolver",
    "TAC_SOURCE_SHEET",
    "build_defect_correlation_bundle",
    "correlate_scoped_defects",
    "extract_csc_ids",
    "normalize_csc_id",
]
