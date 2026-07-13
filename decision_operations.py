"""Decision Operations persistence layer for recommendation review workflows.

This module keeps a minimal, auditable record of canonical recommendation
decisions without mutating the immutable Decision Intelligence bundle.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sqlite3
import secrets
from typing import Any, Dict, List, Optional, Tuple

from decision_intelligence import AnalysisBundle, RecommendedAction


_DEFAULT_SCOPE = "customer"
_VALID_REVIEW_DECISIONS = frozenset(
    {
        "accept",
        "approve",
        "approved",
        "deny",
        "reject",
        "edit",
        "defer",
        "needs_more_evidence",
        "duplicate",
        "already_completed",
        "out_of_scope",
        "superseded",
        "needs_revalidation",
        "reopen",
        "ok",
        "okay",
    }
)
_VALID_REVIEW_STATES = frozenset(
    {
        "reviewed",
        "proposed",
        "reopened",
        "accepted",
        "accepted_with_edit",
        "deferred",
        "needs_more_evidence",
        "duplicate",
        "already_completed",
        "out_of_scope",
        "needs_revalidation",
        "rejected",
        "dismissed",
        "superseded",
    }
)
_REVIEW_STATE_BY_DECISION = {
    "accept": "accepted",
    "approve": "accepted",
    "approved": "accepted",
    "deny": "rejected",
    "reject": "rejected",
    "edit": "accepted_with_edit",
    "defer": "deferred",
    "needs_more_evidence": "needs_more_evidence",
    "duplicate": "duplicate",
    "already_completed": "already_completed",
    "out_of_scope": "out_of_scope",
    "superseded": "superseded",
    "needs_revalidation": "needs_revalidation",
    "reopen": "reopened",
    "ok": "accepted",
    "okay": "accepted",
}
_VALID_REASON_CODES = frozenset(
    {
        "as_original",
        "owner_corrected",
        "priority_corrected",
        "scope_corrected",
        "evidence_quality",
        "already_completed",
        "wrong_scope",
        "stale_evidence",
        "duplicate",
        "out_of_scope",
        "no_actionable_output",
        "needs_more_evidence",
        "revalidation",
    }
)
_REASON_CODE_REQUIRED_DECISIONS = frozenset(
    {
        "edit",
        "reject",
        "deny",
        "defer",
        "needs_more_evidence",
        "duplicate",
        "already_completed",
        "out_of_scope",
        "needs_revalidation",
        "reopen",
    }
)
_VALID_OUTCOMES = frozenset({"succeeded", "not_succeeded", "in_progress", "unknown"})
_OUTCOME_ALIASES = {
    "expected_improvement_observed": ("expected_improvement", "improvement_observed"),
    "expected_deterioration_avoided": ("deterioration_avoided", "risk_improving", "risk avoided"),
    "no_material_change_observed": ("no_material_change", "no_change"),
    "mixed_result": ("mixed", "mixed_signals"),
    "worsening_observed": ("worsening", "condition_worsened", "got_worse"),
    "apparent_improvement_but_causality_unknown": ("causality_unknown", "ambiguous", "ambiguous_causality", "causal_unknown", "apparent_improvement"),
    "human_confirmed": ("confirmed",),
    "human_rejected": ("rejected", "disproved"),
    "not_yet_observable": ("not_observable_yet", "not_yet_visible", "not_yet_visible"),
    "observation_window_not_reached": ("window_not_reached", "window_not_ready"),
    "insufficient_evidence": ("insufficient_data", "evidence_insufficient"),
    "not_measurable": ("unmeasurable", "not_measurable"),
}
_NORMALIZED_OUTCOME_ALIASES = {
    alias: canonical for canonical, aliases in _OUTCOME_ALIASES.items() for alias in aliases
}
_VALID_OUTCOMES = frozenset(_VALID_OUTCOMES | set(_NORMALIZED_OUTCOME_ALIASES.keys()) | set(_OUTCOME_ALIASES.keys()))
_COMPLETION_OUTCOMES = frozenset(
    {
        "succeeded",
        "not_succeeded",
        "expected_improvement_observed",
        "expected_deterioration_avoided",
        "no_material_change_observed",
        "mixed_result",
        "worsening_observed",
        "apparent_improvement_but_causality_unknown",
        "human_confirmed",
        "human_rejected",
        "not_measurable",
        "unknown",
    }
)
_FEEDBACK_EXPORT_SCHEMA_VERSION = "1.0"
_REC_ID_PREFIX = "rec"
_DEFAULT_ACTION_STATE = "proposed"
_VALID_ACTION_STATES = frozenset(
    {
        "proposed",
        "approved",
        "assigned",
        "in_progress",
        "blocked",
        "completion_reported",
        "awaiting_verification",
        "verified",
        "dismissed",
        "superseded",
        "closed",
        "reopened",
    }
)
_ACTION_STATE_BY_DECISION = {
    "accept": "approved",
    "approve": "approved",
    "approved": "approved",
    "edit": "approved",
    "defer": "proposed",
    "needs_more_evidence": "proposed",
    "needs_revalidation": "proposed",
    "reject": "dismissed",
    "deny": "dismissed",
    "duplicate": "dismissed",
    "already_completed": "dismissed",
    "out_of_scope": "dismissed",
    "superseded": "superseded",
    "reopen": "reopened",
    "ok": "approved",
    "okay": "approved",
}
_ALLOWED_ACTION_STATE_TRANSITIONS = {
    "proposed": {"approved", "assigned", "blocked", "dismissed", "reopened"},
    "approved": {
        "assigned",
        "blocked",
        "in_progress",
        "dismissed",
        "superseded",
        "closed",
        "awaiting_verification",
        "reopened",
    },
    "assigned": {
        "in_progress",
        "blocked",
        "completion_reported",
        "dismissed",
        "superseded",
        "closed",
        "reopened",
    },
    "in_progress": {
        "blocked",
        "completion_reported",
        "dismissed",
        "superseded",
        "closed",
        "reopened",
    },
    "blocked": {
        "assigned",
        "in_progress",
        "completion_reported",
        "dismissed",
        "superseded",
        "closed",
        "reopened",
    },
    "completion_reported": {
        "awaiting_verification",
        "verified",
        "dismissed",
        "closed",
        "reopened",
    },
    "awaiting_verification": {"verified", "dismissed", "closed"},
    "verified": {"closed", "reopened"},
    "dismissed": {"reopened", "closed", "superseded"},
    "superseded": {"reopened", "closed"},
    "closed": {"reopened"},
    "reopened": {"proposed", "approved", "assigned", "closed"},
}

_RECURRENCE_TRIGGER_REVIEW_STATES = frozenset(
    {
        "accepted",
        "accepted_with_edit",
        "needs_revalidation",
        "needs_more_evidence",
        "duplicate",
        "already_completed",
        "out_of_scope",
        "superseded",
        "rejected",
    }
)
_RECURRENCE_TRIGGER_ACTION_STATES = frozenset(
    {
        "verified",
        "closed",
        "dismissed",
        "superseded",
    }
)


def _is_recurrence_candidate(row: sqlite3.Row) -> bool:
    if row is None:
        return False
    review_state = _normalize_review_state(row["review_state"])
    action_state = _normalize_action_state(row["action_state"])
    return (
        review_state in _RECURRENCE_TRIGGER_REVIEW_STATES
        or action_state in _RECURRENCE_TRIGGER_ACTION_STATES
    )


def _safe_text(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return str(value)
    rendered = str(value).strip()
    return rendered if rendered else default


def _safe_tuple_text(values: Iterable[Any]) -> Tuple[str, ...]:
    cleaned = tuple(_safe_text(value) for value in values)
    cleaned = tuple(item for item in cleaned if item)
    deduped = []
    for item in cleaned:
        if item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def _json_to_text_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return _safe_tuple_text((value,))
    if isinstance(value, (list, tuple, set)):
        return _safe_tuple_text(value)
    return _safe_tuple_text((value,))


def _canonical_text_sequence_diff(
    left: Iterable[Any], right: Iterable[Any]
) -> bool:
    return _canonical_sequence(left) != _canonical_sequence(right)


def _stable_signature_diffs(row: sqlite3.Row, action: RecommendedAction) -> list[str]:
    diffs: list[str] = []
    if _canonical_text_sequence_diff(_json_to_text_tuple(row["triggering_finding_ids"]), action.triggering_finding_ids):
        diffs.append("triggering_finding_ids")
    if _canonical_text_sequence_diff(_json_to_text_tuple(row["evidence_ids"]), action.evidence_ids):
        diffs.append("evidence_ids")
    if _safe_text(row["proposed_owner"], default="").casefold() != _safe_text(
        action.proposed_owner
    ).casefold():
        diffs.append("proposed_owner")
    if _safe_text(row["owner_confidence"], default="").casefold() != _safe_text(
        action.owner_confidence
    ).casefold():
        diffs.append("owner_confidence")
    if _canonical_text_sequence_diff(_json_to_text_tuple(row["dependencies_json"]), action.dependencies):
        diffs.append("dependencies")
    if _safe_text(row["timing_window"], default="").casefold() != _safe_text(
        action.timing_window
    ).casefold():
        diffs.append("timing_window")
    if _safe_text(row["expected_outcome"], default="").casefold() != _safe_text(
        action.expected_outcome
    ).casefold():
        diffs.append("expected_outcome")
    return diffs


def _revalidation_reason_code(changes: Iterable[str]) -> str:
    for change in changes:
        if change in {"triggering_finding_ids", "evidence_ids"}:
            return "stale_evidence"
    if "proposed_owner" in changes:
        return "owner_corrected"
    if "dependencies" in changes:
        return "scope_corrected"
    if changes:
        return "revalidation"
    return "as_original"


def _canonical_sequence(values: Iterable[Any]) -> Tuple[str, ...]:
    return tuple(_safe_text(item).casefold() for item in values if _safe_text(item))


def _canonical_json(value: Any) -> str:
    if isinstance(value, set):
        normalized = sorted(_safe_text(item).casefold() for item in value)
    elif isinstance(value, (tuple, list)):
        normalized = [_safe_text(item).casefold() for item in value]
    elif isinstance(value, dict):
        normalized = {
            _safe_text(key).casefold(): _safe_text(val).casefold() for key, val in value.items()
        }
        return json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    else:
        normalized = _safe_text(value)
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def _recommendation_stable_signature(action: RecommendedAction) -> str:
    payload = {
        "scope_kind": _safe_text(action.scope_kind).casefold(),
        "scope_id": _safe_text(action.scope_id).casefold(),
        "action_type": _safe_text(action.action_type).casefold(),
        "triggering_finding_ids": _canonical_sequence(action.triggering_finding_ids),
        "measurable_success_signal": _safe_text(action.measurable_success_signal).casefold(),
    }
    payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    digest = sha256(payload_json.encode("utf-8")).hexdigest()[:24]
    return f"{_REC_ID_PREFIX}:{digest}"


def _row_signature_from_payload(action_payload: Dict[str, Any]) -> str:
    return _recommendation_stable_signature(
        SimpleNamespace(
            scope_kind=action_payload.get("scope_kind", ""),
            scope_id=action_payload.get("scope_id", ""),
            action_type=action_payload.get("action_type", ""),
            triggering_finding_ids=action_payload.get("triggering_finding_ids", ()),
            measurable_success_signal=action_payload.get("measurable_success_signal", ""),
        )
    )


def _row_signature_from_stored_action(row: sqlite3.Row) -> str:
    if row is None:
        return ""
    try:
        triggering = json.loads(row["triggering_finding_ids"] or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        triggering = ()
    if not isinstance(triggering, (list, tuple)):
        triggering = ()
    return _row_signature_from_payload(
        {
            "scope_kind": row["scope_kind"] if row["scope_kind"] else "",
            "scope_id": row["scope_id"] if row["scope_id"] else "",
            "action_type": row["action_type"] if row["action_type"] else "",
            "triggering_finding_ids": tuple(triggering),
            "measurable_success_signal": row["measurable_success_signal"] if row["measurable_success_signal"] else "",
        }
    )

def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False, sort_keys=True)


def _scoped_action_id(base_action_id: str, scope_id: str) -> str:
    scope_text = _safe_text(scope_id)
    if not scope_text:
        return base_action_id
    digest = sha256(scope_text.encode("utf-8")).hexdigest()[:10]
    return f"{base_action_id}:{digest}"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalize_decision(value: Any) -> str:
    cleaned = _safe_text(value).casefold()
    if cleaned in {"approve", "approved", "accept", "okay", "ok"}:
        return "accept"
    if cleaned in _VALID_REVIEW_DECISIONS:
        return cleaned
    raise ValueError(f"Unsupported review decision: {value!r}")


def _normalize_decision_state(decision: str) -> str:
    if decision not in _REVIEW_STATE_BY_DECISION:
        return "reviewed"
    return _REVIEW_STATE_BY_DECISION.get(decision, "reviewed")


def _normalize_review_state(value: Any) -> str:
    cleaned = _safe_text(value).casefold()
    if not cleaned:
        return ""
    if cleaned in _VALID_REVIEW_STATES:
        return cleaned
    if cleaned in _REVIEW_STATE_BY_DECISION:
        return _REVIEW_STATE_BY_DECISION[cleaned]
    return cleaned


def _normalize_reason_code(value: Any) -> str:
    cleaned = _safe_text(value)
    if not cleaned:
        return "as_original"
    lowered = cleaned.casefold()
    if lowered in _VALID_REASON_CODES:
        return lowered
    return "as_original"


def _normalize_action_state(value: Any) -> str:
    cleaned = _safe_text(value).casefold()
    if cleaned in _VALID_ACTION_STATES:
        return cleaned
    return _DEFAULT_ACTION_STATE


def _is_allowed_action_state_transition(current_state: str, next_state: str) -> bool:
    current_state = _normalize_action_state(current_state)
    next_state = _normalize_action_state(next_state)
    if current_state == next_state:
        return True
    return next_state in _ALLOWED_ACTION_STATE_TRANSITIONS.get(current_state, set())


def _next_action_state_from_review(
    *, current_state: str, review_state: str
) -> str:
    target = _ACTION_STATE_BY_DECISION.get(review_state, _normalize_action_state(current_state))
    if target in {"approved", "proposed"}:
        return target
    if target not in _VALID_ACTION_STATES:
        return _DEFAULT_ACTION_STATE

    if target == "superseded" and _normalize_action_state(current_state) != "closed":
        return "superseded"
    return target


def _next_action_state_from_outcome(
    *, current_state: str, outcome: str
) -> str:
    current_state = _normalize_action_state(current_state)
    if outcome in {"", _DEFAULT_ACTION_STATE}:
        return current_state
    if outcome == "in_progress":
        if _is_allowed_action_state_transition(current_state, "in_progress"):
            return "in_progress"
        return current_state
    if outcome in _COMPLETION_OUTCOMES:
        if _is_allowed_action_state_transition(current_state, "completion_reported"):
            return "completion_reported"
        if _is_allowed_action_state_transition(current_state, "awaiting_verification"):
            return "awaiting_verification"
    return current_state


def _normalize_outcome(value: Any) -> str:
    cleaned = _safe_text(value).casefold()
    if cleaned in _NORMALIZED_OUTCOME_ALIASES:
        return _NORMALIZED_OUTCOME_ALIASES[cleaned]
    if cleaned in _VALID_OUTCOMES:
        return cleaned
    return "unknown"


def _extract_recommended_actions(bundle: AnalysisBundle) -> List[RecommendedAction]:
    customers = bundle.customers
    if customers is None:
        customer_values: Iterable[SimpleNamespace] = ()
    elif isinstance(customers, SimpleNamespace):
        customer_values = (customers,)
    elif isinstance(customers, dict):
        customer_values = tuple(customers.values())
    elif isinstance(customers, list):
        customer_values = tuple(customers)
    elif isinstance(customers, tuple):
        customer_values = customers
    elif isinstance(customers, str):
        raise TypeError("bundle.customers must be iterable of customer payloads, got str")
    elif isinstance(customers, Iterable):
        customer_values = tuple(customers)
    else:
        customer_values = (customers,)

    by_signature: Dict[str, RecommendedAction] = {}
    for customer in customer_values:
        for action in customer.recommended_actions:
            if not action.action_id:
                continue
            signature = _recommendation_stable_signature(action)
            existing = by_signature.get(signature)
            if existing is None or float(action.priority_score) > float(existing.priority_score):
                by_signature[signature] = action
    for action in bundle.portfolio.recommended_actions:
        if not action.action_id:
            continue
        signature = _recommendation_stable_signature(action)
        existing = by_signature.get(signature)
        if existing is None or float(action.priority_score) > float(existing.priority_score):
            by_signature[signature] = action
    return sorted(
        by_signature.values(),
        key=lambda item: (-item.priority_score, item.scope_id.casefold(), item.action_id),
    )


def _stable_salt(value: str | None) -> str:
    return value.strip() if value else secrets.token_hex(16)


def _pseudonymize_identifier(value: Any, *, salt: str, prefix: str = "id") -> str:
    normalized = _safe_text(value)
    if not normalized:
        return f"{prefix}:unknown"
    digest = sha256(f"{salt}:{normalized}".encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


class DecisionOpsStore:
    """SQLite-backed store for reviews, events, and outcomes."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path:
            target = Path(db_path)
        else:
            explicit = os.getenv("ADOPTIQ_DECISION_OPS_DB_PATH")
            if explicit:
                target = Path(explicit)
            else:
                root = Path(os.getenv("ADOPTIQ_DECISION_OPS_DIR") or str(Path.home() / ".adoptiq" / "decision_ops"))
                root.mkdir(parents=True, exist_ok=True)
                target = root / "decision_operations.db"
        target.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = target
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_ops_actions (
                    action_id TEXT NOT NULL,
                    scope_fingerprint TEXT NOT NULL,
                    scope_kind TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    source_action_id TEXT NOT NULL DEFAULT '',
                    action_signature TEXT NOT NULL DEFAULT '',
                    action_type TEXT NOT NULL,
                    specific_action TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    triggering_finding_ids TEXT NOT NULL,
                    evidence_ids TEXT NOT NULL,
                    proposed_owner TEXT NOT NULL,
                    owner_confidence TEXT NOT NULL,
                    urgency TEXT NOT NULL,
                    rank INTEGER NOT NULL DEFAULT 1,
                    priority_score REAL NOT NULL DEFAULT 0.0,
                    timing_window TEXT NOT NULL,
                    effort TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    expected_outcome TEXT NOT NULL,
                    measurable_success_signal TEXT NOT NULL,
                    recommendation_source TEXT NOT NULL,
                    ranking_factors_json TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL,
                    analysis_fingerprint TEXT NOT NULL,
                    analysis_snapshot_path TEXT NOT NULL,
                    analysis_request_fingerprint TEXT NOT NULL,
                    analysis_comparison_scope_fingerprint TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    review_state TEXT NOT NULL DEFAULT 'proposed',
                    action_state TEXT NOT NULL DEFAULT 'proposed',
                    reviewed_at TEXT,
                    reviewed_by TEXT,
                    review_reason TEXT,
                    review_reason_code TEXT,
                    review_notes TEXT,
                    review_edited_value_json TEXT,
                    recurrence_depth INTEGER NOT NULL DEFAULT 0,
                    recurrence_parent_action_id TEXT NOT NULL DEFAULT '',
                    recurrence_previous_analysis_fingerprint TEXT NOT NULL DEFAULT '',
                    last_synced_at TEXT NOT NULL,
                    PRIMARY KEY (action_id, scope_fingerprint)
                )
                """
            )
            self._ensure_action_columns(cursor)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ops_actions_scope"
                " ON decision_ops_actions(scope_fingerprint)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ops_actions_active"
                " ON decision_ops_actions(scope_fingerprint, is_active)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_ops_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL,
                    scope_fingerprint TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    reason TEXT,
                    notes TEXT,
                    reason_code TEXT,
                    edited_value_json TEXT,
                    source_analysis_fingerprint TEXT,
                    recorded_at TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    UNIQUE (action_id, scope_fingerprint, recorded_at, decision),
                    FOREIGN KEY (action_id, scope_fingerprint)
                        REFERENCES decision_ops_actions(action_id, scope_fingerprint)
                        ON DELETE CASCADE
                )
                """
            )
            self._ensure_review_columns(cursor)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ops_reviews_action"
                " ON decision_ops_reviews(action_id, scope_fingerprint, recorded_at DESC)"
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_ops_reviews_idempotency
                ON decision_ops_reviews(action_id, scope_fingerprint, idempotency_key)
                WHERE idempotency_key != ''
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_ops_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL,
                    scope_fingerprint TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    observed_signal TEXT NOT NULL,
                    observed_value TEXT,
                    notes TEXT,
                    reporter TEXT,
                    recorded_at TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (action_id, scope_fingerprint)
                        REFERENCES decision_ops_actions(action_id, scope_fingerprint)
                        ON DELETE CASCADE
                )
                """
            )
            self._ensure_outcome_columns(cursor)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ops_outcomes_action"
                " ON decision_ops_outcomes(action_id, scope_fingerprint, recorded_at DESC)"
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_ops_outcomes_idempotency
                ON decision_ops_outcomes(action_id, scope_fingerprint, idempotency_key)
                WHERE idempotency_key != ''
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_ops_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL,
                    scope_fingerprint TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    event_payload_json TEXT,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (action_id, scope_fingerprint)
                        REFERENCES decision_ops_actions(action_id, scope_fingerprint)
                        ON DELETE CASCADE
                )
                """
            )
            self._ensure_event_columns(cursor)
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ops_events_action"
                " ON decision_ops_events(action_id, scope_fingerprint, recorded_at DESC)"
            )

    @staticmethod
    def _ensure_columns(
        cursor: sqlite3.Cursor,
        table_name: str,
        desired_columns: Dict[str, str],
    ) -> None:
        existing = {
            row[1]
            for row in cursor.execute(f"PRAGMA table_info({table_name})")
            if len(row) >= 2
        }
        for name, ddl in desired_columns.items():
            if name in existing:
                continue
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}")

    @staticmethod
    def _ensure_action_columns(cursor: sqlite3.Cursor) -> None:
        """Backfill any missing columns into an existing action table.

        Legacy installs may have initialized the table with a narrower set of
        fields. ``CREATE TABLE IF NOT EXISTS`` won't alter that in-place, so we
        patch the schema proactively before writes that require newly-added
        columns.
        """
        desired_columns = {
            "action_type": "TEXT NOT NULL DEFAULT 'improve'",
            "specific_action": "TEXT NOT NULL DEFAULT ''",
            "rationale": "TEXT NOT NULL DEFAULT ''",
            "triggering_finding_ids": "TEXT NOT NULL DEFAULT '[]'",
            "evidence_ids": "TEXT NOT NULL DEFAULT '[]'",
            "proposed_owner": "TEXT NOT NULL DEFAULT ''",
            "owner_confidence": "TEXT NOT NULL DEFAULT 'MEDIUM'",
            "urgency": "TEXT NOT NULL DEFAULT 'Normal'",
            "rank": "INTEGER NOT NULL DEFAULT 1",
            "priority_score": "REAL NOT NULL DEFAULT 0.0",
            "timing_window": "TEXT NOT NULL DEFAULT ''",
            "effort": "TEXT NOT NULL DEFAULT ''",
            "confidence": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "expected_outcome": "TEXT NOT NULL DEFAULT ''",
            "measurable_success_signal": "TEXT NOT NULL DEFAULT ''",
            "recommendation_source": "TEXT NOT NULL DEFAULT 'decision-intelligence-v2'",
            "ranking_factors_json": "TEXT NOT NULL DEFAULT '{}'",
            "dependencies_json": "TEXT NOT NULL DEFAULT '[]'",
            "analysis_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "analysis_snapshot_path": "TEXT NOT NULL DEFAULT ''",
            "analysis_request_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "analysis_comparison_scope_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "review_state": "TEXT NOT NULL DEFAULT 'proposed'",
            "action_state": "TEXT NOT NULL DEFAULT 'proposed'",
            "action_signature": "TEXT NOT NULL DEFAULT ''",
            "source_action_id": "TEXT NOT NULL DEFAULT ''",
            "reviewed_at": "TEXT",
            "reviewed_by": "TEXT",
            "review_reason": "TEXT",
            "review_reason_code": "TEXT",
            "review_edited_value_json": "TEXT",
            "review_notes": "TEXT",
            "recurrence_depth": "INTEGER NOT NULL DEFAULT 0",
            "recurrence_parent_action_id": "TEXT NOT NULL DEFAULT ''",
            "recurrence_previous_analysis_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "last_synced_at": "TEXT NOT NULL DEFAULT ''",
        }

        DecisionOpsStore._ensure_columns(cursor, "decision_ops_actions", desired_columns)

    @staticmethod
    def _ensure_review_columns(cursor: sqlite3.Cursor) -> None:
        """Backfill missing review columns for legacy installs."""
        desired_columns = {
            "action_id": "TEXT NOT NULL DEFAULT ''",
            "scope_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "decision": "TEXT NOT NULL DEFAULT ''",
            "reviewer": "TEXT NOT NULL DEFAULT ''",
            "reason": "TEXT",
            "notes": "TEXT",
            "reason_code": "TEXT",
            "edited_value_json": "TEXT",
            "source_analysis_fingerprint": "TEXT",
            "recorded_at": "TEXT NOT NULL DEFAULT ''",
            "idempotency_key": "TEXT NOT NULL DEFAULT ''",
            "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
        }
        DecisionOpsStore._ensure_columns(cursor, "decision_ops_reviews", desired_columns)

    @staticmethod
    def _ensure_outcome_columns(cursor: sqlite3.Cursor) -> None:
        desired_columns = {
            "action_id": "TEXT NOT NULL DEFAULT ''",
            "scope_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "outcome": "TEXT NOT NULL DEFAULT ''",
            "observed_signal": "TEXT NOT NULL DEFAULT ''",
            "observed_value": "TEXT",
            "notes": "TEXT",
            "reporter": "TEXT",
            "recorded_at": "TEXT NOT NULL DEFAULT ''",
            "idempotency_key": "TEXT NOT NULL DEFAULT ''",
        }
        DecisionOpsStore._ensure_columns(cursor, "decision_ops_outcomes", desired_columns)

    @staticmethod
    def _ensure_event_columns(cursor: sqlite3.Cursor) -> None:
        desired_columns = {
            "action_id": "TEXT NOT NULL DEFAULT ''",
            "scope_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "event_type": "TEXT NOT NULL DEFAULT ''",
            "actor": "TEXT NOT NULL DEFAULT ''",
            "event_payload_json": "TEXT",
            "recorded_at": "TEXT NOT NULL DEFAULT ''",
        }
        DecisionOpsStore._ensure_columns(cursor, "decision_ops_events", desired_columns)

        desired_reason_columns = {
            "reason_code": "TEXT",
            "edited_value_json": "TEXT",
            "source_analysis_fingerprint": "TEXT",
        }
        DecisionOpsStore._ensure_columns(cursor, "decision_ops_reviews", desired_reason_columns)

    def load_bundle(self, snapshot_path: str | Path) -> AnalysisBundle:
        return AnalysisBundle.load(Path(snapshot_path))

    def _action_payload(
        self, action: RecommendedAction, bundle: AnalysisBundle, scope_fp: str
    ) -> Dict[str, Any]:
        return {
            "action_id": action.action_id,
            "action_signature": _recommendation_stable_signature(action),
            "source_action_id": action.action_id,
            "scope_kind": action.scope_kind or _DEFAULT_SCOPE,
            "scope_id": action.scope_id,
            "action_type": action.action_type,
            "specific_action": action.specific_action,
            "rationale": action.rationale,
            "triggering_finding_ids": list(action.triggering_finding_ids),
            "evidence_ids": list(action.evidence_ids),
            "proposed_owner": action.proposed_owner,
            "owner_confidence": action.owner_confidence,
            "urgency": action.urgency,
            "rank": int(action.rank),
            "priority_score": float(action.priority_score),
            "ranking_factors": dict(action.ranking_factors),
            "dependencies": list(action.dependencies),
            "timing_window": action.timing_window,
            "effort": action.effort,
            "confidence": action.confidence,
            "expected_outcome": action.expected_outcome,
            "measurable_success_signal": action.measurable_success_signal,
            "recommendation_source": action.recommendation_source,
            "recommendation_context": {
                "analysis_fingerprint": bundle.analysis_fingerprint,
                "analysis_request_fingerprint": bundle.context.request_fingerprint,
                "scope_fingerprint": scope_fp,
            },
        }

    @staticmethod
    def _find_existing_action(
        cursor: sqlite3.Cursor,
        scope_fp: str,
        scope_id: str,
        action_signature: str,
        action_id: str,
        source_action_id: str,
    ) -> Optional[sqlite3.Row]:
        cursor.execute(
            """
            SELECT *
            FROM decision_ops_actions
            WHERE scope_fingerprint = ?
              AND scope_id = ?
              AND (
                  action_id = ?
                  OR source_action_id = ?
                  OR action_signature = ?
              )
            ORDER BY action_id = ? DESC, source_action_id = ? DESC, action_signature = ? DESC
            """,
            (
                scope_fp,
                scope_id,
                action_id,
                source_action_id,
                action_signature,
                action_id,
                source_action_id,
                action_signature,
            ),
        )
        existing = cursor.fetchone()
        if existing is not None:
            return existing

        cursor.execute(
            """
            SELECT * FROM decision_ops_actions
            WHERE scope_fingerprint = ? AND action_signature = ?
            """,
            (scope_fp, action_signature),
        )
        existing = cursor.fetchone()
        if existing is not None:
            return existing
        return None

    @staticmethod
    def _find_action_id_collision(
        cursor: sqlite3.Cursor, scope_fp: str, action_id: str
    ) -> bool:
        cursor.execute(
            """
            SELECT 1
            FROM decision_ops_actions
            WHERE action_id = ? AND scope_fingerprint = ?
            LIMIT 1
            """,
            (action_id, scope_fp),
        )
        return cursor.fetchone() is not None

    @staticmethod
    def _resolve_action_identity(
        cursor: sqlite3.Cursor,
        scope_fp: str,
        scope_id: str,
        action_signature: str,
        action_id: str,
        source_action_id: str,
    ) -> tuple[str, Optional[sqlite3.Row]]:
        existing = DecisionOpsStore._find_existing_action(
            cursor,
            scope_fp,
            scope_id,
            action_signature,
            action_id,
            source_action_id,
        )
        if existing is not None:
            return existing["action_id"], existing

        resolved_action_id = action_id
        if DecisionOpsStore._find_action_id_collision(cursor, scope_fp, action_id):
            resolved_action_id = _scoped_action_id(action_id, scope_id)
            while DecisionOpsStore._find_action_id_collision(
                cursor, scope_fp, resolved_action_id
            ):
                suffix_seed = f"{resolved_action_id}:{action_signature}"
                digest = sha256(suffix_seed.encode("utf-8")).hexdigest()[:10]
                resolved_action_id = f"{action_id}:{digest}"

        return resolved_action_id, None

    def _upsert_action(
        self,
        cursor: sqlite3.Cursor,
        action: RecommendedAction,
        bundle: AnalysisBundle,
        scope_fp: str,
        snapshot_path: str,
    ) -> str:
        payload = self._action_payload(action, bundle, scope_fp)
        now = _now_utc()
        action_id, existing = self._resolve_action_identity(
            cursor,
            scope_fp,
            payload["scope_id"],
            payload["action_signature"],
            payload["action_id"],
            payload["source_action_id"],
        )
        row_exists = existing is not None
        old_review_state = str(existing["review_state"]) if row_exists else None
        old_action_state = (
            _normalize_action_state(existing["action_state"]) if row_exists else _DEFAULT_ACTION_STATE
        )
        recurrence_depth = int(existing["recurrence_depth"]) if row_exists else 0
        recurrence_parent_action_id = (
            _safe_text(existing["recurrence_parent_action_id"]) if row_exists else ""
        )
        recurrence_previous_analysis_fingerprint = (
            _safe_text(existing["recurrence_previous_analysis_fingerprint"]) if row_exists else ""
        )
        revalidation_required = False
        revalidation_diffs: list[str] = []
        review_reason: Optional[str] = None
        review_reason_code: Optional[str] = None
        reviewed_state_for_store = old_review_state
        action_state_for_store = old_action_state
        is_recurrence = False
        if row_exists:
            old_signature = _row_signature_from_stored_action(existing)
            new_signature = _row_signature_from_payload(payload)
            revalidation_diffs = _stable_signature_diffs(existing, action)
            revalidation_required = old_signature != new_signature or bool(revalidation_diffs)
            if revalidation_required and old_review_state in {"accepted", "accepted_with_edit"}:
                reviewed_state_for_store = "needs_revalidation"
                diffs_display = ", ".join(sorted(revalidation_diffs))
                review_reason = (
                    "Canonical recommendation changed since last review: "
                    + (diffs_display if diffs_display else "signature changed")
                )
                review_reason_code = _revalidation_reason_code(revalidation_diffs)

            if revalidation_required and _is_recurrence_candidate(existing):
                is_recurrence = True
                recurrence_depth += 1
                if not recurrence_parent_action_id:
                    recurrence_parent_action_id = existing["action_id"]
                recurrence_previous_analysis_fingerprint = _safe_text(existing["analysis_fingerprint"])
                if reviewed_state_for_store == "needs_revalidation":
                    action_state_for_store = "proposed"
                else:
                    reviewed_state_for_store = "proposed"
                    action_state_for_store = "proposed"
                if review_reason is None:
                    review_reason = (
                        "Action recurred after completion and was reintroduced by a new analysis"
                    )
                    review_reason_code = "revalidation"

        if not row_exists:
            cursor.execute(
                """
                INSERT INTO decision_ops_actions (
                    action_id, scope_fingerprint, scope_kind, scope_id, source_action_id,
                    action_signature, action_type, specific_action, rationale, triggering_finding_ids, evidence_ids,
                    proposed_owner, owner_confidence, urgency, rank, priority_score,
                    timing_window, effort, confidence, expected_outcome,
                    measurable_success_signal, recommendation_source,
                    ranking_factors_json, dependencies_json,
                    analysis_fingerprint, analysis_snapshot_path,
                    analysis_request_fingerprint, analysis_comparison_scope_fingerprint,
                    is_active, review_state, action_state, reviewed_at, reviewed_by,
                    review_reason, review_reason_code, review_edited_value_json, review_notes,
                    recurrence_depth, recurrence_parent_action_id, recurrence_previous_analysis_fingerprint,
                    last_synced_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    action_id,
                    scope_fp,
                    payload["scope_kind"],
                    payload["scope_id"],
                    payload["source_action_id"],
                    payload["action_signature"],
                    payload["action_type"],
                    payload["specific_action"],
                    payload["rationale"],
                    _safe_json(payload["triggering_finding_ids"]),
                    _safe_json(payload["evidence_ids"]),
                    payload["proposed_owner"],
                    action.owner_confidence,
                    action.urgency,
                    int(payload["rank"]),
                    float(payload["priority_score"]),
                    action.timing_window,
                    action.effort,
                    action.confidence,
                    action.expected_outcome,
                    action.measurable_success_signal,
                    payload["recommendation_source"],
                    _safe_json(payload["ranking_factors"]),
                    _safe_json(action.dependencies),
                    bundle.analysis_fingerprint,
                    snapshot_path,
                    bundle.context.request_fingerprint,
                    scope_fp,
                    1,
                    "proposed",
                    _DEFAULT_ACTION_STATE,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    "",
                    "",
                    now,
                ),
            )
            self._record_event(
                cursor,
                action_id,
                scope_fp,
                "action_synced",
                actor="system",
                details=_safe_json({"analysis_fingerprint": bundle.analysis_fingerprint}),
            )
            return action_id

        cursor.execute(
            """
            UPDATE decision_ops_actions
            SET source_action_id = ?, action_signature = ?, scope_kind = ?, scope_id = ?, action_type = ?, specific_action = ?,
                rationale = ?, triggering_finding_ids = ?, evidence_ids = ?,
                proposed_owner = ?, owner_confidence = ?, urgency = ?,
                rank = ?, priority_score = ?, timing_window = ?, effort = ?, confidence = ?,
                expected_outcome = ?, measurable_success_signal = ?, recommendation_source = ?,
                ranking_factors_json = ?, dependencies_json = ?, review_state = ?, action_state = ?,
                recurrence_depth = ?, recurrence_parent_action_id = ?, recurrence_previous_analysis_fingerprint = ?,
                analysis_fingerprint = ?, analysis_snapshot_path = ?, analysis_request_fingerprint = ?,
                analysis_comparison_scope_fingerprint = ?, is_active = 1, last_synced_at = ?
            WHERE action_id = ? AND scope_fingerprint = ?
            """,
            (
                payload["source_action_id"],
                payload["action_signature"],
                payload["scope_kind"],
                payload["scope_id"],
                payload["action_type"],
                payload["specific_action"],
                payload["rationale"],
                _safe_json(payload["triggering_finding_ids"]),
                _safe_json(payload["evidence_ids"]),
                payload["proposed_owner"],
                action.owner_confidence,
                action.urgency,
                int(payload["rank"]),
                float(payload["priority_score"]),
                action.timing_window,
                action.effort,
                action.confidence,
                action.expected_outcome,
                action.measurable_success_signal,
                action.recommendation_source,
                _safe_json(payload["ranking_factors"]),
                _safe_json(action.dependencies),
                reviewed_state_for_store,
                action_state_for_store,
                recurrence_depth,
                recurrence_parent_action_id,
                recurrence_previous_analysis_fingerprint,
                bundle.analysis_fingerprint,
                snapshot_path,
                bundle.context.request_fingerprint,
                scope_fp,
                now,
                action_id,
                scope_fp,
            ),
        )
        if revalidation_required and reviewed_state_for_store == "needs_revalidation":
            cursor.execute(
                """
                UPDATE decision_ops_actions
                SET review_reason = ?, review_reason_code = ?
                WHERE action_id = ? AND scope_fingerprint = ?
                """,
                (
                    _safe_text(review_reason),
                    _safe_text(review_reason_code),
                    action_id,
                    scope_fp,
                ),
            )
            self._record_event(
                cursor,
                action_id,
                scope_fp,
                "review_revalidated",
                actor="system",
                details=_safe_json(
                    {
                        "review_state": old_review_state,
                        "new_review_state": reviewed_state_for_store,
                        "action_signature": payload["action_signature"],
                        "revalidation_reasons": revalidation_diffs,
                    }
                ),
            )
        if is_recurrence:
            cursor.execute(
                """
                UPDATE decision_ops_actions
                SET review_reason = COALESCE(review_reason, ?),
                    review_reason_code = COALESCE(NULLIF(review_reason_code, ''), ?)
                WHERE action_id = ? AND scope_fingerprint = ?
                """,
                (
                    _safe_text(review_reason),
                    _safe_text(review_reason_code),
                    action_id,
                    scope_fp,
                ),
            )
            self._record_event(
                cursor,
                action_id,
                scope_fp,
                "action_recurred",
                actor="system",
                details=_safe_json(
                    {
                        "review_state_before": old_review_state,
                        "action_state_before": old_action_state,
                        "recurrence_depth": recurrence_depth,
                        "recurrence_parent_action_id": recurrence_parent_action_id,
                        "analysis_fingerprint_previous": recurrence_previous_analysis_fingerprint,
                        "analysis_fingerprint_current": bundle.analysis_fingerprint,
                    }
                ),
            )
        return action_id

    def sync_from_snapshot(self, snapshot_path: str | Path) -> Dict[str, Any]:
        bundle = self.load_bundle(snapshot_path)
        scope_fp = bundle.context.comparison_scope_fingerprint
        actions = _extract_recommended_actions(bundle)
        snapshot_path_str = str(snapshot_path)
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE decision_ops_actions SET is_active = 0 WHERE scope_fingerprint = ?",
                (scope_fp,),
            )
            synced_ids: List[str] = []
            for action in actions:
                synced_ids.append(
                    self._upsert_action(
                    cursor,
                    action,
                    bundle,
                    scope_fp,
                    snapshot_path_str,
                    )
                )
            present_ids = tuple(dict.fromkeys(synced_ids))
            if present_ids:
                placeholders = ",".join("?" for _ in present_ids)
                cursor.execute(
                    f"UPDATE decision_ops_actions "
                    f"SET is_active = 1 "
                    f"WHERE action_id IN ({placeholders}) AND scope_fingerprint = ?",
                    tuple(present_ids) + (scope_fp,),
                )
        return {
            "scope_fingerprint": scope_fp,
            "analysis_fingerprint": bundle.analysis_fingerprint,
            "action_count": len(actions),
            "snapshot_path": str(snapshot_path),
        }

    def _fetch_rows(self, cursor: sqlite3.Cursor, scope_fp: str) -> List[sqlite3.Row]:
        cursor.execute(
            """
            SELECT * FROM decision_ops_actions
            WHERE scope_fingerprint = ?
            ORDER BY review_state, priority_score DESC, rank ASC, action_id ASC
            """,
            (scope_fp,),
        )
        return cursor.fetchall()

    def _events_for_action(
        self, cursor: sqlite3.Cursor, scope_fp: str, action_id: str
    ) -> List[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT event_type, actor, event_payload_json, recorded_at
            FROM decision_ops_events
            WHERE action_id = ? AND scope_fingerprint = ?
            ORDER BY recorded_at DESC
            LIMIT 5
            """,
            (action_id, scope_fp),
        )
        events = []
        for event in cursor.fetchall():
            events.append({
                "event_type": event["event_type"],
                "actor": event["actor"],
                "recorded_at": event["recorded_at"],
                "payload": event["event_payload_json"] or "{}",
            })
        return events

    def _resolve_action_row(
        self, cursor: sqlite3.Cursor, scope_fp: str, action_id: str
    ) -> Optional[sqlite3.Row]:
        cursor.execute(
            """
            SELECT * FROM decision_ops_actions
            WHERE action_id = ? AND scope_fingerprint = ?
            """,
            (action_id, scope_fp),
        )
        row = cursor.fetchone()
        if row is not None:
            return row

        cursor.execute(
            """
            SELECT * FROM decision_ops_actions
            WHERE source_action_id = ? AND scope_fingerprint = ?
            """,
            (action_id, scope_fp),
        )
        rows = cursor.fetchall()
        if len(rows) == 1:
            return rows[0]
        return None

    @staticmethod
    def _coerce_review_row_action_state(row: Optional[sqlite3.Row]) -> str:
        if row is None:
            return _DEFAULT_ACTION_STATE
        return _normalize_action_state(row["action_state"])

    def _apply_action_state(
        self,
        cursor: sqlite3.Cursor,
        scope_fp: str,
        action_id: str,
        *,
        next_state: str,
        actor: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        row = self._resolve_action_row(cursor, scope_fp, action_id)
        if row is None:
            raise ValueError("action_not_found")
        current_state = self._coerce_review_row_action_state(row)
        next_state = _normalize_action_state(next_state)
        if next_state == current_state:
            return current_state
        if not _is_allowed_action_state_transition(current_state, next_state):
            raise ValueError("invalid_action_state_transition")

        cursor.execute(
            """
            UPDATE decision_ops_actions
            SET action_state = ?
            WHERE action_id = ? AND scope_fingerprint = ?
            """,
            (next_state, action_id, scope_fp),
        )
        self._record_event(
            cursor,
            action_id,
            scope_fp,
            "action_state_changed",
            actor=actor,
            details=_safe_json({"from": current_state, "to": next_state, **(details or {})}),
        )
        return next_state

    def _events_for_action_excluding_types(
        self,
        cursor: sqlite3.Cursor,
        scope_fp: str,
        action_id: str,
        excluded_types: Iterable[str],
    ) -> List[Dict[str, Any]]:
        excluded = tuple(_safe_text(item) for item in excluded_types if item)
        if not excluded:
            return self._events_for_action(cursor, scope_fp, action_id)
        return [
            event
            for event in self._events_for_action(cursor, scope_fp, action_id)
            if event["event_type"] not in excluded
        ]

    def _reviews_for_action(
        self, cursor: sqlite3.Cursor, scope_fp: str, action_id: str
    ) -> List[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT decision, reviewer, reason, reason_code, edited_value_json, notes, recorded_at
            FROM decision_ops_reviews
            WHERE action_id = ? AND scope_fingerprint = ?
            ORDER BY recorded_at DESC
            """,
            (action_id, scope_fp),
        )
        return [
            {
                "decision": record["decision"],
                "reviewer": record["reviewer"],
                "reason": record["reason"],
                "reason_code": record["reason_code"] or "as_original",
                "edited_value": (
                    json.loads(record["edited_value_json"])
                    if record["edited_value_json"]
                    else None
                ),
                "notes": record["notes"],
                "recorded_at": record["recorded_at"],
            }
            for record in cursor.fetchall()
        ]

    def _review_by_idempotency_key(
        self,
        cursor: sqlite3.Cursor,
        scope_fp: str,
        action_id: str,
        idempotency_key: str,
    ) -> Optional[sqlite3.Row]:
        if not idempotency_key:
            return None
        cursor.execute(
            """
            SELECT *
            FROM decision_ops_reviews
            WHERE action_id = ? AND scope_fingerprint = ? AND idempotency_key = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (action_id, scope_fp, idempotency_key),
        )
        return cursor.fetchone()

    def _outcome_by_idempotency_key(
        self,
        cursor: sqlite3.Cursor,
        scope_fp: str,
        action_id: str,
        idempotency_key: str,
    ) -> Optional[sqlite3.Row]:
        if not idempotency_key:
            return None
        cursor.execute(
            """
            SELECT *
            FROM decision_ops_outcomes
            WHERE action_id = ? AND scope_fingerprint = ? AND idempotency_key = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (action_id, scope_fp, idempotency_key),
        )
        return cursor.fetchone()

    def _outcomes_for_action(
        self, cursor: sqlite3.Cursor, scope_fp: str, action_id: str
    ) -> List[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT outcome, observed_signal, observed_value, notes, reporter, recorded_at
            FROM decision_ops_outcomes
            WHERE action_id = ? AND scope_fingerprint = ?
            ORDER BY recorded_at DESC
            """,
            (action_id, scope_fp),
        )
        return [
            {
                "outcome": record["outcome"],
                "observed_signal": record["observed_signal"],
                "observed_value": record["observed_value"],
                "notes": record["notes"],
                "reporter": record["reporter"],
                "recorded_at": record["recorded_at"],
            }
                for record in cursor.fetchall()
            ]

    @staticmethod
    def _json_or_default(value: Optional[str], default: Any = None) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    def _build_feedback_records(
        self,
        cursor: sqlite3.Cursor,
        scope_fp: str,
        *,
        salt: str,
        include_raw_ids: bool,
        include_free_text: bool,
    ) -> List[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT * FROM decision_ops_actions
            WHERE scope_fingerprint = ?
            ORDER BY priority_score DESC, action_id ASC
            """,
            (scope_fp,),
        )
        action_rows = cursor.fetchall()
        records = []
        for row in action_rows:
            action_id_raw = row["action_id"]
            scope_id_raw = row["scope_id"]
            reviewer = row["reviewed_by"] or ""
            review_reason = row["review_reason"] or ""
            review_notes = row["review_notes"] or ""

            reviews = self._reviews_for_action(cursor, scope_fp, action_id_raw)
            outcomes = self._outcomes_for_action(cursor, scope_fp, action_id_raw)
            events = self._events_for_action(cursor, scope_fp, action_id_raw)

            reviews_payload = []
            for review in reviews:
                reason = review["reason"]
                notes = review.get("notes") or ""
                edited_value = review.get("edited_value")
                review_payload = {
                    "decision": review["decision"],
                    "reason_code": review["reason_code"],
                    "recorded_at": review["recorded_at"],
                    "reviewer": review["reviewer"] if include_raw_ids else _pseudonymize_identifier(review["reviewer"], salt=salt, prefix="reviewer"),
                    "reason": reason if include_free_text else "",
                    "notes": notes if include_free_text else "",
                }
                review_payload["edited_value"] = edited_value
                reviews_payload.append(review_payload)

            outcomes_payload = []
            for outcome in outcomes:
                outcome_payload = {
                    "outcome": outcome["outcome"],
                    "observed_signal": outcome["observed_signal"],
                    "observed_value": outcome["observed_value"],
                    "recorded_at": outcome["recorded_at"],
                    "reporter": outcome["reporter"] if include_raw_ids else _pseudonymize_identifier(outcome["reporter"], salt=salt, prefix="owner"),
                    "notes": outcome["notes"] if include_free_text else "",
                }
                outcomes_payload.append(outcome_payload)

            events_payload = []
            for event in events:
                event_payload = {
                    "event_type": event["event_type"],
                    "recorded_at": event["recorded_at"],
                    "actor": event["actor"] if include_raw_ids else _pseudonymize_identifier(event["actor"], salt=salt, prefix="actor"),
                    "payload": self._json_or_default(event["payload"], default={}),
                }
                events_payload.append(event_payload)

            records.append(
                {
                    "action_id": action_id_raw if include_raw_ids else _pseudonymize_identifier(action_id_raw, salt=salt, prefix="action"),
                    "scope_fingerprint": row["scope_fingerprint"],
                    "scope_kind": row["scope_kind"],
                    "scope_id": scope_id_raw if include_raw_ids else _pseudonymize_identifier(scope_id_raw, salt=salt, prefix="scope"),
                    "action_type": row["action_type"],
                    "specific_action": row["specific_action"],
                    "rationale": row["rationale"] if include_free_text else "",
                    "proposed_owner": row["proposed_owner"] if include_raw_ids else _pseudonymize_identifier(row["proposed_owner"], salt=salt, prefix="owner"),
                    "owner_confidence": row["owner_confidence"],
                    "urgency": row["urgency"],
                    "rank": row["rank"],
                    "priority_score": row["priority_score"],
                    "timing_window": row["timing_window"],
                    "effort": row["effort"],
                    "confidence": row["confidence"],
                    "expected_outcome": row["expected_outcome"] if include_free_text else "",
                    "measurable_success_signal": row["measurable_success_signal"] if include_free_text else "",
                    "triggering_finding_ids": self._json_or_default(row["triggering_finding_ids"], default=[]),
                    "evidence_ids": self._json_or_default(row["evidence_ids"], default=[]),
                    "dependencies": self._json_or_default(row["dependencies_json"], default=[]),
                    "ranking_factors": self._json_or_default(row["ranking_factors_json"], default={}),
                    "analysis_fingerprint": row["analysis_fingerprint"],
                    "analysis_request_fingerprint": row["analysis_request_fingerprint"],
                    "analysis_comparison_scope_fingerprint": row["analysis_comparison_scope_fingerprint"],
                    "analysis_snapshot_path": row["analysis_snapshot_path"],
                    "review_state": row["review_state"],
                    "reviewed_at": row["reviewed_at"],
                    "reviewer": reviewer if include_raw_ids else _pseudonymize_identifier(reviewer, salt=salt, prefix="reviewer"),
                    "review_reason": review_reason if include_free_text else "",
                    "review_reason_code": row["review_reason_code"] or "",
                    "is_active": bool(row["is_active"]),
                    "recurrence_depth": int(row["recurrence_depth"]),
                    "recurrence_parent_action_id": row["recurrence_parent_action_id"] or "",
                    "recurrence_previous_analysis_fingerprint": row[
                        "recurrence_previous_analysis_fingerprint"
                    ]
                    or "",
                    "review_edited_value": self._json_or_default(
                        row["review_edited_value_json"], default=None
                    ),
                    "reviews": reviews_payload,
                    "outcomes": outcomes_payload,
                    "events": events_payload,
                }
            )
        return records

    def export_feedback(
        self,
        snapshot_path: str | Path,
        *,
        include_raw_ids: bool = False,
        include_free_text: bool = False,
        export_salt: str | None = None,
    ) -> Dict[str, Any]:
        """
        Build a privacy-safe calibration-feedback export payload for an analysis snapshot.

        The default behavior intentionally excludes raw identifiers and free-text fields.
        """
        bundle = self.load_bundle(snapshot_path)
        scope_fp = bundle.context.comparison_scope_fingerprint
        self.sync_from_snapshot(snapshot_path)

        salt = _stable_salt(export_salt)
        with self._connection() as connection:
            cursor = connection.cursor()
            records = self._build_feedback_records(
                cursor,
                scope_fp,
                salt=salt,
                include_raw_ids=include_raw_ids,
                include_free_text=include_free_text,
            )

        manifest = {
            "schema_version": _FEEDBACK_EXPORT_SCHEMA_VERSION,
            "exported_at": _now_utc(),
            "analysis_id": _safe_text(bundle.context.request_fingerprint),
            "analysis_fingerprint": bundle.analysis_fingerprint,
            "analysis_snapshot_path": str(snapshot_path),
            "scope_fingerprint": scope_fp,
            "request_fingerprint": bundle.context.request_fingerprint,
            "included_scope": scope_fp,
            "include_raw_ids": bool(include_raw_ids),
            "include_free_text": bool(include_free_text),
            "record_count": len(records),
            "fields_included": [
                "action_id",
                "scope_fingerprint",
                "scope_kind",
                "scope_id",
                "action_type",
                "specific_action",
                "rationale",
                "proposed_owner",
                "owner_confidence",
                "urgency",
                "rank",
                "priority_score",
                "timing_window",
                "effort",
                "confidence",
                "expected_outcome",
                "measurable_success_signal",
                "triggering_finding_ids",
                "evidence_ids",
                "dependencies",
                "ranking_factors",
                "analysis_fingerprint",
                "analysis_request_fingerprint",
                "analysis_comparison_scope_fingerprint",
                "analysis_snapshot_path",
                "review_state",
                "review_reason_code",
                "reviewed_at",
                "reviewer",
                "recurrence_depth",
                "recurrence_parent_action_id",
                "recurrence_previous_analysis_fingerprint",
                "is_active",
                "reviews",
                "outcomes",
                "events",
            ],
            "fields_excluded_by_default": [
                "free_text_fields",
                "raw_customer_or_subscription_ids",
                "raw_action_identifiers_unhashed",
                "raw_urls",
                "credentials",
            ],
            "per_export_salt": salt,
            "causality_note": "Outcome temporal alignment is not causal without explicit human confirmation.",
        }

        return {
            "manifest": manifest,
            "records": records,
        }

    def _record_event(
        self,
        cursor: sqlite3.Cursor,
        action_id: str,
        scope_fp: str,
        event_type: str,
        actor: str,
        details: str = "{}",
    ) -> None:
        cursor.execute(
            """
            INSERT INTO decision_ops_events (
                action_id, scope_fingerprint, event_type, actor, event_payload_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (action_id, scope_fp, event_type, actor, _safe_text(details, default="{}"), _now_utc()),
        )

    def queue(self, snapshot_path: str | Path) -> List[Dict[str, Any]]:
        metadata = self.sync_from_snapshot(snapshot_path)
        scope_fp = metadata["scope_fingerprint"]
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT * FROM decision_ops_actions
                WHERE scope_fingerprint = ? AND is_active = 1
                ORDER BY review_state ASC, priority_score DESC, rank ASC, action_id ASC
                """,
                (scope_fp,),
            )
            rows = cursor.fetchall()
            payload: List[Dict[str, Any]] = []
            for row in rows:
                action_payload = {
                    "action_id": row["action_id"],
                    "scope_fingerprint": row["scope_fingerprint"],
                    "scope_kind": row["scope_kind"],
                    "scope_id": row["scope_id"],
                    "action_type": row["action_type"],
                    "specific_action": row["specific_action"],
                    "rationale": row["rationale"],
                    "triggering_finding_ids": json.loads(row["triggering_finding_ids"] or "[]"),
                    "evidence_ids": json.loads(row["evidence_ids"] or "[]"),
                    "proposed_owner": row["proposed_owner"],
                    "owner_confidence": row["owner_confidence"],
                    "urgency": row["urgency"],
                    "rank": int(row["rank"]),
                    "priority_score": float(row["priority_score"]),
                    "timing_window": row["timing_window"],
                    "effort": row["effort"],
                    "confidence": row["confidence"],
                    "expected_outcome": row["expected_outcome"],
                    "measurable_success_signal": row["measurable_success_signal"],
                    "recommendation_source": row["recommendation_source"],
                    "ranking_factors": json.loads(row["ranking_factors_json"] or "{}"),
                    "dependencies": json.loads(row["dependencies_json"] or "[]"),
                    "analysis_fingerprint": row["analysis_fingerprint"],
                    "analysis_snapshot_path": row["analysis_snapshot_path"],
                    "analysis_request_fingerprint": row["analysis_request_fingerprint"],
                    "analysis_comparison_scope_fingerprint": row["analysis_comparison_scope_fingerprint"],
                    "review_state": row["review_state"],
                    "action_state": row["action_state"] or row["review_state"],
                    "reviewed_at": row["reviewed_at"],
                    "reviewed_by": row["reviewed_by"],
                    "review_reason": row["review_reason"],
                    "review_reason_code": row["review_reason_code"] or "as_original",
                    "review_edited_value": (
                        json.loads(row["review_edited_value_json"])
                        if row["review_edited_value_json"]
                        else None
                    ),
                    "review_notes": row["review_notes"],
                    "recurrence_depth": int(row["recurrence_depth"]),
                    "recurrence_parent_action_id": row["recurrence_parent_action_id"] or "",
                    "recurrence_previous_analysis_fingerprint": row[
                        "recurrence_previous_analysis_fingerprint"
                    ]
                    or "",
                    "is_active": bool(row["is_active"]),
                }
                action_payload["recent_events"] = self._events_for_action(
                    cursor, scope_fp, row["action_id"]
                )
                action_payload["events"] = action_payload["recent_events"]
                action_payload["recent_events"] = self._events_for_action_excluding_types(
                    cursor,
                    scope_fp,
                    row["action_id"],
                    {"action_synced"},
                )
                action_payload["reviews"] = self._reviews_for_action(
                    cursor, scope_fp, row["action_id"]
                )
                action_payload["outcomes"] = self._outcomes_for_action(
                    cursor, scope_fp, row["action_id"]
                )
                payload.append(action_payload)
            return payload

    def action_detail(self, snapshot_path: str | Path, action_id: str) -> Dict[str, Any]:
        bundle = self.load_bundle(snapshot_path)
        scope_fp = bundle.context.comparison_scope_fingerprint
        self.sync_from_snapshot(snapshot_path)
        with self._connection() as connection:
            cursor = connection.cursor()
            row = self._resolve_action_row(cursor, scope_fp, action_id)
            if row is None:
                return {}
            action_payload = {
                "action_id": row["action_id"],
                "scope_fingerprint": row["scope_fingerprint"],
                "scope_kind": row["scope_kind"],
                "scope_id": row["scope_id"],
                "action_type": row["action_type"],
                "specific_action": row["specific_action"],
                "rationale": row["rationale"],
                "triggering_finding_ids": json.loads(row["triggering_finding_ids"] or "[]"),
                "evidence_ids": json.loads(row["evidence_ids"] or "[]"),
                "proposed_owner": row["proposed_owner"],
                "owner_confidence": row["owner_confidence"],
                "urgency": row["urgency"],
                "rank": int(row["rank"]),
                "priority_score": float(row["priority_score"]),
                "timing_window": row["timing_window"],
                "effort": row["effort"],
                "confidence": row["confidence"],
                "expected_outcome": row["expected_outcome"],
                "measurable_success_signal": row["measurable_success_signal"],
                "recommendation_source": row["recommendation_source"],
                "ranking_factors": json.loads(row["ranking_factors_json"] or "{}"),
                "dependencies": json.loads(row["dependencies_json"] or "[]"),
                "analysis_fingerprint": row["analysis_fingerprint"],
                "analysis_snapshot_path": row["analysis_snapshot_path"],
                "analysis_request_fingerprint": row["analysis_request_fingerprint"],
                "analysis_comparison_scope_fingerprint": row["analysis_comparison_scope_fingerprint"],
                "review_state": row["review_state"],
                "action_state": row["action_state"] or row["review_state"],
                "reviewed_at": row["reviewed_at"],
                "reviewed_by": row["reviewed_by"],
                "review_reason": row["review_reason"],
                "review_reason_code": row["review_reason_code"] or "as_original",
                "review_edited_value": (
                    json.loads(row["review_edited_value_json"])
                    if row["review_edited_value_json"]
                    else None
                ),
                "review_notes": row["review_notes"],
                "recurrence_depth": int(row["recurrence_depth"]),
                "recurrence_parent_action_id": row["recurrence_parent_action_id"] or "",
                "recurrence_previous_analysis_fingerprint": row[
                    "recurrence_previous_analysis_fingerprint"
                ]
                or "",
                "is_active": bool(row["is_active"]),
            }
            action_payload["events"] = self._events_for_action(
                cursor, scope_fp, row["action_id"]
            )
            action_payload["recent_events"] = self._events_for_action_excluding_types(
                cursor,
                scope_fp,
                row["action_id"],
                {"action_synced"},
            )
            action_payload["reviews"] = self._reviews_for_action(
                cursor, scope_fp, row["action_id"]
            )
            action_payload["outcomes"] = self._outcomes_for_action(
                cursor, scope_fp, row["action_id"]
            )
            return action_payload

    def review(
        self,
        snapshot_path: str | Path,
        action_id: str,
        decision: str,
        reviewer: str,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        reason_code: Optional[str] = None,
        edited_value: Optional[Any] = None,
        analysis_fingerprint: Optional[str] = None,
        expected_review_state: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        bundle = self.load_bundle(snapshot_path)
        scope_fp = bundle.context.comparison_scope_fingerprint
        normalized = _normalize_decision(decision)
        normalized_state = _normalize_decision_state(normalized)
        normalized_reason_code = _normalize_reason_code(reason_code)
        normalized_edited_value = _safe_json(edited_value) if edited_value is not None else None
        if normalized in _REASON_CODE_REQUIRED_DECISIONS and normalized_reason_code == "as_original":
            raise ValueError("reason_code_required")
        self.sync_from_snapshot(snapshot_path)
        with self._connection() as connection:
            cursor = connection.cursor()
            row = self._resolve_action_row(cursor, scope_fp, action_id)
            if row is None:
                raise ValueError("action_not_found")
            resolved_action_id = row["action_id"]
            if row["is_active"] != 1:
                raise ValueError("analysis_stale")
            if analysis_fingerprint and analysis_fingerprint != row["analysis_fingerprint"]:
                raise ValueError("analysis_stale")
            if expected_review_state:
                expected_state = _normalize_review_state(expected_review_state)
                current_state = _normalize_review_state(row["review_state"])
                if expected_state != current_state:
                    raise ValueError("concurrent_review_conflict")
            resolved_idempotency_key = _safe_text(idempotency_key)
            prior_review = self._review_by_idempotency_key(
                cursor,
                scope_fp,
                resolved_action_id,
                resolved_idempotency_key,
            )
            if prior_review is not None:
                return {
                    "action_id": resolved_action_id,
                    "scope_fingerprint": scope_fp,
                    "decision": _safe_text(prior_review["decision"], default=normalized),
                    "action_state": normalized_state,
                    "reviewed_at": prior_review["recorded_at"],
                    "reviewer": _safe_text(prior_review["reviewer"]),
                    "reason": _safe_text(prior_review["reason"]),
                    "reason_code": _safe_text(prior_review["reason_code"], default="as_original"),
                    "notes": _safe_text(prior_review["notes"]),
                    "edited_value": self._json_or_default(
                        prior_review["edited_value_json"], default=None
                    ),
                    "action_lifecycle_state": self._coerce_review_row_action_state(row),
                }
            now = _now_utc()
            action_state = self._apply_action_state(
                cursor,
                scope_fp,
                resolved_action_id,
                next_state=_next_action_state_from_review(
                    current_state=self._coerce_review_row_action_state(row),
                    review_state=normalized,
                ),
                actor=_safe_text(reviewer),
                details={"decision": normalized, "decision_state": normalized_state},
            )
            cursor.execute(
                """
                UPDATE decision_ops_actions
                SET review_state = ?, reviewed_at = ?, reviewed_by = ?, review_reason = ?,
                    review_reason_code = ?, review_edited_value_json = ?, review_notes = ?
                WHERE action_id = ? AND scope_fingerprint = ?
                """,
                (
                    normalized_state,
                    now,
                    _safe_text(reviewer),
                    _safe_text(reason),
                    normalized_reason_code,
                    normalized_edited_value,
                    _safe_text(notes),
                    resolved_action_id,
                    scope_fp,
                ),
            )
            try:
                cursor.execute(
                    """
                    INSERT INTO decision_ops_reviews (
                        action_id, scope_fingerprint, decision, reviewer, reason, reason_code,
                        edited_value_json, notes, source_analysis_fingerprint, recorded_at,
                        idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_action_id,
                        scope_fp,
                        normalized,
                        _safe_text(reviewer),
                        _safe_text(reason),
                        normalized_reason_code,
                        normalized_edited_value,
                        _safe_text(notes),
                        _safe_text(analysis_fingerprint or row["analysis_fingerprint"]),
                        now,
                        resolved_idempotency_key,
                    ),
                )
            except sqlite3.IntegrityError:
                prior_review = self._review_by_idempotency_key(
                    cursor,
                    scope_fp,
                    resolved_action_id,
                    resolved_idempotency_key,
                )
                if prior_review is not None:
                    return {
                        "action_id": resolved_action_id,
                        "scope_fingerprint": scope_fp,
                        "decision": _safe_text(
                            prior_review["decision"], default=normalized
                        ),
                        "action_state": normalized_state,
                        "reviewed_at": prior_review["recorded_at"],
                        "reviewer": _safe_text(prior_review["reviewer"]),
                        "reason": _safe_text(prior_review["reason"]),
                        "reason_code": _safe_text(
                            prior_review["reason_code"], default="as_original"
                        ),
                        "notes": _safe_text(prior_review["notes"]),
                        "edited_value": self._json_or_default(
                            prior_review["edited_value_json"], default=None
                        ),
                        "action_lifecycle_state": self._coerce_review_row_action_state(row),
                    }
                raise
            self._record_event(
                cursor,
                resolved_action_id,
                scope_fp,
                f"review_{normalized_state}",
                actor=_safe_text(reviewer),
                details=_safe_json(
                    {
                        "decision": normalized,
                        "decision_state": normalized_state,
                        "reason_code": normalized_reason_code,
                        "reason": reason,
                        "notes": notes,
                        "edited_value": edited_value,
                        "analysis_fingerprint": _safe_text(analysis_fingerprint or row["analysis_fingerprint"]),
                    }
                ),
            )
            return {
                "action_id": resolved_action_id,
                "scope_fingerprint": scope_fp,
                "decision": normalized,
                "action_state": normalized_state,
                "reviewed_at": now,
                "reviewer": _safe_text(reviewer),
                "reason": _safe_text(reason),
                "reason_code": normalized_reason_code,
                "notes": _safe_text(notes),
                "edited_value": edited_value,
                "action_lifecycle_state": action_state,
            }

    def action_state(
        self,
        snapshot_path: str | Path,
        action_id: str,
        action_state: str,
        actor: str,
        *,
        expected_action_state: Optional[str] = None,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        bundle = self.load_bundle(snapshot_path)
        scope_fp = bundle.context.comparison_scope_fingerprint
        normalized_actor = _safe_text(actor, default="system")
        requested_state = _safe_text(action_state)
        if not requested_state:
            raise ValueError("invalid_action_state")
        normalized_state = _normalize_action_state(requested_state)
        normalized_state_map = {state.lower(): state for state in _VALID_ACTION_STATES}
        state_key = requested_state.casefold()
        if state_key not in normalized_state_map:
            raise ValueError("invalid_action_state")
        normalized_state = normalized_state_map[state_key]

        self.sync_from_snapshot(snapshot_path)
        with self._connection() as connection:
            cursor = connection.cursor()
            row = self._resolve_action_row(cursor, scope_fp, action_id)
            if row is None:
                raise ValueError("action_not_found")
            resolved_action_id = row["action_id"]
            if expected_action_state:
                expected_key = _safe_text(expected_action_state).casefold()
                if not expected_key:
                    raise ValueError("invalid_action_state")
                if expected_key not in normalized_state_map:
                    raise ValueError("invalid_action_state")
                current_state = self._coerce_review_row_action_state(row)
                if current_state.lower() != expected_key:
                    raise ValueError("concurrent_action_state_conflict")
            next_state = self._apply_action_state(
                cursor,
                scope_fp,
                resolved_action_id,
                next_state=normalized_state,
                actor=normalized_actor,
                details={"reason": reason, "notes": notes},
            )
            return {
                "action_id": resolved_action_id,
                "scope_fingerprint": scope_fp,
                "requested_action_state": normalized_state,
                "action_state": next_state,
                "action_lifecycle_state": next_state,
            }

    def outcome(
        self,
        snapshot_path: str | Path,
        action_id: str,
        outcome: str,
        observed_signal: str,
        observed_value: Optional[Any] = None,
        notes: Optional[str] = None,
        reporter: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        expected_action_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        bundle = self.load_bundle(snapshot_path)
        scope_fp = bundle.context.comparison_scope_fingerprint
        self.sync_from_snapshot(snapshot_path)
        normalized = _normalize_outcome(outcome)
        with self._connection() as connection:
            cursor = connection.cursor()
            row = self._resolve_action_row(cursor, scope_fp, action_id)
            if row is None:
                raise ValueError("action_not_found")
            resolved_action_id = row["action_id"]
            if expected_action_state:
                expected_key = _safe_text(expected_action_state).casefold()
                if not expected_key:
                    raise ValueError("invalid_action_state")
                if expected_key not in {state.lower() for state in _VALID_ACTION_STATES}:
                    raise ValueError("invalid_action_state")
                current_state = self._coerce_review_row_action_state(row)
                if current_state.lower() != expected_key:
                    raise ValueError("concurrent_action_state_conflict")
            resolved_idempotency_key = _safe_text(idempotency_key)
            prior_outcome = self._outcome_by_idempotency_key(
                cursor,
                scope_fp,
                resolved_action_id,
                resolved_idempotency_key,
            )
            if prior_outcome is not None:
                return {
                    "action_id": resolved_action_id,
                    "scope_fingerprint": scope_fp,
                    "outcome": _safe_text(prior_outcome["outcome"], default="unknown"),
                    "observed_signal": _safe_text(prior_outcome["observed_signal"]),
                    "observed_value": self._json_or_default(
                        prior_outcome["observed_value"], default=None
                    ),
                    "notes": _safe_text(prior_outcome["notes"]),
                    "reporter": _safe_text(prior_outcome["reporter"]),
                    "recorded_at": prior_outcome["recorded_at"],
                    "action_lifecycle_state": self._coerce_review_row_action_state(row),
                }
            now = _now_utc()
            action_state = self._apply_action_state(
                cursor,
                scope_fp,
                resolved_action_id,
                next_state=_next_action_state_from_outcome(
                    current_state=self._coerce_review_row_action_state(row),
                    outcome=normalized,
                ),
                actor=_safe_text(reporter, default="system"),
                details={"outcome": normalized, "observed_signal": observed_signal},
            )
            try:
                cursor.execute(
                    """
                    INSERT INTO decision_ops_outcomes (
                        action_id, scope_fingerprint, outcome, observed_signal,
                        observed_value, notes, reporter, recorded_at, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_action_id,
                        scope_fp,
                        normalized,
                        _safe_text(observed_signal),
                        _safe_json(observed_value),
                        _safe_text(notes),
                        _safe_text(reporter),
                        now,
                        resolved_idempotency_key,
                    ),
                )
            except sqlite3.IntegrityError:
                prior_outcome = self._outcome_by_idempotency_key(
                    cursor,
                    scope_fp,
                    resolved_action_id,
                    resolved_idempotency_key,
                )
                if prior_outcome is not None:
                    return {
                        "action_id": resolved_action_id,
                        "scope_fingerprint": scope_fp,
                        "outcome": _safe_text(
                            prior_outcome["outcome"], default="unknown"
                        ),
                        "observed_signal": _safe_text(prior_outcome["observed_signal"]),
                        "observed_value": self._json_or_default(
                            prior_outcome["observed_value"], default=None
                        ),
                        "notes": _safe_text(prior_outcome["notes"]),
                        "reporter": _safe_text(prior_outcome["reporter"]),
                        "recorded_at": prior_outcome["recorded_at"],
                        "action_lifecycle_state": self._coerce_review_row_action_state(row),
                    }
                raise
            self._record_event(
                cursor,
                resolved_action_id,
                scope_fp,
                "outcome_recorded",
                actor=_safe_text(reporter, default="system"),
                details=_safe_json({
                    "outcome": normalized,
                    "observed_signal": observed_signal,
                    "observed_value": observed_value,
                }),
            )
            return {
                "action_id": resolved_action_id,
                "scope_fingerprint": scope_fp,
                "outcome": normalized,
                "observed_signal": _safe_text(observed_signal),
                "observed_value": observed_value,
                "notes": _safe_text(notes),
                "reporter": _safe_text(reporter),
                "recorded_at": now,
                "action_lifecycle_state": action_state,
            }


__all__ = ["DecisionOpsStore"]
