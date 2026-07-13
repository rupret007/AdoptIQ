"""Decision Operations persistence layer for recommendation review workflows.

This module keeps a minimal, auditable record of canonical recommendation
decisions without mutating the immutable Decision Intelligence bundle.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from decision_intelligence import AnalysisBundle, RecommendedAction


_DEFAULT_SCOPE = "customer"
_VALID_REVIEW_DECISIONS = frozenset({"accept", "deny", "edit", "defer"})
_VALID_OUTCOMES = frozenset({"succeeded", "not_succeeded", "in_progress", "unknown"})


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


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False, sort_keys=True)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_decision(value: Any) -> str:
    cleaned = _safe_text(value).casefold()
    if cleaned in {"approve", "approved", "accept", "okay", "ok"}:
        return "accept"
    if cleaned in _VALID_REVIEW_DECISIONS:
        return cleaned
    raise ValueError(f"Unsupported review decision: {value!r}")


def _normalize_outcome(value: Any) -> str:
    cleaned = _safe_text(value).casefold()
    if cleaned in _VALID_OUTCOMES:
        return cleaned
    return "unknown"


def _extract_recommended_actions(bundle: AnalysisBundle) -> List[RecommendedAction]:
    by_id: Dict[str, RecommendedAction] = {}
    for customer in bundle.customers:
        for action in customer.recommended_actions:
            if action.action_id and action.action_id not in by_id:
                by_id[action.action_id] = action
    for action in bundle.portfolio.recommended_actions:
        if action.action_id and action.action_id not in by_id:
            by_id[action.action_id] = action
    return sorted(
        by_id.values(),
        key=lambda item: (-item.priority_score, item.scope_id.casefold(), item.action_id),
    )


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
                    reviewed_at TEXT,
                    reviewed_by TEXT,
                    review_reason TEXT,
                    review_notes TEXT,
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
                    recorded_at TEXT NOT NULL,
                    UNIQUE (action_id, scope_fingerprint, recorded_at, decision),
                    FOREIGN KEY (action_id, scope_fingerprint)
                        REFERENCES decision_ops_actions(action_id, scope_fingerprint)
                        ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ops_reviews_action"
                " ON decision_ops_reviews(action_id, scope_fingerprint, recorded_at DESC)"
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
                    FOREIGN KEY (action_id, scope_fingerprint)
                        REFERENCES decision_ops_actions(action_id, scope_fingerprint)
                        ON DELETE CASCADE
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ops_outcomes_action"
                " ON decision_ops_outcomes(action_id, scope_fingerprint, recorded_at DESC)"
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
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_ops_events_action"
                " ON decision_ops_events(action_id, scope_fingerprint, recorded_at DESC)"
            )

    @staticmethod
    def _ensure_action_columns(cursor: sqlite3.Cursor) -> None:
        """Backfill any missing columns into an existing action table.

        Legacy installs may have initialized the table with a narrower set of
        fields. ``CREATE TABLE IF NOT EXISTS`` won't alter that in-place, so we
        patch the schema proactively before writes that require newly-added
        columns.
        """
        existing = {
            row[1]
            for row in cursor.execute("PRAGMA table_info(decision_ops_actions)")
            if len(row) >= 2
        }
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
            "reviewed_at": "TEXT",
            "reviewed_by": "TEXT",
            "review_reason": "TEXT",
            "review_notes": "TEXT",
            "last_synced_at": "TEXT NOT NULL DEFAULT ''",
        }

        for name, ddl in desired_columns.items():
            if name in existing:
                continue
            cursor.execute(f"ALTER TABLE decision_ops_actions ADD COLUMN {name} {ddl}")

    def load_bundle(self, snapshot_path: str | Path) -> AnalysisBundle:
        return AnalysisBundle.load(Path(snapshot_path))

    def _action_payload(
        self, action: RecommendedAction, bundle: AnalysisBundle, scope_fp: str
    ) -> Dict[str, Any]:
        return {
            "action_id": action.action_id,
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

    def _upsert_action(
        self,
        cursor: sqlite3.Cursor,
        action: RecommendedAction,
        bundle: AnalysisBundle,
        scope_fp: str,
        snapshot_path: str,
    ) -> None:
        payload = self._action_payload(action, bundle, scope_fp)
        now = _now_utc()
        cursor.execute(
            """
            SELECT review_state, reviewed_at, reviewed_by, review_reason, review_notes
            FROM decision_ops_actions
            WHERE action_id = ? AND scope_fingerprint = ?
            """,
            (payload["action_id"], scope_fp),
        )
        existing = cursor.fetchone()
        if existing is None:
            cursor.execute(
                """
                INSERT INTO decision_ops_actions (
                    action_id, scope_fingerprint, scope_kind, scope_id, action_type,
                    specific_action, rationale, triggering_finding_ids, evidence_ids,
                    proposed_owner, owner_confidence, urgency, rank, priority_score,
                    timing_window, effort, confidence, expected_outcome,
                    measurable_success_signal, recommendation_source,
                    ranking_factors_json, dependencies_json,
                    analysis_fingerprint, analysis_snapshot_path,
                    analysis_request_fingerprint, analysis_comparison_scope_fingerprint,
                    is_active, review_state, reviewed_at, reviewed_by,
                    review_reason, review_notes, last_synced_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    payload["action_id"],
                    scope_fp,
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
                    payload["recommendation_source"],
                    _safe_json(payload["ranking_factors"]),
                    _safe_json(action.dependencies),
                    bundle.analysis_fingerprint,
                    snapshot_path,
                    bundle.context.request_fingerprint,
                    scope_fp,
                    1,
                    "proposed",
                    None,
                    None,
                    None,
                    None,
                    now,
                ),
            )
            self._record_event(
                cursor,
                payload["action_id"],
                scope_fp,
                "action_synced",
                actor="system",
                details=_safe_json({"analysis_fingerprint": bundle.analysis_fingerprint}),
            )
            return

        cursor.execute(
            """
            UPDATE decision_ops_actions
            SET scope_kind = ?, scope_id = ?, action_type = ?, specific_action = ?,
                rationale = ?, triggering_finding_ids = ?, evidence_ids = ?,
                proposed_owner = ?, owner_confidence = ?, urgency = ?,
                rank = ?, priority_score = ?, timing_window = ?, effort = ?, confidence = ?,
                expected_outcome = ?, measurable_success_signal = ?, recommendation_source = ?,
                ranking_factors_json = ?, dependencies_json = ?, analysis_fingerprint = ?,
                analysis_snapshot_path = ?, analysis_request_fingerprint = ?,
                analysis_comparison_scope_fingerprint = ?, is_active = 1, last_synced_at = ?
            WHERE action_id = ? AND scope_fingerprint = ?
            """,
            (
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
                bundle.analysis_fingerprint,
                snapshot_path,
                bundle.context.request_fingerprint,
                scope_fp,
                now,
                payload["action_id"],
                scope_fp,
            ),
        )

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
            for action in actions:
                self._upsert_action(
                    cursor,
                    action,
                    bundle,
                    scope_fp,
                    snapshot_path_str,
                )
            present_ids = tuple(action.action_id for action in actions)
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

    def _reviews_for_action(
        self, cursor: sqlite3.Cursor, scope_fp: str, action_id: str
    ) -> List[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT decision, reviewer, reason, notes, recorded_at
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
                "notes": record["notes"],
                "recorded_at": record["recorded_at"],
            }
            for record in cursor.fetchall()
        ]

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
                    "reviewed_at": row["reviewed_at"],
                    "reviewed_by": row["reviewed_by"],
                    "review_reason": row["review_reason"],
                    "review_notes": row["review_notes"],
                    "is_active": bool(row["is_active"]),
                }
                action_payload["recent_events"] = self._events_for_action(
                    cursor, scope_fp, row["action_id"]
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
            cursor.execute(
                "SELECT * FROM decision_ops_actions WHERE action_id = ? AND scope_fingerprint = ?",
                (action_id, scope_fp),
            )
            row = cursor.fetchone()
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
                "reviewed_at": row["reviewed_at"],
                "reviewed_by": row["reviewed_by"],
                "review_reason": row["review_reason"],
                "review_notes": row["review_notes"],
                "is_active": bool(row["is_active"]),
            }
            action_payload["events"] = self._events_for_action(
                cursor, scope_fp, row["action_id"]
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
    ) -> Dict[str, Any]:
        bundle = self.load_bundle(snapshot_path)
        scope_fp = bundle.context.comparison_scope_fingerprint
        normalized = _normalize_decision(decision)
        self.sync_from_snapshot(snapshot_path)
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT action_id, review_state FROM decision_ops_actions"
                " WHERE action_id = ? AND scope_fingerprint = ?",
                (action_id, scope_fp),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("action_not_found")
            now = _now_utc()
            cursor.execute(
                """
                UPDATE decision_ops_actions
                SET review_state = ?, reviewed_at = ?, reviewed_by = ?, review_reason = ?, review_notes = ?
                WHERE action_id = ? AND scope_fingerprint = ?
                """,
                (
                    normalized,
                    now,
                    _safe_text(reviewer),
                    _safe_text(reason),
                    _safe_text(notes),
                    action_id,
                    scope_fp,
                ),
            )
            cursor.execute(
                """
                INSERT INTO decision_ops_reviews (
                    action_id, scope_fingerprint, decision, reviewer, reason, notes, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    scope_fp,
                    normalized,
                    _safe_text(reviewer),
                    _safe_text(reason),
                    _safe_text(notes),
                    now,
                ),
            )
            self._record_event(
                cursor,
                action_id,
                scope_fp,
                "review_decision",
                actor=_safe_text(reviewer),
                details=_safe_json({"decision": normalized, "reason": reason, "notes": notes}),
            )
            return {
                "action_id": action_id,
                "scope_fingerprint": scope_fp,
                "decision": normalized,
                "reviewed_at": now,
                "reviewer": _safe_text(reviewer),
                "reason": _safe_text(reason),
                "notes": _safe_text(notes),
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
    ) -> Dict[str, Any]:
        bundle = self.load_bundle(snapshot_path)
        scope_fp = bundle.context.comparison_scope_fingerprint
        self.sync_from_snapshot(snapshot_path)
        normalized = _normalize_outcome(outcome)
        with self._connection() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT 1 FROM decision_ops_actions WHERE action_id = ? AND scope_fingerprint = ?",
                (action_id, scope_fp),
            )
            if cursor.fetchone() is None:
                raise ValueError("action_not_found")
            now = _now_utc()
            cursor.execute(
                """
                INSERT INTO decision_ops_outcomes (
                    action_id, scope_fingerprint, outcome, observed_signal,
                    observed_value, notes, reporter, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    scope_fp,
                    normalized,
                    _safe_text(observed_signal),
                    _safe_json(observed_value),
                    _safe_text(notes),
                    _safe_text(reporter),
                    now,
                ),
            )
            self._record_event(
                cursor,
                action_id,
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
                "action_id": action_id,
                "scope_fingerprint": scope_fp,
                "outcome": normalized,
                "observed_signal": _safe_text(observed_signal),
                "observed_value": observed_value,
                "notes": _safe_text(notes),
                "reporter": _safe_text(reporter),
                "recorded_at": now,
            }


__all__ = ["DecisionOpsStore"]
