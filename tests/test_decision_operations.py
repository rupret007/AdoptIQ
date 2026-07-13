from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from decision_operations import DecisionOpsStore


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

    assert metadata["action_count"] == 2
    assert metadata["analysis_fingerprint"] == "analysis:one"
    assert metadata["snapshot_path"] == str(snapshot)
    assert {row["action_id"] for row in queue} == {"action:shared", "action:onlyonce"}
    shared = next(row for row in queue if row["action_id"] == "action:shared")
    assert shared["scope_id"] == "customer:acme"
    assert shared["analysis_snapshot_path"] == str(snapshot)
    assert shared["analysis_fingerprint"] == "analysis:one"

    with sqlite3.connect(store.db_path) as connection:
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

    assert stored_scope == {"customer:acme", "customer:beta"}
    assert stored_snapshots == {str(snapshot)}


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
    assert detail["review_state"] == "accept"
    assert detail["reviews"][0]["decision"] == "accept"
    assert detail["reviews"][0]["reviewer"] == "alice"
    assert outcome["outcome"] == "not_succeeded"
    assert detail["outcomes"][0]["outcome"] == "not_succeeded"
    assert detail["recent_events"][0]["event_type"] in {"outcome_recorded", "review_decision"}


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
        "last_synced_at",
    }:
        assert expected in columns
