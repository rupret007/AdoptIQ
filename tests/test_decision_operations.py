from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from types import SimpleNamespace
import pytest

import app_simple
from decision_operations import DecisionOpsStore


@dataclass
class _FakeDecisionOpsStore:
    queue_payload: list[dict] | None = None
    review_payload: dict | None = None
    outcome_payload: dict | None = None
    action_payload: dict | None = None
    export_payload: dict | None = None
    export_args: dict | None = None
    snapshot_path: str | None = None
    action_id: str | None = None
    decision_args: dict | None = None
    outcome_args: dict | None = None

    def queue(self, snapshot_path: str) -> list[dict]:
        self.snapshot_path = snapshot_path
        return self.queue_payload or []

    def review(
        self,
        snapshot_path: str,
        action_id: str,
        decision: str,
        reviewer: str,
        reason: str | None = None,
        notes: str | None = None,
        reason_code: str | None = None,
        edited_value: object | None = None,
        analysis_fingerprint: str | None = None,
    ) -> dict:
        self.snapshot_path = snapshot_path
        self.action_id = action_id
        self.decision_args = dict(
            snapshot_path=snapshot_path,
            action_id=action_id,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            notes=notes,
            reason_code=reason_code,
            edited_value=edited_value,
            analysis_fingerprint=analysis_fingerprint,
        )
        return self.review_payload or {}

    def outcome(
        self,
        snapshot_path: str,
        action_id: str,
        outcome: str,
        observed_signal: object,
        observed_value: object = None,
        notes: str | None = None,
        reporter: str | None = None,
    ) -> dict:
        self.snapshot_path = snapshot_path
        self.action_id = action_id
        self.outcome_args = dict(
            snapshot_path=snapshot_path,
            action_id=action_id,
            outcome=outcome,
            observed_signal=observed_signal,
            observed_value=observed_value,
            notes=notes,
            reporter=reporter,
        )
        return self.outcome_payload or {}

    def action_detail(self, snapshot_path: str, action_id: str) -> dict:
        self.snapshot_path = snapshot_path
        self.action_id = action_id
        return self.action_payload or {}

    def export_feedback(
        self,
        snapshot_path: str,
        *,
        include_raw_ids: bool,
        include_free_text: bool,
        export_salt: str | None = None,
    ) -> dict:
        self.snapshot_path = snapshot_path
        self.export_args = {
            "snapshot_path": snapshot_path,
            "include_raw_ids": include_raw_ids,
            "include_free_text": include_free_text,
            "export_salt": export_salt,
        }
        return self.export_payload or {}


class _FakeAction(SimpleNamespace):
    action_id: str
    scope_kind: str
    scope_id: str
    action_type: str
    specific_action: str
    rationale: str
    triggering_finding_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    proposed_owner: str
    owner_confidence: str
    urgency: str
    rank: int
    priority_score: float
    ranking_factors: dict[str, float]
    dependencies: tuple[str, ...]
    timing_window: str
    effort: str
    confidence: str
    expected_outcome: str
    measurable_success_signal: str
    recommendation_source: str


def _mk_action(action_id: str, scope_id: str) -> _FakeAction:
    return _FakeAction(
        action_id=action_id,
        scope_kind="customer",
        scope_id=scope_id,
        action_type="retain",
        specific_action=f"Take action {action_id}",
        rationale=f"Rationale for {action_id}",
        triggering_finding_ids=("finding:shared",),
        evidence_ids=("evidence:shared",),
        proposed_owner="CSM",
        owner_confidence="HIGH",
        urgency="within 7 days",
        rank=1,
        priority_score=72.0,
        ranking_factors={"risk": 72.0},
        dependencies=(),
        timing_window="7 days",
        effort="medium",
        confidence="HIGH",
        expected_outcome="risk reduced",
        measurable_success_signal="owner closes mitigation",
        recommendation_source="decision-intelligence-v2",
    )


def _mk_bundle(
    *, scope_fingerprint: str, analysis_fingerprint: str, as_of_time: str
):
    return SimpleNamespace(
        analysis_fingerprint=analysis_fingerprint,
        context=SimpleNamespace(
            as_of_time=as_of_time,
            request_fingerprint="request:abc",
            comparison_scope_fingerprint=scope_fingerprint,
        ),
        customers=tuple(),
        portfolio=SimpleNamespace(recommended_actions=()),
    )


def test_sync_from_snapshot_stores_actions_and_snapshot_path(tmp_path, monkeypatch):
    snapshot = tmp_path / "decision-intelligence-snapshot.json"
    snapshot.write_text("{}")
    duplicate = _mk_action("action:shared", "customer:acme")
    only_once = _mk_action("action:onlyonce", "customer:beta")
    portfolio_action = _mk_action("action:shared", "portfolio:all")
    store = DecisionOpsStore(db_path=tmp_path / "decision_ops.db")
    bundle = _mk_bundle(
        scope_fingerprint="scope:one",
        analysis_fingerprint="analysis:one",
        as_of_time="2026-07-13T00:00:00Z",
    )
    bundle.customers = (
        SimpleNamespace(recommended_actions=(duplicate, only_once)),
    )
    bundle.portfolio = SimpleNamespace(recommended_actions=(portfolio_action,))

    monkeypatch.setattr(store, "load_bundle", lambda *_: bundle)
    metadata = store.sync_from_snapshot(snapshot)
    queue = store.queue(snapshot)

    assert metadata["action_count"] == 3
    assert metadata["analysis_fingerprint"] == "analysis:one"
    assert metadata["snapshot_path"] == str(snapshot)
    assert len(queue) == 3
    assert queue[0]["scope_id"] in {"customer:acme", "customer:beta", "portfolio:all"}
    assert {row["scope_id"] for row in queue} == {
        "customer:acme",
        "customer:beta",
        "portfolio:all",
    }
    assert len({row["action_id"] for row in queue}) == 3
    shared_customer = next(row for row in queue if row["scope_id"] == "customer:acme")
    shared_portfolio = next(row for row in queue if row["scope_id"] == "portfolio:all")
    assert shared_customer["action_id"] == "action:shared"
    assert shared_portfolio["action_id"] != "action:shared"
    assert shared_portfolio["action_id"].startswith("action:shared")
    assert shared_customer["analysis_snapshot_path"] == str(snapshot)
    assert shared_customer["analysis_fingerprint"] == "analysis:one"

    with sqlite3.connect(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        stored_scope = {
            row["scope_id"]
            for row in connection.execute(
                "SELECT scope_id FROM decision_ops_actions WHERE scope_fingerprint = ?",
                ("scope:one",),
            )
        }
        stored_snapshots = {
            row["analysis_snapshot_path"]
            for row in connection.execute(
                "SELECT analysis_snapshot_path FROM decision_ops_actions WHERE scope_fingerprint = ?",
                ("scope:one",),
            )
        }
        portfolio_rows = {
            row["scope_id"]: (row["action_id"], row["source_action_id"])
            for row in connection.execute(
                "SELECT scope_id, action_id, source_action_id FROM decision_ops_actions WHERE scope_fingerprint = ?",
                ("scope:one",),
            )
        }

    assert stored_scope == {"customer:acme", "customer:beta", "portfolio:all"}
    assert stored_snapshots == {str(snapshot)}
    assert portfolio_rows["portfolio:all"][1] == "action:shared"
    assert portfolio_rows["portfolio:all"][0].startswith("action:shared:")


def test_sync_from_snapshot_preserves_history_when_signature_matches(tmp_path, monkeypatch):
    snapshot = tmp_path / "signature-stable.json"
    snapshot.write_text("{}")
    action_v1 = _mk_action("action:first", "customer:acme")
    action_v2 = _mk_action("action:renamed", "customer:acme")
    action_v2.specific_action = "Updated prose for same recommendation"

    store = DecisionOpsStore(db_path=tmp_path / "decision_ops.db")
    bundle_v1 = _mk_bundle(
        scope_fingerprint="scope:stable",
        analysis_fingerprint="analysis:stable-1",
        as_of_time="2026-07-13T00:00:00Z",
    )
    bundle_v1.customers = (SimpleNamespace(recommended_actions=(action_v1,)),)

    monkeypatch.setattr(store, "load_bundle", lambda *_: bundle_v1)
    store.sync_from_snapshot(snapshot)
    store.review(snapshot, "action:first", "approve", "alice", analysis_fingerprint="analysis:stable-1")

    with sqlite3.connect(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        before = connection.execute(
            "SELECT action_id, review_state, source_action_id, action_signature FROM decision_ops_actions WHERE scope_fingerprint = 'scope:stable'"
        ).fetchone()
        assert before["action_id"] == "action:first"
        assert before["review_state"] == "accepted"

    bundle_v2 = _mk_bundle(
        scope_fingerprint="scope:stable",
        analysis_fingerprint="analysis:stable-2",
        as_of_time="2026-07-13T01:00:00Z",
    )
    bundle_v2.customers = (SimpleNamespace(recommended_actions=(action_v2,)),)
    monkeypatch.setattr(store, "load_bundle", lambda *_: bundle_v2)
    store.sync_from_snapshot(snapshot)

    with sqlite3.connect(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = list(
            connection.execute(
                "SELECT action_id, review_state, source_action_id, action_signature FROM decision_ops_actions WHERE scope_fingerprint = 'scope:stable'"
            )
        )

    assert len(rows) == 1
    assert rows[0]["action_id"] == "action:first"
    assert rows[0]["review_state"] == "accepted"
    assert rows[0]["source_action_id"] == "action:renamed"


def test_refresh_updates_action_liveness(tmp_path, monkeypatch):
    store = DecisionOpsStore(db_path=tmp_path / "decision_ops.db")
    active_scope = "scope:active"
    customer_a = SimpleNamespace(recommended_actions=(_mk_action("action:keep", "customer:one"),))
    customer_b = SimpleNamespace(
        recommended_actions=(_mk_action("action:drop", "customer:two"),)
    )
    bundle_initial = _mk_bundle(
        scope_fingerprint=active_scope,
        analysis_fingerprint="analysis:initial",
        as_of_time="2026-07-13T10:00:00Z",
    )
    bundle_initial.customers = (customer_a, customer_b)
    monkeypatch.setattr(store, "load_bundle", lambda *_: bundle_initial)
    store.sync_from_snapshot(tmp_path / "initial.json")
    detail = store.queue(tmp_path / "initial.json")
    assert {row["action_id"] for row in detail} == {"action:keep", "action:drop"}
    with sqlite3.connect(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        assert {
            row["action_id"]: bool(row["is_active"])
            for row in connection.execute(
                "SELECT action_id, is_active FROM decision_ops_actions WHERE scope_fingerprint = ?",
                (active_scope,),
            )
        } == {"action:keep": True, "action:drop": True}

    bundle_next = _mk_bundle(
        scope_fingerprint=active_scope,
        analysis_fingerprint="analysis:next",
        as_of_time="2026-07-13T11:00:00Z",
    )
    bundle_next.customers = (SimpleNamespace(recommended_actions=(customer_a.recommended_actions[0],)))
    monkeypatch.setattr(store, "load_bundle", lambda *_: bundle_next)
    store.sync_from_snapshot(tmp_path / "next.json")
    with sqlite3.connect(store.db_path) as connection:
        connection.row_factory = sqlite3.Row
        assert {
            row["action_id"]: bool(row["is_active"])
            for row in connection.execute(
                "SELECT action_id, is_active FROM decision_ops_actions WHERE scope_fingerprint = ?",
                (active_scope,),
            )
        } == {"action:keep": True, "action:drop": False}


def test_review_and_outcome_are_recorded(tmp_path, monkeypatch):
    store = DecisionOpsStore(db_path=tmp_path / "decision_ops.db")
    scope = "scope:review"
    action = _mk_action("action:review", "customer:alpha")
    bundle = _mk_bundle(
        scope_fingerprint=scope,
        analysis_fingerprint="analysis:review",
        as_of_time="2026-07-13T12:00:00Z",
    )
    bundle.customers = (SimpleNamespace(recommended_actions=(action,)),)
    monkeypatch.setattr(store, "load_bundle", lambda *_: bundle)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}")
    store.sync_from_snapshot(snapshot)

    reviewed = store.review(snapshot, "action:review", "approve", "alice", notes="approved now")
    outcome = store.outcome(snapshot, "action:review", "not_succeeded", "Owner not responded", observed_value=12)
    detail = store.action_detail(snapshot, "action:review")

    assert reviewed["decision"] == "accept"
    assert reviewed["reviewer"] == "alice"
    assert detail["review_state"] == "accepted"
    assert detail["reviews"][0]["decision"] == "accept"
    assert detail["reviews"][0]["reviewer"] == "alice"
    assert outcome["outcome"] == "not_succeeded"
    assert detail["outcomes"][0]["outcome"] == "not_succeeded"
    assert detail["recent_events"][0]["event_type"] in {"outcome_recorded", "review_accepted"}


def test_review_records_reason_code_and_edit_overlay(tmp_path, monkeypatch):
    store = DecisionOpsStore(db_path=tmp_path / "decision_ops.db")
    scope = "scope:overlay"
    action = _mk_action("action:overlay", "customer:alpha")
    bundle = _mk_bundle(
        scope_fingerprint=scope,
        analysis_fingerprint="analysis:overlay",
        as_of_time="2026-07-13T13:00:00Z",
    )
    bundle.customers = (SimpleNamespace(recommended_actions=(action,)),)
    monkeypatch.setattr(store, "load_bundle", lambda *_: bundle)

    snapshot = tmp_path / "overlay.json"
    snapshot.write_text("{}")
    store.sync_from_snapshot(snapshot)

    reviewed = store.review(
        snapshot,
        "action:overlay",
        "edit",
        "alice",
        reason="owner correction",
        reason_code="owner_corrected",
        edited_value={"proposed_owner": "Alice"},
        analysis_fingerprint="analysis:overlay",
    )
    detail = store.action_detail(snapshot, "action:overlay")

    assert reviewed["action_state"] == "accepted_with_edit"
    assert reviewed["decision"] == "edit"
    assert reviewed["reason_code"] == "owner_corrected"
    assert detail["review_state"] == "accepted_with_edit"
    assert detail["review_reason_code"] == "owner_corrected"
    assert detail["review_edited_value"] == {"proposed_owner": "Alice"}
    assert detail["reviews"][0]["decision"] == "edit"
    assert detail["reviews"][0]["reason_code"] == "owner_corrected"
    assert detail["reviews"][0]["edited_value"] == {"proposed_owner": "Alice"}


def test_review_rejects_inactive_or_stale_recommendation(tmp_path, monkeypatch):
    store = DecisionOpsStore(db_path=tmp_path / "decision_ops.db")
    scope = "scope:stale"
    keep = _mk_action("action:keep", "customer:one")
    stale = _mk_action("action:stale", "customer:two")

    initial = _mk_bundle(
        scope_fingerprint=scope,
        analysis_fingerprint="analysis:initial",
        as_of_time="2026-07-13T14:00:00Z",
    )
    initial.customers = (SimpleNamespace(recommended_actions=(keep, stale)),)
    monkeypatch.setattr(store, "load_bundle", lambda *_: initial)
    store.sync_from_snapshot(tmp_path / "initial.json")

    next_bundle = _mk_bundle(
        scope_fingerprint=scope,
        analysis_fingerprint="analysis:next",
        as_of_time="2026-07-13T15:00:00Z",
    )
    next_bundle.customers = (SimpleNamespace(recommended_actions=(keep,)),)
    monkeypatch.setattr(store, "load_bundle", lambda *_: next_bundle)
    snapshot_next = tmp_path / "next.json"
    snapshot_next.write_text("{}")
    store.sync_from_snapshot(snapshot_next)

    with pytest.raises(ValueError, match="analysis_stale"):
        store.review(
            snapshot_next,
            "action:stale",
            "accept",
            "alice",
            analysis_fingerprint="analysis:next",
        )


def test_init_db_upgrades_legacy_action_table_with_missing_columns(tmp_path):
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as connection:
        connection.execute(
            """
            CREATE TABLE decision_ops_actions (
                action_id TEXT NOT NULL,
                scope_fingerprint TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                PRIMARY KEY (action_id, scope_fingerprint)
            )
            """
        )
        connection.execute(
            "CREATE TABLE decision_ops_reviews (id INTEGER PRIMARY KEY)"
        )
        connection.execute(
            "CREATE TABLE decision_ops_outcomes (id INTEGER PRIMARY KEY)"
        )
        connection.execute(
            "CREATE TABLE decision_ops_events (id INTEGER PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO decision_ops_actions (action_id, scope_fingerprint, scope_id, action_type) VALUES (?, ?, ?, ?)",
            ("legacy:one", "scope:legacy", "customer:legacy", "old"),
        )
    DecisionOpsStore(db_path=legacy)

    with sqlite3.connect(legacy) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(decision_ops_actions)")
        }

    for expected in {
        "specific_action",
        "analysis_snapshot_path",
        "analysis_request_fingerprint",
        "analysis_comparison_scope_fingerprint",
        "rank",
        "priority_score",
        "is_active",
        "review_state",
        "review_reason_code",
        "review_edited_value_json",
        "last_synced_at",
    }:
        assert expected in columns


def test_decisionops_queue_uses_status_snapshot_path(client, monkeypatch, tmp_path):
    analysis_id = "analysis-status-1"
    snapshot_path = tmp_path / "analysis-snapshot.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[analysis_id] = {
            "status": "completed",
            "analysis_snapshot_path": str(snapshot_path),
        }

    fake_store = _FakeDecisionOpsStore(
        queue_payload=[
            {"action_id": "act:one", "review_state": "proposed"},
            {"action_id": "act:two", "review_state": "proposed"},
        ],
    )
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    response = client.get(f"/api/decisionops/queue/{analysis_id}")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["analysis_id"] == analysis_id
    assert payload["analysis_snapshot_path"] == str(snapshot_path)
    assert payload["actions"][0]["action_id"] == "act:one"
    assert fake_store.snapshot_path == str(snapshot_path)


def test_decisionops_queue_falls_back_to_report_history_when_status_missing(
    client,
    monkeypatch,
    tmp_path,
):
    analysis_id = "analysis-history-1"
    snapshot_path = tmp_path / "history-snapshot.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()

    monkeypatch.setattr(
        app_simple,
        "get_report_history",
        lambda: [
            {
                "request_id": analysis_id,
                "analysis_snapshot_path": str(snapshot_path),
            },
        ],
    )

    fake_store = _FakeDecisionOpsStore(
        queue_payload=[{"action_id": "act:history", "review_state": "proposed"}],
    )
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    response = client.get(f"/api/decisionops/queue/{analysis_id}")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["analysis_id"] == analysis_id
    assert payload["analysis_snapshot_path"] == str(snapshot_path)
    assert payload["actions"][0]["action_id"] == "act:history"


def test_decisionops_review_and_outcome_endpoints(client, monkeypatch, tmp_path):
    analysis_id = "analysis-review-1"
    snapshot_path = tmp_path / "review-snapshot.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[analysis_id] = {
            "status": "completed",
            "analysis_snapshot_path": str(snapshot_path),
        }

    fake_store = _FakeDecisionOpsStore(
        review_payload={"action_id": "act-review", "decision": "accept"},
        outcome_payload={"action_id": "act-review", "outcome": "succeeded"},
    )
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    review = client.post(
        "/api/decisionops/review",
        json={
            "analysis_id": analysis_id,
            "action_id": "act-review",
            "decision": "approve",
            "reviewer": "alice",
            "reason_code": "scope_corrected",
            "edited_value": {"specific_action": "Reworded action"},
            "analysis_fingerprint": "analysis-review-1",
        },
    )
    review_payload = review.get_json()
    assert review.status_code == 200
    assert review_payload["ok"] is True
    assert review_payload["decision"] == "accept"
    assert fake_store.decision_args["action_id"] == "act-review"
    assert fake_store.decision_args["reason_code"] == "scope_corrected"
    assert fake_store.decision_args["edited_value"] == {"specific_action": "Reworded action"}

    outcome = client.post(
        "/api/decisionops/outcome",
        json={
            "analysis_id": analysis_id,
            "action_id": "act-review",
            "outcome": "succeeded",
            "observed_signal": "owner response",
            "observed_value": {"value": 1},
        },
    )
    outcome_payload = outcome.get_json()
    assert outcome.status_code == 200
    assert outcome_payload["ok"] is True
    assert outcome_payload["outcome"] == "succeeded"


def test_decisionops_action_detail_endpoint(client, monkeypatch, tmp_path):
    analysis_id = "analysis-action-1"
    action_id = "act-action"
    snapshot_path = tmp_path / "action-snapshot.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[analysis_id] = {
            "status": "completed",
            "analysis_snapshot_path": str(snapshot_path),
        }

    fake_store = _FakeDecisionOpsStore(
        action_payload={
            "action_id": action_id,
            "review_state": "proposed",
            "scope_fingerprint": "scope:one",
        },
    )
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    response = client.get(f"/api/decisionops/action/{analysis_id}/{action_id}")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["action_id"] == action_id
    assert fake_store.action_id == action_id


def test_decisionops_workbench_route_shows_review_queue(client, monkeypatch, tmp_path):
    analysis_id = "analysis-workbench-ui"
    snapshot_path = tmp_path / "workbench-snapshot.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[analysis_id] = {
            "status": "completed",
            "analysis_snapshot_path": str(snapshot_path),
        }

    fake_store = _FakeDecisionOpsStore(
        queue_payload=[
            {
                "action_id": "act-ui-1",
                "review_state": "proposed",
                "scope_id": "customer:acme",
                "proposed_owner": "CSE",
                "specific_action": "Set renewal date reminder",
                "urgency": "7 days",
                "priority_score": 72,
                "outcomes": [],
                "events": [],
            },
            {
                "action_id": "act-ui-2",
                "review_state": "accepted_with_edit",
                "scope_id": "customer:beta",
                "proposed_owner": "CSM",
                "specific_action": "Confirm onboarding plan",
                "urgency": "today",
                "priority_score": 64,
                "outcomes": [{"outcome": "succeeded", "recorded_at": "2026-07-13T00:00:00Z", "observed_signal": "done"}],
                "events": [{"event_type": "review_accepted", "actor": "alice", "recorded_at": "2026-07-13T00:00:01Z", "payload": "{}"}],
            },
        ],
    )
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    response = client.get(f"/decisionops/{analysis_id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Decision Review Workbench" in html
    assert "act-ui-1" in html
    assert "act-ui-2" in html
    assert "Open detail" in html
    assert fake_store.snapshot_path == str(snapshot_path)


def test_decisionops_action_detail_page_shows_ledger(client, monkeypatch, tmp_path):
    analysis_id = "analysis-workbench-detail"
    action_id = "act-ui-detail"
    snapshot_path = tmp_path / "detail-snapshot.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[analysis_id] = {
            "status": "completed",
            "analysis_snapshot_path": str(snapshot_path),
        }

    fake_store = _FakeDecisionOpsStore(
        action_payload={
            "action_id": action_id,
            "review_state": "accepted",
            "scope_fingerprint": "scope:one",
            "scope_id": "customer:acme",
            "specific_action": "Follow up on renewal risk",
            "rationale": "Customer is at risk due to unresolved barrier",
            "expected_outcome": "Barriers reduced",
            "measurable_success_signal": "No new critical barriers in 14 days",
            "proposed_owner": "CSM",
            "owner_confidence": "MEDIUM",
            "urgency": "within 14 days",
            "priority_score": 81.0,
            "reviewed_at": "2026-07-13T01:00:00Z",
            "reviewed_by": "alice",
            "review_reason_code": "owner_corrected",
            "review_reason": "owner correction",
            "review_edited_value": {
                "specific_action": "Follow up with engineering first",
            },
            "review_notes": "Owner corrected",
            "events": [
                {
                    "event_type": "review_accepted",
                    "actor": "alice",
                    "recorded_at": "2026-07-13T01:02:00Z",
                    "payload": "{}",
                },
            ],
            "reviews": [
                {
                    "decision": "edit",
                    "reviewer": "alice",
                    "reason": "owner correction",
                    "reason_code": "owner_corrected",
                    "recorded_at": "2026-07-13T01:01:00Z",
                },
            ],
            "outcomes": [
                {
                    "outcome": "succeeded",
                    "observed_signal": "No critical barriers",
                    "observed_value": "[]",
                    "recorded_at": "2026-07-13T01:03:00Z",
                    "reporter": "alice",
                    "notes": "Confirmed on follow-up",
                },
            ],
            "review_edited_value": {"specific_action": "Follow up with engineering first"},
        },
    )
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    response = client.get(f"/decisionops/action/{analysis_id}/{action_id}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Decision Action Ledger" in html
    assert "Follow up on renewal risk" in html
    assert "review_accepted" in html
    assert "Human revision currently applied" in html


def test_decisionops_portfolio_route_shows_register_breakdown(client, monkeypatch, tmp_path):
    analysis_id = "analysis-workbench-portfolio"
    snapshot_path = tmp_path / "portfolio-snapshot.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[analysis_id] = {
            "status": "completed",
            "analysis_snapshot_path": str(snapshot_path),
        }

    fake_store = _FakeDecisionOpsStore(
        queue_payload=[
            {
                "action_id": "act-port-1",
                "review_state": "accepted",
                "scope_id": "customer:acme",
                "proposed_owner": "CSE",
                "specific_action": "Close cases",
                "measurable_success_signal": "No open cases for 14 days",
                "outcomes": [],
                "events": [],
            },
            {
                "action_id": "act-port-2",
                "review_state": "duplicate",
                "review_reason_code": "duplicate",
                "scope_id": "customer:acme",
                "proposed_owner": "CSE",
                "specific_action": "Run follow-up sync",
                "measurable_success_signal": "Follow-up completed",
                "outcomes": [],
                "events": [],
            },
        ],
    )
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    response = client.get(f"/decisionops/portfolio/{analysis_id}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Portfolio DecisionOps Brief" in html
    assert "act-port-1" in html
    assert "act-port-2" in html
    assert "duplication" in html.lower() or "duplicate" in html.lower()
def test_decisionops_export_requires_confirmation(client, monkeypatch, tmp_path):
    analysis_id = "analysis-export-1"
    snapshot_path = tmp_path / "export-snapshot.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[analysis_id] = {
            "status": "completed",
            "analysis_snapshot_path": str(snapshot_path),
        }

    fake_store = _FakeDecisionOpsStore(export_payload={"manifest": {"record_count": 1}})
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    resp = client.post('/api/decisionops/export', json={"analysis_id": analysis_id})
    payload = resp.get_json()
    assert resp.status_code == 400
    assert payload["ok"] is False
    assert payload["error"] == "Export is disabled by default; set confirm_export to I_UNDERSTAND"


def test_decisionops_export_payload_shape_and_flags(client, monkeypatch, tmp_path):
    analysis_id = "analysis-export-2"
    snapshot_path = tmp_path / "export-snapshot-2.json"
    snapshot_path.write_text("{}")

    with app_simple.analysis_status_lock:
        app_simple.analysis_status.clear()
        app_simple.analysis_status[analysis_id] = {
            "status": "completed",
            "analysis_snapshot_path": str(snapshot_path),
        }

    fake_store = _FakeDecisionOpsStore(
        export_payload={
            "manifest": {"record_count": 2},
            "records": [{"action_id": "action-1"}, {"action_id": "action-2"}],
        }
    )
    monkeypatch.setattr(app_simple, "_get_decision_ops_store", lambda: fake_store)

    response = client.post(
        '/api/decisionops/export',
        json={
            "analysis_id": analysis_id,
            "confirm_export": "I_UNDERSTAND",
            "include_raw_ids": True,
            "include_free_text": True,
            "export_salt": "salted",
        },
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["export"]["manifest"]["record_count"] == 2
    assert payload["export"]["records"][0]["action_id"] == "action-1"
    assert fake_store.export_args["include_raw_ids"] is True
    assert fake_store.export_args["include_free_text"] is True
    assert fake_store.export_args["export_salt"] == "salted"


def test_export_feedback_is_pseudonymized_by_default(tmp_path, monkeypatch):
    store = DecisionOpsStore(db_path=tmp_path / "decision_ops.db")
    scope = "scope:privacy"
    action = _mk_action("action:raw", "customer:Acme")
    bundle = _mk_bundle(
        scope_fingerprint=scope,
        analysis_fingerprint="analysis:privacy",
        as_of_time="2026-07-13T16:00:00Z",
    )
    bundle.customers = (SimpleNamespace(recommended_actions=(action,)),)
    monkeypatch.setattr(store, "load_bundle", lambda *_: bundle)
    snapshot = tmp_path / "privacy.json"
    snapshot.write_text("{}")

    store.sync_from_snapshot(snapshot)
    store.review(
        snapshot,
        "action:raw",
        "edit",
        "reviewer-ada",
        reason="owner correction",
        reason_code="owner_corrected",
        edited_value={"specific_action": "reworded"},
        analysis_fingerprint="analysis:privacy",
    )
    store.outcome(
        snapshot,
        "action:raw",
        "succeeded",
        "owner action completed",
    )

    payload = store.export_feedback(snapshot)
    manifest = payload["manifest"]
    record = payload["records"][0]

    assert manifest["include_raw_ids"] is False
    assert manifest["include_free_text"] is False
    assert manifest["record_count"] == 1
    assert record["action_id"].startswith("action:")
    assert "action:raw" not in record["action_id"]
    assert "customer:Acme" not in record["scope_id"]
    assert record["expected_outcome"] == ""
    assert record["measurable_success_signal"] == ""
    assert record["review_reason"] == ""
    assert record["reviews"][0]["reason"] == ""
    assert record["reviews"][0]["notes"] == ""
    assert record["outcomes"][0]["notes"] == ""
    assert record["reviews"][0]["reviewer"] != "reviewer-ada"
    assert record["outcomes"][0]["reporter"] != "reviewer-ada"
    assert record["events"][0]["actor"] != "reviewer-ada"
