#!/usr/bin/env python3
"""Round 169 deterministic metamorphic report-truth acceptance gate.

This runner exercises the real canonical fact, delivery, workbook, Word,
identity, defect-correlation, and report-bound Ask AI contracts against the
guarded local acceptance fixture.  Its public JSON is aggregate-only: no
customer/member names, source IDs, source rows, artifact paths, or report text
are emitted.  It never claims live Cisco/Snowflake validation.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import canonical_metrics as metrics  # noqa: E402
import canonical_report_adapter as adapter  # noqa: E402
import decision_report_delivery as delivery  # noqa: E402
import defect_correlation  # noqa: E402
from local_acceptance_lab import build_scenario_bundle  # noqa: E402
from local_acceptance_runtime import _with_fixture_member_attribution  # noqa: E402
import report_source_parity as source_parity  # noqa: E402


SUMMARY_SCHEMA = "round169-metamorphic/v1"
MAX_ACCEPTANCE_SECONDS = 900
MAX_SUMMARY_BYTES = 32_768
CHECK_NAMES = (
    "artifact_invariance",
    "identical_duplicate_invariance",
    "conflicting_duplicate_quarantine",
    "invalid_id_publication_block",
    "identity_quarantine",
    "freshness_truth",
    "scope_isolation",
    "ask_ai_origin_transport",
)
EXPECTED_CASE_COUNTS = {
    "artifact_invariance": 4,
    "identical_duplicate_invariance": 8,
    "conflicting_duplicate_quarantine": 6,
    "invalid_id_publication_block": 2,
    "identity_quarantine": 2,
    "freshness_truth": 4,
    "scope_isolation": 7,
    "ask_ai_origin_transport": 2,
}
SUMMARY_KEYS = frozenset(
    {
        "schema",
        "sanitized",
        "aggregate_only",
        "do_not_commit_artifacts",
        "companion_http_negative_control_required",
        "live_validation_performed",
        "production_accuracy_claimed",
        "all_passed",
        "check_count",
        "passed_count",
        "checks",
    }
)
REPORT_TYPES = (
    "Compact",
    "Comprehensive",
    "Leader",
    "Renewal Portfolio",
)
CORE_KEYS = tuple(delivery._FRAME_KEYS)  # noqa: SLF001 - shared production inventory
ID_COLUMNS = {
    "subscriptions": "SUBSCRIPTION_ID",
    "action_plans": "ID",
    "adoption_barriers": "ID",
    "customer_pulse": "ID",
    "tac_cases": "SR Number",
    "success_priorities": "ID",
}
ACCOUNT_COLUMNS = (
    "ACCOUNT_ID_C",
    "ACCOUNT__C",
    "ACCOUNT_ID",
    "Account ID",
)
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_HEX = frozenset("0123456789abcdef")


class AcceptanceFailure(AssertionError):
    """A privacy-safe acceptance invariant failed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _require(condition: object, code: str) -> None:
    if condition is not True:
        raise AcceptanceFailure(code)


def _validated_max_seconds(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_ACCEPTANCE_SECONDS:
        raise ValueError(
            f"max_seconds must be an integer from 1 through {MAX_ACCEPTANCE_SECONDS}"
        )
    return value


@contextlib.contextmanager
def _acceptance_deadline(seconds: float):
    """Interrupt a hung check on platforms with a main-thread real-time timer.

    The parent Round 146 runner also enforces a process timeout.  This local
    alarm gives the standalone Makefile target the same bounded-failure
    behavior on the macOS/Linux hosts where these native report gates run.
    """

    if not hasattr(signal, "setitimer") or seconds <= 0:
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def _expired(_signum: int, _frame: object) -> None:
        raise AcceptanceFailure("time_budget_exceeded")

    signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, max(float(seconds), 0.001))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    token = str(value or "")
    return len(token) == 64 and all(character in _HEX for character in token)


def _frame_attrs_copy(frame: pd.DataFrame, result: pd.DataFrame) -> pd.DataFrame:
    result.attrs.update(dict(getattr(frame, "attrs", {}) or {}))
    return result


def _fixture_context() -> dict[str, Any]:
    bundle = build_scenario_bundle("multi_manager")
    ownership = bundle.frame("ownership")
    owner_email = {
        str(row["OWNER_NAME"]): str(row["OWNER_EMAIL"])
        for _, row in ownership.iterrows()
    }
    display_by_email = {
        email.strip().casefold(): member for member, email in owner_email.items()
    }
    return {
        "bundle": bundle,
        "ownership": ownership,
        "owner_email": owner_email,
        "display_by_email": display_by_email,
    }


def _production_frames(
    context: Mapping[str, Any],
    *,
    raw_frames: Mapping[str, pd.DataFrame] | None = None,
    shuffle: bool = False,
) -> dict[str, pd.DataFrame]:
    bundle = context["bundle"]
    source = raw_frames or {key: bundle.frame(key) for key in CORE_KEYS}
    prepared: dict[str, pd.DataFrame] = {}
    for offset, key in enumerate(CORE_KEYS):
        original = source[key]
        frame = _with_fixture_member_attribution(
            original,
            context["owner_email"],
        )
        attrs = dict(getattr(original, "attrs", {}) or {})
        frame = frame.drop(
            columns=["LOCAL_ACCEPTANCE_RECORD_ID", "FIXTURE_MEMBER"],
            errors="ignore",
        )
        if key == "tac_cases" and "Date/Time Opened" in frame.columns:
            frame["Date/Time Opened"] = pd.to_datetime(
                frame["Date/Time Opened"],
                errors="coerce",
                utc=True,
                format="mixed",
            )
        if shuffle:
            frame = frame.sample(
                frac=1,
                random_state=169 + offset,
            ).reset_index(drop=True)
            frame = frame.loc[:, list(reversed(frame.columns))]
        frame.attrs.update(attrs)
        prepared[key] = frame
    return prepared


def _team_data(
    context: Mapping[str, Any],
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, dict[str, Any]]:
    projected, _ = adapter._project_scoped_account_attribution(  # noqa: SLF001
        frames,
        member_display_names_by_email=context["display_by_email"],
    )
    return adapter._build_attributed_team_data(  # noqa: SLF001
        projected,
        member_display_names_by_email=context["display_by_email"],
    )


def _external_records(context: Mapping[str, Any], dataset: str) -> list[dict[str, Any]]:
    frame = context["bundle"].frame(dataset).drop(
        columns=["LOCAL_ACCEPTANCE_RECORD_ID"],
        errors="ignore",
    )
    return frame.where(pd.notna(frame), None).to_dict(orient="records")


def _build_facts(
    context: Mapping[str, Any],
    frames: Mapping[str, pd.DataFrame],
    *,
    report_type: str = "Leader",
    scope_type: str = "team",
    scope_value: str = "selected scope",
    manager_name: str = "selected manager",
    technology: str = "",
) -> dict[str, Any]:
    bundle = context["bundle"]
    return delivery.build_report_facts(
        _team_data(context, frames),
        report_type=report_type,
        scope_type=scope_type,
        scope_value=scope_value,
        manager_name=manager_name,
        technology=technology,
        days=90,
        as_of=bundle.as_of_utc,
        data_as_of_utc=bundle.as_of_utc,
        data_as_of_state="available",
        data_mode="Guarded offline fixture",
        live_validation_performed=False,
        external_incidents=_external_records(context, "external_incidents"),
        external_bugs=_external_records(context, "external_bugs"),
    )


def _sheet_digests(sheets: Mapping[str, pd.DataFrame]) -> dict[str, str]:
    return {
        name: delivery._frame_content_digest(frame, sheet_name=name)  # noqa: SLF001
        for name, frame in sheets.items()
    }


def _document_digest(document: Any) -> str:
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    tables = [
        [[cell.text for cell in row.cells] for row in table.rows]
        for table in document.tables
    ]
    return _sha256(
        {
            "paragraphs": paragraphs,
            "tables": tables,
            "inline_shape_count": len(document.inline_shapes),
        }
    )


def _tiny_chart_renderer(_chart_id: str, _rows: pd.DataFrame, target: Path) -> bool:
    target.write_bytes(_TINY_PNG)
    return True


def _artifact_invariance(context: Mapping[str, Any], workdir: Path) -> tuple[int, Any]:
    baseline_frames = _production_frames(context)
    shuffled_frames = _production_frames(context, shuffle=True)
    original_renderer = delivery._render_chart_image  # noqa: SLF001
    family_signatures: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    delivery._render_chart_image = _tiny_chart_renderer  # type: ignore[assignment]  # noqa: SLF001
    try:
        for index, report_type in enumerate(REPORT_TYPES):
            baseline = _build_facts(context, baseline_frames, report_type=report_type)
            shuffled = _build_facts(context, shuffled_frames, report_type=report_type)
            baseline_sheets = delivery.build_source_data_sheets(baseline)
            shuffled_sheets = delivery.build_source_data_sheets(shuffled)
            _require(
                tuple(baseline_sheets) == tuple(delivery.SOURCE_DATA_SHEET_NAMES),
                "artifact_sheet_inventory",
            )
            _require(
                tuple(shuffled_sheets) == tuple(delivery.SOURCE_DATA_SHEET_NAMES),
                "artifact_shuffled_sheet_inventory",
            )
            _require(
                delivery.fact_contract_fingerprint(baseline)
                == delivery.fact_contract_fingerprint(shuffled),
                "artifact_fact_shuffle_drift",
            )
            _require(
                _sheet_digests(baseline_sheets) == _sheet_digests(shuffled_sheets),
                "artifact_sheet_shuffle_drift",
            )

            baseline_doc = delivery.build_concise_word_document(baseline)
            shuffled_doc = delivery.build_concise_word_document(shuffled)
            baseline_word = delivery.validate_word_semantics(baseline, baseline_doc)
            shuffled_word = delivery.validate_word_semantics(shuffled, shuffled_doc)
            _require(baseline_word.get("ok") is True, "artifact_word_semantics")
            _require(shuffled_word.get("ok") is True, "artifact_shuffled_word_semantics")
            _require(
                _document_digest(baseline_doc) == _document_digest(shuffled_doc),
                "artifact_word_shuffle_drift",
            )
            baseline_contract = delivery.validate_cross_artifact_contract(
                baseline,
                baseline_sheets,
                baseline_doc,
            )
            shuffled_contract = delivery.validate_cross_artifact_contract(
                shuffled,
                shuffled_sheets,
                shuffled_doc,
            )
            _require(baseline_contract.get("ok") is True, "artifact_cross_contract")
            _require(shuffled_contract.get("ok") is True, "artifact_shuffled_cross_contract")

            written_signatures: list[dict[str, Any]] = []
            for variant, facts, sheets in (
                ("baseline", baseline, baseline_sheets),
                ("shuffled", shuffled, shuffled_sheets),
            ):
                path = workdir / f"artifact-{index}-{variant}.xlsx"
                delivery.write_source_data_workbook(path, sheets)
                written = delivery.validate_written_source_workbook(path, facts)
                _require(written.get("ok") is True, "artifact_written_contract")
                written_signatures.append(
                    source_parity.build_workbook_parity_signature(path)["signatures"]
                )
            _require(
                written_signatures[0] == written_signatures[1],
                "artifact_written_shuffle_drift",
            )
            family_signatures.append(written_signatures[0])
            evidence.append(
                {
                    "fact": delivery.fact_contract_fingerprint(baseline),
                    "sheets": _sha256(_sheet_digests(baseline_sheets)),
                    "word": _document_digest(baseline_doc),
                    "parity": _sha256(written_signatures[0]),
                }
            )
    finally:
        delivery._render_chart_image = original_renderer  # type: ignore[assignment]  # noqa: SLF001
    _require(
        all(signature == family_signatures[0] for signature in family_signatures),
        "artifact_cross_family_parity",
    )
    return len(REPORT_TYPES), evidence


def _append_exact_duplicates(
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    mutated: dict[str, pd.DataFrame] = {}
    for key, frame in frames.items():
        attrs = dict(getattr(frame, "attrs", {}) or {})
        result = pd.concat([frame, frame.iloc[[0]].copy()], ignore_index=True, sort=False)
        result.attrs.update(attrs)
        mutated[key] = result
    return mutated


def _identical_duplicate_invariance(context: Mapping[str, Any]) -> tuple[int, Any]:
    baseline_frames = _production_frames(context)
    duplicate_frames = _append_exact_duplicates(baseline_frames)
    baseline = _build_facts(context, baseline_frames)
    duplicate = _build_facts(context, duplicate_frames)
    baseline_sheets = delivery.build_source_data_sheets(baseline)
    duplicate_sheets = delivery.build_source_data_sheets(duplicate)
    _require(
        delivery.fact_contract_fingerprint(baseline)
        == delivery.fact_contract_fingerprint(duplicate),
        "identical_duplicate_fact_drift",
    )
    _require(
        _sheet_digests(baseline_sheets) == _sheet_digests(duplicate_sheets),
        "identical_duplicate_sheet_drift",
    )
    _require(
        not any(
            warning.get("kind") == "stable_id_conflict"
            for warning in duplicate.get("partial_data_warnings") or []
        ),
        "identical_duplicate_false_conflict",
    )

    provenance_rows = pd.DataFrame(
        [
            {
                "Record_ID": "SAFE-1",
                "ACCOUNT_ID_C": "ACCOUNT-1",
                "STATUS_C": "Open",
                "DESCRIPTION_C": "",
                "CSSM": "Member A",
                "Attributed_Team_Members": "Member A",
                "QUERY_ID": "query-a",
                "RETRIEVED_AT": "2026-08-03T20:00:00Z",
                "SOURCE_ROW_NUMBER": 2,
            },
            {
                "Record_ID": "SAFE-1",
                "ACCOUNT_ID_C": "ACCOUNT-1",
                "STATUS_C": "Open",
                "DESCRIPTION_C": "Complementary evidence",
                "CSSM": "Member B",
                "Attributed_Team_Members": "Member B",
                "QUERY_ID": "query-b",
                "RETRIEVED_AT": "2026-08-03T20:01:00Z",
                "SOURCE_ROW_NUMBER": 9,
            },
        ]
    )
    reconciled, coverage = metrics.reconcile_stable_id_observations(
        provenance_rows,
        source_label="privacy-safe fixture",
    )
    _require(len(reconciled) == 1, "provenance_fanout_not_coalesced")
    _require(coverage["conflicting_stable_id_count"] == 0, "provenance_false_conflict")
    _require(
        reconciled.iloc[0]["Attributed_Team_Members"] == "Member A; Member B",
        "provenance_attribution_lost",
    )
    substantive = provenance_rows.copy()
    substantive.loc[1, "STATUS_C"] = "Closed"
    quarantined, conflict = metrics.reconcile_stable_id_observations(
        substantive,
        source_label="privacy-safe fixture",
    )
    _require(quarantined.empty, "substantive_conflict_published")
    _require(conflict["conflicting_stable_id_count"] == 1, "substantive_conflict_missed")
    _require("SAFE-1" not in json.dumps(conflict, sort_keys=True), "conflict_id_leaked")
    return len(CORE_KEYS) + 2, {
        "fact": delivery.fact_contract_fingerprint(baseline),
        "sheets": _sha256(_sheet_digests(baseline_sheets)),
        "compatible": int(coverage["compatible_duplicate_observation_count"]),
        "conflict": int(conflict["conflicting_stable_id_count"]),
    }


def _conflicting_frames(
    frames: Mapping[str, pd.DataFrame],
    *,
    reverse: bool,
) -> tuple[dict[str, pd.DataFrame], tuple[str, ...]]:
    fields = {
        "subscriptions": ("PRODUCT_NAME", "conflicting product"),
        "action_plans": ("STATUS_C", "Completed"),
        "adoption_barriers": ("STATUS_C", "Closed"),
        "customer_pulse": ("SCORE__C", 99),
        "tac_cases": ("Severity", "P4"),
        "success_priorities": ("STATUS_C", "Conflicting"),
    }
    mutated: dict[str, pd.DataFrame] = {}
    raw_ids: list[str] = []
    for offset, (key, frame) in enumerate(frames.items()):
        attrs = dict(getattr(frame, "attrs", {}) or {})
        duplicate = frame.iloc[0].copy()
        raw_ids.append(str(duplicate[ID_COLUMNS[key]]))
        field, value = fields[key]
        duplicate[field] = value
        result = pd.concat([frame, pd.DataFrame([duplicate])], ignore_index=True, sort=False)
        if reverse:
            result = result.sample(
                frac=1,
                random_state=9169 + offset,
            ).reset_index(drop=True)
            result = result.loc[:, list(reversed(result.columns))]
        result.attrs.update(attrs)
        mutated[key] = result
    return mutated, tuple(raw_ids)


def _conflicting_duplicate_quarantine(context: Mapping[str, Any]) -> tuple[int, Any]:
    baseline_frames = _production_frames(context)
    baseline = _build_facts(context, baseline_frames)
    forward_frames, raw_ids = _conflicting_frames(baseline_frames, reverse=False)
    reverse_frames, _ = _conflicting_frames(baseline_frames, reverse=True)
    forward = _build_facts(context, forward_frames)
    reverse = _build_facts(context, reverse_frames)
    forward_sheets = delivery.build_source_data_sheets(forward)
    reverse_sheets = delivery.build_source_data_sheets(reverse)
    baseline_sheets = delivery.build_source_data_sheets(baseline)
    _require(
        delivery.fact_contract_fingerprint(forward)
        == delivery.fact_contract_fingerprint(reverse),
        "conflict_order_fact_drift",
    )
    _require(
        _sheet_digests(forward_sheets) == _sheet_digests(reverse_sheets),
        "conflict_order_sheet_drift",
    )
    _require(
        delivery.validate_cross_artifact_contract(forward, forward_sheets).get("ok") is True,
        "conflict_forward_contract",
    )
    _require(
        delivery.validate_cross_artifact_contract(reverse, reverse_sheets).get("ok") is True,
        "conflict_reverse_contract",
    )
    for key, sheet_name in delivery._FRAME_KEYS.items():  # noqa: SLF001
        _require(
            len(forward_sheets[sheet_name]) == len(baseline_sheets[sheet_name]) - 1,
            "conflict_record_not_quarantined",
        )
        state = forward["source_coverage"].loc[
            forward["source_coverage"]["Source_Sheet"].eq(sheet_name),
            "Source_State",
        ]
        _require(not state.empty and state.iloc[0] == "partial", "conflict_state_not_partial")
    _require(
        len(forward_sheets["BEMS"]) == len(baseline_sheets["BEMS"]) - 1,
        "conflict_bems_not_quarantined",
    )
    _require(
        delivery._frame_content_digest(  # noqa: SLF001
            forward_sheets["Risk_Components"],
            sheet_name="Risk_Components",
        )
        == delivery._frame_content_digest(  # noqa: SLF001
            reverse_sheets["Risk_Components"],
            sheet_name="Risk_Components",
        ),
        "conflict_risk_order_drift",
    )
    warnings = json.dumps(forward.get("partial_data_warnings") or [], sort_keys=True)
    _require(
        sum(
            warning.get("kind") == "stable_id_conflict"
            for warning in forward.get("partial_data_warnings") or []
        )
        == len(CORE_KEYS),
        "conflict_warning_inventory",
    )
    _require(all(raw_id not in warnings for raw_id in raw_ids), "conflict_warning_id_leak")
    return len(CORE_KEYS), {
        "fact": delivery.fact_contract_fingerprint(forward),
        "sheets": _sha256(_sheet_digests(forward_sheets)),
        "warning_count": len(forward.get("partial_data_warnings") or []),
        "bems_count": len(forward_sheets["BEMS"]),
        "risk": delivery._frame_content_digest(  # noqa: SLF001
            forward_sheets["Risk_Components"],
            sheet_name="Risk_Components",
        ),
    }


def _invalid_id_publication_block(
    context: Mapping[str, Any],
    workdir: Path,
) -> tuple[int, Any]:
    evidence: list[Any] = []
    for index, invalid_value in enumerate(("", "AP/INVALID")):
        frames = _production_frames(context)
        attrs = dict(getattr(frames["action_plans"], "attrs", {}) or {})
        frames["action_plans"] = frames["action_plans"].copy()
        frames["action_plans"].loc[frames["action_plans"].index[0], "ID"] = invalid_value
        frames["action_plans"].attrs.update(attrs)
        facts = _build_facts(context, frames)
        sheets = delivery.build_source_data_sheets(facts)
        contract = delivery.validate_cross_artifact_contract(facts, sheets)
        target = workdir / f"invalid-{index}.xlsx"
        if contract.get("ok") is True:
            delivery.write_source_data_workbook(target, sheets)
        _require(contract.get("ok") is False, "invalid_id_contract_false_green")
        _require(not target.exists(), "invalid_id_artifact_written")
        evidence.append(
            {
                "error_count": len(contract.get("errors") or []),
                "written": target.exists(),
            }
        )
    return 2, evidence


def _identity_quarantine(_context: Mapping[str, Any]) -> tuple[int, Any]:
    subscriptions = pd.DataFrame(
        [
            {
                "SUBSCRIPTION_ID": "SUB-A",
                "ACCOUNT_ID_C": "ACCOUNT-A",
                "BU_NAME": "Collision Name",
            },
            {
                "SUBSCRIPTION_ID": "SUB-B",
                "ACCOUNT_ID_C": "ACCOUNT-B",
                "BU_NAME": "Collision Name",
            },
        ]
    )
    ambiguous = pd.DataFrame(
        [{"SR Number": "CASE-A", "Customer": "Collision Name"}]
    )
    identity_frames = {
        "subscriptions": subscriptions,
        "tac_cases": ambiguous,
    }
    identities = delivery._canonical_customer_identities(identity_frames)  # noqa: SLF001
    _require(len(identities) == 2, "identity_collision_merged")
    selected_counts = [
        len(
            delivery._customer_frame_for_identity(  # noqa: SLF001
                ambiguous,
                identity,
                identities,
            )
        )
        for identity in identities
    ]
    _require(selected_counts == [0, 0], "identity_ambiguous_row_attached")

    correlation = defect_correlation.build_defect_correlation_bundle(
        [
            {
                "SR Number": "CASE-UNRESOLVED",
                "Title": "Contains CSCaa12345",
            }
        ],
        [],
        [],
    )
    resolution = correlation["coverage"]["identity_resolution"]
    _require(correlation["records"] == [], "identity_unresolved_correlation_published")
    _require(resolution["state"] == "partial", "identity_unresolved_state")
    _require(
        resolution["quarantined_observation_count"] == 1,
        "identity_unresolved_count",
    )
    _require(
        "CSCaa12345" not in json.dumps(resolution, sort_keys=True),
        "identity_unresolved_id_leak",
    )
    return 2, {
        "identity_count": len(identities),
        "selected_counts": selected_counts,
        "quarantined_count": resolution["quarantined_observation_count"],
    }


def _ask_request(
    grounded: Any,
    *,
    data_as_of_utc: str,
    data_as_of_state: str,
    question: str,
    partial: bool = False,
) -> Any:
    fingerprint = "a" * 64
    exact_group = {
        "evidence_key": "kpi.action_plans_open",
        "label": "Open Action Plans",
        "evidence_type": "metric",
        "metric_value": 2,
        "unit": "records",
        "evidence_roles": ["supporting_record"],
        "source_state": "available",
        "total_records": 2,
        "records": [
            {
                "source_sheet": "Action_Plans",
                "source_row_number": 2,
                "record_id": "RECORD-A",
                "record_id_quality": "OK",
            },
            {
                "source_sheet": "Action_Plans",
                "source_row_number": 3,
                "record_id": "RECORD-B",
                "record_id_quality": "OK",
            },
        ],
        "limitations": [],
        "scope_label": "selected scope",
    }
    bundle = {
        "schema": "report-bound-facts/v2",
        "canonical_snapshot": True,
        "analysis_id": "round169-metamorphic",
        "report_type": "leader",
        "fact_fingerprint": fingerprint,
        "data_as_of_utc": data_as_of_utc,
        "data_as_of_state": data_as_of_state,
        "manager": "selected manager",
        "technology": "All",
        "days": 90,
        "scope_type": "team",
        "scope_value": "",
        "scope_member": "",
        "source_states": {
            "Action_Plans": "available",
            **({"Customer_Pulse": "partial"} if partial else {}),
        },
        # Hostile legacy/LLM-facing projection. Canonical v2 answers must use
        # exact evidence instead and never surface these values or identities.
        "decision_metrics": [
            {
                "metric_key": "hostile.legacy_projection_999",
                "value": 999,
                "record_id": "LEGACY-PROJECTION-ID-999",
            }
        ],
        "evidence_contract": "canonical-evidence-links/v1",
        "exact_evidence": {
            "schema": "report-bound-evidence/v1",
            "evidence_contract": "canonical-evidence-links/v1",
            "fact_fingerprint": fingerprint,
            "data_as_of_utc": data_as_of_utc,
            "truncated": partial,
            "groups": [exact_group],
        },
        "action_plans": [{"record_id": "LEGACY-PROJECTION-ID-999"}],
        "accounts": [{"customer": "LEGACY-PROJECTION-ACCOUNT-999"}],
    }
    return grounded.AskAIRequest(
        question=question,
        manager="selected manager",
        technology="All",
        days=90,
        report_analysis_id="round169-metamorphic",
        report_type="leader",
        data_as_of_utc=data_as_of_utc,
        evaluation_utc="2026-08-03T21:00:00Z",
        fact_fingerprint=fingerprint,
        report_fact_bundle=json.dumps(bundle, sort_keys=True),
    )


def _load_ai_modules() -> tuple[Any, Any]:
    # Some optional enhanced-report imports print a capability notice. Keep
    # the gate's public stdout as one exact JSON document.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        import ask_ai_grounded as grounded  # noqa: PLC0415
        import app_simple  # noqa: PLC0415

    return grounded, app_simple


def _freshness_truth(_context: Mapping[str, Any]) -> tuple[int, Any]:
    grounded, _ = _load_ai_modules()
    cases = (
        (
            "2020-01-01T00:00:00Z",
            "available",
            "How many action plans are currently open?",
            False,
            "stale",
        ),
        (
            "2026-08-04T00:00:00Z",
            "available",
            "How many action plans are currently open?",
            False,
            "future",
        ),
        (
            "2026-08-03T20:00:00Z",
            "unavailable",
            "How many action plans are currently open?",
            False,
            "current",
        ),
        (
            "2020-01-01T00:00:00Z",
            "available",
            "How many action plans were open in this report as of 2020?",
            True,
            "stale",
        ),
    )
    projections: list[Any] = []
    for as_of, state, question, historical, expected_freshness in cases:
        request = _ask_request(
            grounded,
            data_as_of_utc=as_of,
            data_as_of_state=state,
            question=question,
        )
        result = grounded._r146_report_bound_snapshot_answer(  # noqa: SLF001
            request,
            {"scope_type": "team", "report_analysis_id": request.report_analysis_id},
        )
        _require(
            (result.get("snapshot_freshness") or {}).get("state")
            == expected_freshness,
            "freshness_state_mismatch",
        )
        if historical:
            _require(
                result.get("canonical_headline") == {"kpi.action_plans_open": 2},
                "freshness_historical_not_exact",
            )
            _require(
                result.get("retrieval_diag", {}).get("direct_question_answered") is not False,
                "freshness_historical_withheld",
            )
        else:
            _require(result.get("canonical_headline") == {}, "freshness_current_leak")
            _require(result.get("evidence_records") == [], "freshness_evidence_leak")
            _require(
                result.get("retrieval_diag", {}).get("direct_question_answered") is False,
                "freshness_false_answer",
            )
        _require("999" not in json.dumps(result, sort_keys=True), "freshness_hostile_projection")
        projections.append(
            {
                "state": result.get("response_state"),
                "freshness": (result.get("snapshot_freshness") or {}).get("state"),
                "headline_count": len(result.get("canonical_headline") or {}),
                "historical": historical,
            }
        )
    return len(cases), projections


def _filter_by_members(frame: pd.DataFrame, members: set[str]) -> pd.DataFrame:
    if "FIXTURE_MEMBER" not in frame.columns:
        return _frame_attrs_copy(frame, frame.copy())
    selected = frame.loc[
        frame["FIXTURE_MEMBER"].fillna("").astype(str).isin(members)
    ].copy()
    return _frame_attrs_copy(frame, selected.reset_index(drop=True))


def _row_account_tokens(frame: pd.DataFrame) -> pd.Series:
    result = pd.Series("", index=frame.index, dtype="object")
    for column in ACCOUNT_COLUMNS:
        if column not in frame.columns:
            continue
        values = frame[column].fillna("").astype(str).str.strip()
        result.loc[result.eq("") & values.ne("")] = values
    return result


def _filter_by_accounts(frame: pd.DataFrame, accounts: set[str]) -> pd.DataFrame:
    tokens = _row_account_tokens(frame)
    selected = frame.loc[tokens.isin(accounts)].copy()
    return _frame_attrs_copy(frame, selected.reset_index(drop=True))


def _raw_scope_frames(
    context: Mapping[str, Any],
    *,
    members: set[str] | None = None,
    accounts: set[str] | None = None,
    subscription_id: str = "",
    technology: str = "",
) -> dict[str, pd.DataFrame]:
    bundle = context["bundle"]
    scoped: dict[str, pd.DataFrame] = {}
    for key in CORE_KEYS:
        frame = bundle.frame(key)
        if members is not None:
            frame = _filter_by_members(frame, members)
        if accounts is not None:
            frame = _filter_by_accounts(frame, accounts)
        if key == "subscriptions" and subscription_id:
            selected = frame.loc[
                frame["SUBSCRIPTION_ID"].fillna("").astype(str).eq(subscription_id)
            ].copy()
            frame = _frame_attrs_copy(frame, selected.reset_index(drop=True))
        if key == "subscriptions" and technology:
            selected = frame.loc[
                frame["PRODUCT_NAME"].fillna("").astype(str).eq(technology)
            ].copy()
            frame = _frame_attrs_copy(frame, selected.reset_index(drop=True))
        scoped[key] = frame
    return scoped


def _facts_id_sets(facts: Mapping[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for key in CORE_KEYS:
        frame = facts["frames"][key]
        result[key] = {
            str(value).strip()
            for value in frame.get("Record_ID", pd.Series(dtype="object")).tolist()
            if str(value).strip()
        }
    return result


def _scope_isolation(context: Mapping[str, Any]) -> tuple[int, Any]:
    ownership = context["ownership"]
    managers = sorted(str(value) for value in ownership["MANAGER_NAME"].unique())
    _require(len(managers) >= 2, "scope_manager_fixture")
    members_by_manager = {
        manager: set(
            ownership.loc[ownership["MANAGER_NAME"].eq(manager), "OWNER_NAME"].astype(str)
        )
        for manager in managers[:2]
    }
    all_members = set().union(*members_by_manager.values())

    raw_all = _raw_scope_frames(context)
    raw_a = _raw_scope_frames(context, members=members_by_manager[managers[0]])
    raw_b = _raw_scope_frames(context, members=members_by_manager[managers[1]])
    member_name = sorted(members_by_manager[managers[0]])[0]
    raw_member = _raw_scope_frames(context, members={member_name})

    subscriptions = raw_all["subscriptions"]
    selected_subscription = str(subscriptions.iloc[0]["SUBSCRIPTION_ID"])
    selected_account = str(subscriptions.iloc[0]["ACCOUNT_ID_C"])
    selected_customer = str(subscriptions.iloc[0]["BU_NAME"])
    selected_technology = str(subscriptions.iloc[0]["PRODUCT_NAME"])
    technology_accounts = set(
        subscriptions.loc[
            subscriptions["PRODUCT_NAME"].astype(str).eq(selected_technology),
            "ACCOUNT_ID_C",
        ].astype(str)
    )
    raw_customer = _raw_scope_frames(context, accounts={selected_account})
    raw_subscription = _raw_scope_frames(
        context,
        accounts={selected_account},
        subscription_id=selected_subscription,
    )
    raw_technology = _raw_scope_frames(
        context,
        accounts=technology_accounts,
        technology=selected_technology,
    )

    scope_specs = (
        ("manager_a", raw_a, "team", "manager A", managers[0], ""),
        ("manager_b", raw_b, "team", "manager B", managers[1], ""),
        ("all", raw_all, "team", "all managers", "all managers", ""),
        ("member", raw_member, "member", "selected member", managers[0], ""),
        ("customer", raw_customer, "customer", selected_customer, managers[0], ""),
        ("subscription", raw_subscription, "customer", selected_customer, managers[0], ""),
        ("technology", raw_technology, "team", "technology scope", "all managers", selected_technology),
    )
    facts_by_scope: dict[str, dict[str, Any]] = {}
    for label, raw, scope_type, scope_value, manager, technology in scope_specs:
        facts_by_scope[label] = _build_facts(
            context,
            _production_frames(context, raw_frames=raw),
            scope_type=scope_type,
            scope_value=scope_value,
            manager_name=manager,
            technology=technology,
        )
        sheets = delivery.build_source_data_sheets(facts_by_scope[label])
        _require(
            delivery.validate_cross_artifact_contract(facts_by_scope[label], sheets).get("ok")
            is True,
            "scope_cross_artifact_contract",
        )

    ids = {label: _facts_id_sets(facts) for label, facts in facts_by_scope.items()}
    for key in CORE_KEYS:
        _require(
            ids["manager_a"][key] | ids["manager_b"][key] == ids["all"][key],
            "scope_manager_union",
        )
        _require(ids["member"][key] == ids["manager_a"][key], "scope_member_delta")
        for narrow in ("customer", "subscription", "technology"):
            _require(ids[narrow][key] <= ids["all"][key], "scope_narrow_widened")
    _require(
        any(ids["manager_a"][key] - ids["manager_b"][key] for key in CORE_KEYS),
        "scope_manager_a_delta",
    )
    _require(
        any(ids["manager_b"][key] - ids["manager_a"][key] for key in CORE_KEYS),
        "scope_manager_b_delta",
    )
    _require(
        ids["subscription"]["subscriptions"] == {selected_subscription},
        "scope_subscription_exact",
    )
    for label, accounts in (
        ("customer", {selected_account}),
        ("subscription", {selected_account}),
        ("technology", technology_accounts),
    ):
        for key, frame in facts_by_scope[label]["frames"].items():
            if frame.empty:
                continue
            tokens = set(_row_account_tokens(frame).loc[lambda values: values.ne("")])
            _require(tokens <= accounts, "scope_account_bleed")
    _require(all_members == set(context["owner_email"]), "scope_owner_inventory")
    return len(scope_specs), {
        label: {
            key: len(values)
            for key, values in sorted(ids[label].items())
        }
        for label in sorted(ids)
    }


def _ask_ai_origin_transport(_context: Mapping[str, Any]) -> tuple[int, Any]:
    grounded, app = _load_ai_modules()
    sync_source = inspect.getsource(app.ask_ai_portfolio)
    wrapper_source = inspect.getsource(app._r74_run_grounded_for_streaming)  # noqa: SLF001
    stream_source = inspect.getsource(app.ask_ai_portfolio_stream)
    engine_name = "run_portfolio_grounded_ask_ai"
    _require(engine_name in sync_source, "ask_sync_engine_contract")
    _require(engine_name in wrapper_source, "ask_stream_engine_contract")
    _require(
        "_r74_run_grounded_for_streaming" in stream_source,
        "ask_stream_wrapper_contract",
    )

    questions = {
        "complete": "How many action plans are currently open?",
        "partial": "How many action plans are currently open with partial evidence?",
    }
    requests = {
        label: _ask_request(
            grounded,
            data_as_of_utc="2026-08-03T20:00:00Z",
            data_as_of_state="available",
            question=question,
            partial=label == "partial",
        )
        for label, question in questions.items()
    }
    original_enabled = app.is_grounded_ask_ai_enabled
    original_engine = app.run_portfolio_grounded_ask_ai
    original_resolver = app._r146_resolve_ask_ai_context  # noqa: SLF001
    original_throttle = app._check_ask_ai_throttle  # noqa: SLF001
    original_record_diag = app._record_ask_ai_query_diag  # noqa: SLF001
    original_config = {
        key: app.app.config.get(key)
        for key in (
            "TESTING",
            "WTF_CSRF_ENABLED",
            "LOCAL_ACCEPTANCE_MODE",
            "LOCAL_ACCEPTANCE_LIVE_VALIDATION",
            "LOCAL_ACCEPTANCE_AS_OF_UTC",
        )
    }
    calls: list[Any] = []

    def observed_engine(received: Any) -> dict[str, Any]:
        calls.append(received)
        return original_engine(received)

    def resolved_context(data: Mapping[str, Any]) -> tuple[dict[str, Any] | None, None]:
        question = str(data.get("question") or "")
        selected = next(
            (request for request in requests.values() if request.question == question),
            None,
        )
        _require(selected is not None, "ask_route_question_binding")
        return {
            "manager": selected.manager,
            "technology": selected.technology,
            "days": selected.days,
            "scope_type": selected.scope_type,
            "scope_value": selected.scope_value,
            "scope_label": "selected scope",
            "scope_member": selected.scope_member,
            "report_analysis_id": selected.report_analysis_id,
            "report_type": selected.report_type,
            "data_as_of_utc": selected.data_as_of_utc,
            "fact_fingerprint": selected.fact_fingerprint,
            "report_fact_bundle": selected.report_fact_bundle,
        }, None

    def parse_stream(value: str) -> dict[str, Any]:
        events: dict[str, list[dict[str, Any]]] = {}
        for block in re.split(r"\n\n+", value.replace("\r\n", "\n")):
            event_name = "message"
            data_lines: list[str] = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].lstrip())
            if data_lines:
                decoded = json.loads("\n".join(data_lines))
                _require(isinstance(decoded, dict), "ask_stream_event_mapping")
                events.setdefault(event_name, []).append(decoded)
        _require(not events.get("error"), "ask_stream_error_event")
        _require(len(events.get("meta") or []) == 1, "ask_stream_meta_inventory")
        _require(len(events.get("done") or []) == 1, "ask_stream_done_inventory")
        return {
            "meta": events["meta"][0],
            "done": events["done"][0],
            "answer": "".join(
                str(item.get("chunk") or "") for item in events.get("data") or []
            ),
        }

    def citation_ids(answer: object) -> set[str]:
        found: set[str] = set()
        for group in re.findall(r"\[Sources:\s*([^\]]+)\]", str(answer or "")):
            found.update(item.strip() for item in group.split(",") if item.strip())
        return found

    try:
        app.is_grounded_ask_ai_enabled = lambda: True
        app.run_portfolio_grounded_ask_ai = observed_engine
        app._r146_resolve_ask_ai_context = resolved_context  # noqa: SLF001
        app._check_ask_ai_throttle = lambda: None  # noqa: SLF001
        app._record_ask_ai_query_diag = lambda *_args, **_kwargs: None  # noqa: SLF001
        app.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,
            LOCAL_ACCEPTANCE_MODE=True,
            LOCAL_ACCEPTANCE_LIVE_VALIDATION=False,
            LOCAL_ACCEPTANCE_AS_OF_UTC="2026-08-03T21:00:00Z",
        )
        client = app.app.test_client()
        route_results: dict[str, dict[str, Any]] = {}
        for label, question in questions.items():
            body = {
                "question": question,
                "report_analysis_id": "round169-metamorphic",
                "report_context_mode": "bound",
            }
            sync_response = client.post("/api/ask-ai-portfolio", json=body)
            stream_response = client.post("/api/ask-ai-portfolio/stream", json=body)
            sync_payload = sync_response.get_json(silent=True)
            _require(sync_response.status_code == 200, "ask_sync_http_status")
            _require(isinstance(sync_payload, dict), "ask_sync_payload_mapping")
            stream_payload = parse_stream(stream_response.get_data(as_text=True))
            _require(stream_response.status_code == 200, "ask_stream_http_status")

            meta = stream_payload["meta"]
            done = stream_payload["done"]
            expected_state = "partial" if label == "partial" else "ok"
            expected_confidence = "Low" if label == "partial" else "High"
            for payload in (sync_payload, meta, done):
                _require(
                    payload.get("response_state") == expected_state,
                    "ask_transport_response_state",
                )
                _require(
                    (payload.get("confidence") or {}).get("level")
                    == expected_confidence,
                    "ask_transport_confidence",
                )
            expected_headline = {"kpi.action_plans_open": 2}
            _require(sync_payload.get("ok") is True, "ask_sync_ok")
            _require(sync_payload.get("mode") == "grounded", "ask_sync_mode")
            _require(
                sync_payload.get("canonical_headline") == expected_headline,
                "ask_sync_canonical",
            )
            _require(
                meta.get("canonical_headline") == expected_headline,
                "ask_stream_canonical",
            )
            sync_ids = {
                str(item.get("source_id") or "")
                for item in sync_payload.get("evidence_records") or []
                if isinstance(item, Mapping)
            }
            stream_ids = {
                str(item.get("source_id") or "")
                for item in meta.get("evidence_records") or []
                if isinstance(item, Mapping)
            }
            index_ids = {
                str(item.get("source_id") or "")
                for item in sync_payload.get("evidence_index") or []
                if isinstance(item, Mapping)
            }
            _require(sync_ids == stream_ids == index_ids, "ask_transport_evidence_parity")
            _require(bool(sync_ids), "ask_transport_evidence_inventory")
            _require(
                citation_ids(sync_payload.get("answer"))
                == citation_ids(stream_payload["answer"])
                <= sync_ids,
                "ask_transport_citation_parity",
            )
            _require(
                bool(citation_ids(sync_payload.get("answer"))),
                "ask_transport_citation_inventory",
            )
            serialized = json.dumps(
                {"sync": sync_payload, "stream": stream_payload},
                sort_keys=True,
            )
            for hostile in (
                "999",
                "LEGACY-PROJECTION-ID",
                "LEGACY-PROJECTION-ACCOUNT",
            ):
                _require(hostile not in serialized, "ask_hostile_projection_origin")
            if label == "partial":
                _require(
                    bool(sync_payload.get("partial_data_warnings")),
                    "ask_partial_warning_inventory",
                )
                _require(
                    meta.get("partial_data_warnings")
                    == sync_payload.get("partial_data_warnings"),
                    "ask_partial_warning_transport",
                )
            route_results[label] = {
                "state": expected_state,
                "evidence_count": len(sync_ids),
                "citation_count": len(citation_ids(sync_payload.get("answer"))),
            }
    finally:
        app.is_grounded_ask_ai_enabled = original_enabled
        app.run_portfolio_grounded_ask_ai = original_engine
        app._r146_resolve_ask_ai_context = original_resolver  # noqa: SLF001
        app._check_ask_ai_throttle = original_throttle  # noqa: SLF001
        app._record_ask_ai_query_diag = original_record_diag  # noqa: SLF001
        for key, value in original_config.items():
            if value is None:
                app.app.config.pop(key, None)
            else:
                app.app.config[key] = value
    _require(len(calls) == 4, "ask_transport_runtime_engine_inventory")
    _require(
        sorted(received.question for received in calls)
        == sorted([*questions.values(), *questions.values()]),
        "ask_transport_runtime_engine_binding",
    )
    return 2, {
        "static": _sha256(
            {
                "sync": engine_name in sync_source,
                "wrapper": engine_name in wrapper_source,
                "stream": "_r74_run_grounded_for_streaming" in stream_source,
            }
        ),
        "runtime_calls": len(calls),
        "transport_count": 2,
        "variant_count": len(route_results),
        "route_digest": _sha256(route_results),
    }


def _run_one(
    name: str,
    function: Callable[..., tuple[int, Any]],
    context: Mapping[str, Any],
    workdir: Path,
) -> dict[str, Any]:
    try:
        if name in {"artifact_invariance", "invalid_id_publication_block"}:
            cases, evidence = function(context, workdir)
        else:
            cases, evidence = function(context)
        passed = True
        code = ""
    except AcceptanceFailure as exc:
        cases, evidence, passed, code = 0, {"code": exc.code}, False, exc.code
    except Exception as exc:  # noqa: BLE001 - public failure stays type-only
        cases, evidence, passed, code = 0, {"code": type(exc).__name__}, False, type(exc).__name__
    result = {
        "passed": passed,
        "cases": int(cases),
        "digest": _sha256(evidence),
    }
    if code:
        result["failure_code"] = code
    return result


def run_acceptance(*, max_seconds: int = 180) -> dict[str, Any]:
    max_seconds = _validated_max_seconds(max_seconds)
    started = time.monotonic()
    context = _fixture_context()
    functions: dict[str, Callable[..., tuple[int, Any]]] = {
        "artifact_invariance": _artifact_invariance,
        "identical_duplicate_invariance": _identical_duplicate_invariance,
        "conflicting_duplicate_quarantine": _conflicting_duplicate_quarantine,
        "invalid_id_publication_block": _invalid_id_publication_block,
        "identity_quarantine": _identity_quarantine,
        "freshness_truth": _freshness_truth,
        "scope_isolation": _scope_isolation,
        "ask_ai_origin_transport": _ask_ai_origin_transport,
    }
    checks: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="adoptiq-round169-") as raw_dir:
        workdir = Path(raw_dir)
        for name in CHECK_NAMES:
            remaining = max_seconds - (time.monotonic() - started)
            if remaining <= 0:
                checks[name] = {
                    "passed": False,
                    "cases": 0,
                    "digest": _sha256({"code": "time_budget_exceeded"}),
                    "failure_code": "time_budget_exceeded",
                }
                continue
            with _acceptance_deadline(remaining):
                checks[name] = _run_one(name, functions[name], context, workdir)
    passed_count = sum(check["passed"] is True for check in checks.values())
    summary = {
        "schema": SUMMARY_SCHEMA,
        "sanitized": True,
        "aggregate_only": True,
        "do_not_commit_artifacts": True,
        # This in-process gate proves canonical publication preconditions and
        # no local write. The existing loopback HTTP negative-control remains
        # the required companion proof for no download/history side effects.
        "companion_http_negative_control_required": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
        "all_passed": passed_count == len(CHECK_NAMES),
        "check_count": len(CHECK_NAMES),
        "passed_count": passed_count,
        "checks": checks,
    }
    return summary


def validate_summary(summary: Mapping[str, Any]) -> list[str]:
    """Strict projector for parent acceptance orchestration."""

    errors: list[str] = []
    if set(summary) != SUMMARY_KEYS:
        errors.append("summary_inventory")
    if summary.get("schema") != SUMMARY_SCHEMA:
        errors.append("schema")
    for key in (
        "sanitized",
        "aggregate_only",
        "do_not_commit_artifacts",
        "companion_http_negative_control_required",
    ):
        if summary.get(key) is not True:
            errors.append(key)
    for key in ("live_validation_performed", "production_accuracy_claimed"):
        if summary.get(key) is not False:
            errors.append(key)
    checks = summary.get("checks")
    if not isinstance(checks, Mapping) or set(checks) != set(CHECK_NAMES):
        errors.append("check_inventory")
        checks = {}
    for name in CHECK_NAMES:
        check = checks.get(name)
        if not isinstance(check, Mapping):
            errors.append(f"check:{name}")
            continue
        if set(check) != {"passed", "cases", "digest"}:
            errors.append(f"check_keys:{name}")
        if check.get("passed") is not True:
            errors.append(f"passed:{name}")
        if not isinstance(check.get("cases"), int) or isinstance(check.get("cases"), bool):
            errors.append(f"cases:{name}")
        elif check["cases"] != EXPECTED_CASE_COUNTS[name]:
            errors.append(f"cases:{name}")
        if not _is_sha256(check.get("digest")):
            errors.append(f"digest:{name}")
    check_count = summary.get("check_count")
    if (
        not isinstance(check_count, int)
        or isinstance(check_count, bool)
        or check_count != len(CHECK_NAMES)
    ):
        errors.append("check_count")
    passed_count = summary.get("passed_count")
    if (
        not isinstance(passed_count, int)
        or isinstance(passed_count, bool)
        or passed_count != len(CHECK_NAMES)
    ):
        errors.append("passed_count")
    if summary.get("all_passed") is not True:
        errors.append("all_passed")
    return errors


def _parse_max_seconds(value: str) -> int:
    try:
        parsed = int(value)
        return _validated_max_seconds(parsed)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _write_summary_exclusive(path: Path, payload: str) -> None:
    """Atomically create one bounded summary without following/overwriting it."""

    data = (payload + "\n").encode("utf-8")
    if len(data) > MAX_SUMMARY_BYTES:
        raise ValueError("summary exceeds the bounded output contract")
    requested = path.expanduser()
    requested.parent.mkdir(parents=True, exist_ok=True)
    parent = requested.parent.resolve(strict=True)
    target = parent / requested.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite acceptance summary: {target.name}")
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        # A hard-link publish is atomic and fails instead of overwriting if a
        # competing writer or symlink creates the requested name.
        os.link(temporary_name, target)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-seconds", type=_parse_max_seconds, default=180)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = run_acceptance(max_seconds=args.max_seconds)
    validation_errors = validate_summary(summary)
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    if args.output is not None:
        _write_summary_exclusive(args.output, payload)
    print(payload)
    return 0 if not validation_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
