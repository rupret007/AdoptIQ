"""Round 171: signed, persisted, fixture-only validation receipts."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import enhanced_admin_dashboard_v2 as admin
import manager_decision_workspace as workspace
from offline_validation_receipt import (
    OfflineValidationReceiptError,
    OfflineValidationReceiptStatus,
    ReceiptExpectation,
    VerifiedOfflineValidationReceipt,
    analysis_id_sha256,
    canonical_receipt_json,
    gate_spec_sha256,
    issue_offline_validation_receipt,
    parse_offline_validation_receipt_json,
    scope_identity_sha256,
    trusted_key_id,
    verify_offline_validation_receipt,
)
from scripts.generate_offline_acceptance_artifacts import (
    DEFAULT_FIXTURE_PATH,
    generate_acceptance_artifacts,
    persist_generated_offline_validation_receipt,
    prepare_generated_offline_validation_receipt,
)


NOW = datetime(2026, 8, 27, 12, 30, tzinfo=timezone.utc)
ISSUED = "2026-08-27T12:00:00Z"
EXPIRES = "2026-08-27T14:00:00Z"
DATA_AS_OF = "2026-08-27T11:00:00.123456Z"
SOURCE_COMMIT = "f5417e500201718f1013c848bd3edcac93af1ea9"
ANALYSIS_ID = "offline-fixture-round171"
FACT_FINGERPRINT = "a" * 64
WORD_SHA256 = "b" * 64
EXCEL_SHA256 = "c" * 64
WEB_PROJECTION_SHA256 = "d" * 64
FIXTURE_MANIFEST_SHA256 = hashlib.sha256(DEFAULT_FIXTURE_PATH.read_bytes()).hexdigest()
VALIDATOR_BUILD_SHA256 = "9" * 64
SCOPE_VALUE = "fixture-scope"
SCOPE_IDENTITY_SHA256 = scope_identity_sha256(
    report_type="leader",
    scope_type="customer",
    scope_value=SCOPE_VALUE,
    manager="Fixture Manager",
    technology="All",
)


@pytest.fixture
def signing_identity():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, {trusted_key_id(public_key): public_key}


def _issue(
    private_key: Ed25519PrivateKey,
    *,
    report_history_id: int = 7,
    analysis_id: str = ANALYSIS_ID,
    issued_at_utc: str = ISSUED,
    expires_at_utc: str = EXPIRES,
    web_projection_sha256: str = WEB_PROJECTION_SHA256,
    validator_build_sha256: str = VALIDATOR_BUILD_SHA256,
) -> dict:
    return issue_offline_validation_receipt(
        private_key=private_key,
        report_history_id=report_history_id,
        analysis_id=analysis_id,
        report_type="leader",
        scope_type="customer",
        fact_fingerprint=FACT_FINGERPRINT,
        word_sha256=WORD_SHA256,
        excel_sha256=EXCEL_SHA256,
        web_projection_sha256=web_projection_sha256,
        data_as_of_utc=DATA_AS_OF,
        scope_identity_sha256=SCOPE_IDENTITY_SHA256,
        source_commit_sha=SOURCE_COMMIT,
        validator_build_sha256=validator_build_sha256,
        fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
        issued_at_utc=issued_at_utc,
        expires_at_utc=expires_at_utc,
    )


def _expectation(
    *,
    report_history_id: int = 7,
    analysis_id: str = ANALYSIS_ID,
    web_projection_sha256: str = WEB_PROJECTION_SHA256,
) -> ReceiptExpectation:
    return ReceiptExpectation(
        report_history_id=report_history_id,
        analysis_id=analysis_id,
        report_type="leader",
        scope_type="customer",
        fact_fingerprint=FACT_FINGERPRINT,
        word_sha256=WORD_SHA256,
        excel_sha256=EXCEL_SHA256,
        data_as_of_utc=DATA_AS_OF,
        scope_identity_sha256=SCOPE_IDENTITY_SHA256,
        web_projection_sha256=web_projection_sha256,
        source_commit_sha=SOURCE_COMMIT,
        validator_build_sha256=VALIDATOR_BUILD_SHA256,
        fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
        gate_spec_sha256=gate_spec_sha256(),
    )


def _verified(signing_identity) -> VerifiedOfflineValidationReceipt:
    private_key, trusted_keys = signing_identity
    return verify_offline_validation_receipt(
        _issue(private_key),
        trusted_public_keys=trusted_keys,
        expectation=_expectation(),
        now_utc=NOW,
    )


def _runtime_bindings(trusted_keys, *, web_projection_sha256=WEB_PROJECTION_SHA256):
    return {
        "trusted_public_keys": trusted_keys,
        "expected_web_projection_sha256": web_projection_sha256,
        "expected_source_commit_sha": SOURCE_COMMIT,
        "expected_validator_build_sha256": VALIDATOR_BUILD_SHA256,
        "expected_fixture_manifest_sha256": FIXTURE_MANIFEST_SHA256,
        "current_word_sha256": WORD_SHA256,
        "current_excel_sha256": EXCEL_SHA256,
    }


def _preview_ready_snapshot() -> dict:
    return {
        "status": "completed",
        "report_type": "leader",
        "scope_type": "customer",
        "canonical_snapshot": True,
        "fact_fingerprint": FACT_FINGERPRINT,
        "workbook_sha256": EXCEL_SHA256,
        "persisted_workbook_hash_verified": True,
        "evidence_integrity_verified": True,
        "evidence_available": True,
        "formula_cells": 0,
        "data_as_of_state": "available",
        "source_states": {"TAC_Cases": "available"},
        "source_warnings": [],
        "decision_insight_integrity": "verified_shape",
        "decision_insights": [{
            "insight_key": "insight.support_themes",
            "evidence_key": "insight.support_themes",
            "evidence_count": 2,
            "claim": "Two exact fixture support records were grouped.",
            "source_state": "available",
            "source_sheets": ["TAC_Cases"],
        }],
    }


def test_signed_receipt_is_exact_redacted_fixture_contract(signing_identity):
    private_key, trusted_keys = signing_identity
    envelope = _issue(private_key)

    verified = verify_offline_validation_receipt(
        envelope,
        trusted_public_keys=trusted_keys,
        expectation=_expectation(),
        now_utc=NOW,
    )

    assert verified.analysis_id_sha256 == analysis_id_sha256(ANALYSIS_ID)
    assert verified.web_projection_sha256 == WEB_PROJECTION_SHA256
    assert verified.gate_spec_sha256 == gate_spec_sha256()
    assert verified.canonical_json == canonical_receipt_json(envelope)
    assert ANALYSIS_ID not in verified.canonical_json
    assert "customer_name" not in verified.canonical_json
    assert "path" not in verified.canonical_json.casefold()
    assert "error" not in verified.canonical_json.casefold()
    summary = verified.public_summary()
    assert summary["state"] == "verified_offline_fixture"
    assert summary["fixture_validation_performed"] is True
    assert summary["fixture_validation_passed"] is True
    assert summary["live_validation_performed"] is False
    assert summary["production_accuracy_claimed"] is False
    assert summary["release_ready"] is False
    assert summary["customer_shareable"] is False
    assert summary["ready_for_live_cisco"] is False


def test_validator_build_is_digest_only_and_cannot_persist_free_text(signing_identity):
    private_key, _trusted_keys = signing_identity
    sensitive_value = "SensitiveCustomerBuildName"

    with pytest.raises(OfflineValidationReceiptError):
        _issue(private_key, validator_build_sha256=sensitive_value)

    canonical = canonical_receipt_json(_issue(private_key))
    assert sensitive_value not in canonical
    assert "validator_build\"" not in canonical
    assert f'"validator_build_sha256":"{VALIDATOR_BUILD_SHA256}"' in canonical


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt["payload"]["honesty"].__setitem__(
            "customer_shareable", True
        ),
        lambda receipt: receipt["payload"]["honesty"].__setitem__(
            "release_ready", "false"
        ),
        lambda receipt: receipt["payload"]["gates"].__setitem__(
            "evidence_integrity_verified", 1
        ),
        lambda receipt: receipt["payload"].__setitem__("unknown", False),
        lambda receipt: receipt["payload"].pop("gate_spec_sha256"),
        lambda receipt: receipt["payload"].__setitem__(
            "web_projection_sha256", "0" * 64
        ),
    ],
)
def test_truthy_stringish_unknown_missing_and_tampered_payloads_fail_closed(
    signing_identity,
    mutation,
):
    private_key, trusted_keys = signing_identity
    envelope = copy.deepcopy(_issue(private_key))
    mutation(envelope)

    with pytest.raises(OfflineValidationReceiptError):
        verify_offline_validation_receipt(
            envelope,
            trusted_public_keys=trusted_keys,
            expectation=_expectation(),
            now_utc=NOW,
        )


def test_signature_rejects_equivalent_noncanonical_base64_pad_bits(signing_identity):
    private_key, trusted_keys = signing_identity
    envelope = _issue(private_key)
    original = envelope["signature"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    last_value = alphabet.index(original[-3])
    replacement_value = (last_value & 0b110000) | ((last_value + 1) & 0b001111)
    if replacement_value == last_value:
        replacement_value = (last_value & 0b110000) | ((last_value + 2) & 0b001111)
    malleable = original[:-3] + alphabet[replacement_value] + "=="
    assert malleable != original
    assert base64.b64decode(malleable) == base64.b64decode(original)
    envelope["signature"] = malleable

    with pytest.raises(OfflineValidationReceiptError, match="canonical base64"):
        verify_offline_validation_receipt(
            envelope,
            trusted_public_keys=trusted_keys,
            expectation=_expectation(),
            now_utc=NOW,
        )


def test_wrong_signer_replay_expiry_and_future_receipts_fail_closed(signing_identity):
    private_key, trusted_keys = signing_identity
    envelope = _issue(private_key)
    other_key = Ed25519PrivateKey.generate().public_key()
    wrong_keys = {trusted_key_id(other_key): other_key}

    with pytest.raises(OfflineValidationReceiptError) as wrong_signer:
        verify_offline_validation_receipt(
            envelope,
            trusted_public_keys=wrong_keys,
            expectation=_expectation(),
            now_utc=NOW,
        )
    assert wrong_signer.value.code == "unconfigured"

    with pytest.raises(OfflineValidationReceiptError) as replay:
        verify_offline_validation_receipt(
            envelope,
            trusted_public_keys=trusted_keys,
            expectation=_expectation(analysis_id="another-run"),
            now_utc=NOW,
        )
    assert replay.value.code == "artifact_mismatch"

    with pytest.raises(OfflineValidationReceiptError) as expired:
        verify_offline_validation_receipt(
            envelope,
            trusted_public_keys=trusted_keys,
            expectation=_expectation(),
            now_utc=datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc),
        )
    assert expired.value.code == "stale"

    future = _issue(
        private_key,
        issued_at_utc="2026-08-27T13:00:00Z",
        expires_at_utc="2026-08-27T14:00:00Z",
    )
    with pytest.raises(OfflineValidationReceiptError) as future_error:
        verify_offline_validation_receipt(
            future,
            trusted_public_keys=trusted_keys,
            expectation=_expectation(),
            now_utc=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        )
    assert future_error.value.code == "stale"


def test_parser_rejects_duplicate_fields_nonfinite_and_oversized_json():
    with pytest.raises(OfflineValidationReceiptError):
        parse_offline_validation_receipt_json(
            '{"schema_version":"one","schema_version":"two"}'
        )
    with pytest.raises(OfflineValidationReceiptError):
        parse_offline_validation_receipt_json('{"value":NaN}')
    with pytest.raises(OfflineValidationReceiptError):
        parse_offline_validation_receipt_json("{" + ('"x":' + '"y"' * 20_000) + "}")


@pytest.fixture
def receipt_database(monkeypatch, tmp_path: Path):
    database = tmp_path / "offline-receipts.db"
    monkeypatch.setattr(admin, "DB_PATH", str(database))
    monkeypatch.setattr(admin, "_db_initialized_for_path", None)
    admin.init_database()
    with admin.db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            '''
            INSERT INTO report_history
            (request_id, report_type, manager, technology, customer_name,
             status, start_time, end_time, word_hash, excel_hash, scope_type,
             scope_value, data_as_of_utc, fact_fingerprint, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                ANALYSIS_ID,
                "leader",
                "Fixture Manager",
                "All",
                "Fixture Customer",
                "completed",
                ISSUED,
                ISSUED,
                WORD_SHA256,
                EXCEL_SHA256,
                "customer",
                SCOPE_VALUE,
                DATA_AS_OF.replace("Z", "+00:00"),
                FACT_FINGERPRINT,
                ISSUED,
            ),
        )
        report_history_id = int(cursor.lastrowid)
    return database, report_history_id


def test_signed_receipt_persists_reloads_after_restart_and_stays_private(
    signing_identity,
    receipt_database,
):
    private_key, trusted_keys = signing_identity
    database, report_history_id = receipt_database
    envelope = _issue(private_key, report_history_id=report_history_id)

    stored = admin.persist_offline_validation_receipt(
        ANALYSIS_ID,
        envelope,
        **_runtime_bindings(trusted_keys),
        now_utc=NOW,
    )
    assert stored.verified is True

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT request_id_sha256, receipt_json FROM offline_validation_receipts"
        ).fetchone()
    assert row is not None
    assert row[0] == hashlib.sha256(ANALYSIS_ID.encode()).hexdigest()
    assert row[1] == canonical_receipt_json(envelope)
    assert ANALYSIS_ID not in row[1]
    assert "Fixture Customer" not in row[1]
    assert "Fixture Manager" not in row[1]
    assert "fixture-scope" not in row[1]

    # Simulate a new application process: schema initialization and receipt
    # loading use fresh SQLite connections, not in-memory/status JSON state.
    admin._db_initialized_for_path = None
    admin.init_database()
    loaded = admin.load_offline_validation_receipt(
        ANALYSIS_ID,
        **_runtime_bindings(trusted_keys),
        now_utc=NOW,
    )
    assert loaded.verified is True

    projected = workspace.apply_offline_validation_receipt_status(
        {
            **_preview_ready_snapshot(),
            "customer_share_validation_receipt": {"customer_shareable": True},
        },
        loaded,
    )
    assert projected["offline_validation_receipt"]["state"] == "verified_offline_fixture"
    assert projected["offline_validation_receipt"]["fixture_validation_passed"] is True
    assert "customer_share_validation_receipt" not in projected
    readiness = projected["customer_share_readiness"]
    assert readiness["fixture_validation_performed"] is True
    assert readiness["live_validation_performed"] is False
    assert readiness["production_accuracy_claimed"] is False
    assert readiness["release_ready"] is False
    assert readiness["customer_shareable"] is False
    public_json = json.dumps(projected)
    assert "signature" not in public_json
    assert envelope["receipt_id"] not in public_json


def test_store_is_idempotent_conflict_safe_and_requires_trusted_key(
    signing_identity,
    receipt_database,
):
    private_key, trusted_keys = signing_identity
    database, report_history_id = receipt_database
    first = _issue(private_key, report_history_id=report_history_id)

    untrusted = admin.persist_offline_validation_receipt(
        ANALYSIS_ID,
        first,
        **_runtime_bindings({}),
        now_utc=NOW,
    )
    assert untrusted.state == "unconfigured"

    stored = admin.persist_offline_validation_receipt(
        ANALYSIS_ID,
        first,
        **_runtime_bindings(trusted_keys),
        now_utc=NOW,
    )
    repeated = admin.persist_offline_validation_receipt(
        ANALYSIS_ID,
        first,
        **_runtime_bindings(trusted_keys),
        now_utc=NOW,
    )
    second = _issue(
        private_key,
        report_history_id=report_history_id,
        issued_at_utc="2026-08-27T12:01:00Z",
        expires_at_utc="2026-08-27T14:01:00Z",
    )
    conflict = admin.persist_offline_validation_receipt(
        ANALYSIS_ID,
        second,
        **_runtime_bindings(trusted_keys),
        now_utc=NOW,
    )
    assert stored.verified is True
    assert repeated.verified is True
    assert conflict.state == "conflict"

    without_key = admin.load_offline_validation_receipt(
        ANALYSIS_ID,
        **_runtime_bindings({}),
        now_utc=NOW,
    )
    mismatch = admin.load_offline_validation_receipt(
        ANALYSIS_ID,
        **_runtime_bindings(trusted_keys, web_projection_sha256="f" * 64),
        now_utc=NOW,
    )
    assert without_key.state == "unconfigured"
    assert mismatch.state == "artifact_mismatch"

    missing_runtime_binding = _runtime_bindings(trusted_keys)
    missing_runtime_binding["expected_source_commit_sha"] = ""
    assert admin.load_offline_validation_receipt(
        ANALYSIS_ID,
        **missing_runtime_binding,
        now_utc=NOW,
    ).state == "unconfigured"

    for changed_key, changed_value in (
        ("expected_source_commit_sha", "0" * 40),
        ("expected_validator_build_sha256", "1" * 64),
        ("expected_fixture_manifest_sha256", "2" * 64),
        ("current_word_sha256", "3" * 64),
        ("current_excel_sha256", "4" * 64),
    ):
        drifted = _runtime_bindings(trusted_keys)
        drifted[changed_key] = changed_value
        assert admin.load_offline_validation_receipt(
            ANALYSIS_ID,
            **drifted,
            now_utc=NOW,
        ).state == "artifact_mismatch"

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE offline_validation_receipts SET receipt_id = ?",
                ("0" * 64,),
            )


def test_projection_digest_binds_selectors_and_every_bounded_display_section():
    snapshot = {
        "schema": "manager-decision-workspace/v1",
        "report_type": "leader",
        "scope_type": "customer",
        "scope_value": "Private Customer Name",
        "scope_label": "Private Customer Name",
        "manager": "Fixture Manager",
        "technology": "All",
        "days": 90,
        "fact_fingerprint": FACT_FINGERPRINT,
        "workbook_sha256": EXCEL_SHA256,
        "data_as_of_utc": DATA_AS_OF,
        "data_as_of_state": "available",
        "retrieval_attempted_at_utc": "2026-08-27T10:59:00Z",
        "evaluation_as_of_utc": "2026-08-27T11:30:00Z",
        "started_at": "2026-08-27T11:31:00Z",
        "completed_at": "2026-08-27T11:32:00Z",
        "canonical_snapshot": True,
        "persisted_workbook_hash_verified": True,
        "evidence_integrity_verified": True,
        "decision_insight_integrity": "verified_shape",
        "formula_cells": 0,
        "source_states": {"TAC_Cases": "available"},
        "source_warnings": [],
        "decision_metrics": [{
            "metric_key": "kpi.tac_cases",
            "label": "TAC cases",
            "value": 2,
            "display_value": "2",
            "unit": "records",
            "source_state": "available",
            "source_sheet": "TAC_Cases",
            "provenance": "canonical",
            "evidence_key": "kpi.tac_cases",
            "evidence_count": 2,
        }],
        "decision_insights": [{
            "insight_key": "insight.support_themes",
            "label": "Support themes",
            "claim": "Two exact fixture support records were grouped.",
            "caveat": "Fixture only.",
            "source_state": "available",
            "source_sheets": ["TAC_Cases"],
            "evidence_key": "insight.support_themes",
            "evidence_count": 2,
            "provenance": "canonical Metric_Lineage",
        }],
        "top_action_plans": [{"record_id": "AP-1", "title": "Fixture plan"}],
        "top_accounts": [{"customer": "Private Customer Name", "risk_band": "HIGH"}],
        "charts": [{"chart_id": "risk", "points": [{"label": "High", "value": 1}]}],
        "evidence_manifest": [
            {"evidence_key": f"fixture.evidence.{index}", "total_records": index}
            for index in range(300)
        ],
        "ask_ai_binding": {
            "analysis_id": ANALYSIS_ID,
            "scope_type": "customer",
            "scope_value": "Private Customer Name",
        },
        "ask_ai_url": f"/ask-ai?report_analysis_id={ANALYSIS_ID}",
    }
    digest = workspace.offline_validation_projection_sha256(snapshot)
    renamed_scope = {**snapshot, "scope_value": "Another Private Name"}
    changed_claim = copy.deepcopy(snapshot)
    changed_claim["decision_insights"][0]["claim"] = "Different claim."

    assert workspace.offline_validation_projection_sha256(renamed_scope) != digest
    assert workspace.offline_validation_projection_sha256(changed_claim) != digest
    for section, replacement in (
        ("top_action_plans", [{"record_id": "AP-1", "title": "Changed"}]),
        ("top_accounts", [{"customer": "Private Customer Name", "risk_band": "LOW"}]),
        ("charts", [{"chart_id": "risk", "points": [{"label": "High", "value": 2}]}]),
    ):
        changed = copy.deepcopy(snapshot)
        changed[section] = replacement
        assert workspace.offline_validation_projection_sha256(changed) != digest

    changed_metric_label = copy.deepcopy(snapshot)
    changed_metric_label["decision_metrics"][0]["label"] = "Support cases"
    assert workspace.offline_validation_projection_sha256(changed_metric_label) != digest
    changed_scope_label = {**snapshot, "scope_label": "Changed visible customer label"}
    assert workspace.offline_validation_projection_sha256(changed_scope_label) != digest
    changed_late_evidence = copy.deepcopy(snapshot)
    changed_late_evidence["evidence_manifest"][259]["total_records"] = 999
    assert workspace.offline_validation_projection_sha256(changed_late_evidence) != digest
    changed_retrieval_time = {
        **snapshot,
        "retrieval_attempted_at_utc": "2026-08-27T10:58:00Z",
    }
    assert workspace.offline_validation_projection_sha256(changed_retrieval_time) != digest
    changed_ask_binding = copy.deepcopy(snapshot)
    changed_ask_binding["ask_ai_binding"]["scope_value"] = "Another Private Name"
    assert workspace.offline_validation_projection_sha256(changed_ask_binding) != digest


def test_missing_or_forged_status_never_enables_any_live_or_share_flag():
    result = workspace.apply_offline_validation_receipt_status(
        {
            "live_validation_performed": True,
            "production_accuracy_claimed": True,
            "release_ready": True,
            "customer_shareable": True,
            "ready_for_live_cisco": True,
            "customer_share_validation_receipt": {
                "fixture_validation_performed": True,
                "live_validation_performed": True,
                "production_accuracy_claimed": True,
                "release_ready": True,
                "customer_shareable": True,
            }
        },
        OfflineValidationReceiptStatus(state="invalid"),
    )

    assert result["offline_validation_receipt"]["state"] == "invalid"
    assert result["offline_validation_receipt"]["fixture_validation_performed"] is False
    assert result["customer_share_readiness"]["live_validation_performed"] is False
    assert result["customer_share_readiness"]["production_accuracy_claimed"] is False
    assert result["customer_share_readiness"]["release_ready"] is False
    assert result["customer_share_readiness"]["customer_shareable"] is False
    for prohibited_flag in (
        "live_validation_performed",
        "production_accuracy_claimed",
        "release_ready",
        "customer_shareable",
        "ready_for_live_cisco",
    ):
        assert result[prohibited_flag] is False
    assert "customer_share_validation_receipt" not in result

    class ForgedReceipt:
        def public_summary(self):
            return {
                "state": "verified_offline_fixture",
                "fixture_validation_performed": True,
                "fixture_validation_passed": True,
                "live_validation_performed": True,
                "production_accuracy_claimed": True,
                "release_ready": True,
                "customer_shareable": True,
                "ready_for_live_cisco": True,
            }

    forged_status = OfflineValidationReceiptStatus(
        state="verified_offline_fixture",
        receipt=ForgedReceipt(),  # type: ignore[arg-type]
    )
    assert forged_status.verified is False
    assert forged_status.public_summary()["customer_shareable"] is False
    forged_result = workspace.apply_offline_validation_receipt_status(
        _preview_ready_snapshot(),
        forged_status,
    )
    assert forged_result["offline_validation_receipt"]["state"] == "invalid"
    assert forged_result["customer_share_readiness"]["release_ready"] is False


def test_valid_signature_is_downgraded_when_current_preview_gates_are_incomplete(
    signing_identity,
):
    verified = _verified(signing_identity)
    receipt_status = OfflineValidationReceiptStatus(
        state="verified_offline_fixture",
        receipt=verified,
    )
    incomplete = _preview_ready_snapshot()
    incomplete["source_warnings"] = ["Fixture source became partial."]

    result = workspace.apply_offline_validation_receipt_status(
        incomplete,
        receipt_status,
    )

    assert result["offline_validation_receipt"]["state"] == "artifact_mismatch"
    assert result["offline_validation_receipt"]["fixture_validation_performed"] is False
    assert result["customer_share_readiness"]["customer_shareable"] is False
    assert result["customer_share_readiness"]["release_ready"] is False


def test_exact_fixture_provenance_disclosure_does_not_invalidate_fixture_receipt(
    signing_identity,
):
    receipt_status = OfflineValidationReceiptStatus(
        state="verified_offline_fixture",
        receipt=_verified(signing_identity),
    )
    disclosed = _preview_ready_snapshot()
    disclosed["source_warnings"] = [
        "This report uses controlled local test data; no live source validation was performed."
    ]

    result = workspace.apply_offline_validation_receipt_status(
        disclosed,
        receipt_status,
    )

    assert result["offline_validation_receipt"]["state"] == "verified_offline_fixture"
    assert result["offline_validation_receipt"]["fixture_validation_passed"] is True
    assert result["customer_share_readiness"]["customer_shareable"] is False
    assert result["customer_share_readiness"]["live_validation_performed"] is False


def test_real_canonical_fixture_route_reloads_persisted_receipt(
    client,
    monkeypatch,
    tmp_path: Path,
    signing_identity,
):
    import app_simple

    private_key, trusted_keys = signing_identity
    analysis_id = "round171-real-canonical-fixture"
    generated = generate_acceptance_artifacts(
        scope="customer",
        as_of="2026-08-03T12:00:00Z",
        output_dir=tmp_path / "official-offline-harness",
    )
    scope_value = generated["scope_value"]
    word_path = Path(generated["word_path"])
    excel_path = Path(generated["source_data_path"])
    assert generated["fixture_manifest_sha256"] == FIXTURE_MANIFEST_SHA256
    word_hash = hashlib.sha256(word_path.read_bytes()).hexdigest()
    excel_hash = hashlib.sha256(excel_path.read_bytes()).hexdigest()
    workbook = workspace.load_workbook_snapshot(excel_path)
    evidence = workspace.verify_canonical_evidence_workbook(
        excel_path,
        expected_fingerprint=workbook["fact_fingerprint"],
    )
    assert evidence["ok"] is True
    assert workbook["source_warnings"] == [
        "This report uses controlled local test data; no live source validation was performed."
    ]
    assert set(workbook["source_states"].values()) == {"partial"}
    assert workbook["decision_insights"] == []
    manager = workbook["manager"]
    technology = workbook["technology"]
    days = int(workbook["days"])
    status = {
        "analysis_id": analysis_id,
        "status": "completed",
        "report_type": "leader",
        "manager": manager,
        "technology": technology,
        "days": days,
        "scope_type": "customer",
        "scope_value": scope_value,
        "data_as_of_utc": workbook["data_as_of_utc"],
        "fact_fingerprint": workbook["fact_fingerprint"],
        "word_report": str(word_path),
        "excel_report": str(excel_path),
        "word_hash": word_hash,
        "excel_hash": excel_hash,
    }
    database = tmp_path / "route-offline-receipts.db"
    monkeypatch.setattr(admin, "DB_PATH", str(database))
    monkeypatch.setattr(admin, "_db_initialized_for_path", None)
    monkeypatch.setenv(
        "ADOPTIQ_AUDIT_MIRROR_PATH",
        str(tmp_path / "fixture-report-history.audit.jsonl"),
    )
    drifted_generated = dict(generated)
    drifted_generated["scope_value"] = "different-fixture-scope"
    with pytest.raises(ValueError, match="canonical workbook"):
        prepare_generated_offline_validation_receipt(
            drifted_generated,
            analysis_id=analysis_id,
            expected_source_commit_sha=SOURCE_COMMIT,
            expected_validator_build_sha256=VALIDATOR_BUILD_SHA256,
            expected_fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
        )
    assert not database.exists()

    drifted_fixture = dict(generated)
    drifted_fixture["fixture_manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="trusted manifest digest"):
        prepare_generated_offline_validation_receipt(
            drifted_fixture,
            analysis_id=analysis_id,
            expected_source_commit_sha=SOURCE_COMMIT,
            expected_validator_build_sha256=VALIDATOR_BUILD_SHA256,
            expected_fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
        )
    assert not database.exists()

    with pytest.raises(ValueError, match="trust bindings"):
        prepare_generated_offline_validation_receipt(
            generated,
            analysis_id=analysis_id,
            expected_source_commit_sha="malformed",
            expected_validator_build_sha256=VALIDATOR_BUILD_SHA256,
            expected_fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
        )
    assert not database.exists()

    preparation = prepare_generated_offline_validation_receipt(
        generated,
        analysis_id=analysis_id,
        expected_source_commit_sha=SOURCE_COMMIT,
        expected_validator_build_sha256=VALIDATOR_BUILD_SHA256,
        expected_fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
    )
    preparation_json = json.dumps(preparation)
    assert scope_value not in preparation_json
    assert str(tmp_path) not in preparation_json
    assert generated["fixture_path"] not in preparation_json

    with admin.db_connection() as connection:
        connection.execute(
            "UPDATE report_history SET manager = ? WHERE request_id = ?",
            ("Drifted Fixture Manager", analysis_id),
        )
    with pytest.raises(ValueError, match="report history"):
        prepare_generated_offline_validation_receipt(
            generated,
            analysis_id=analysis_id,
            expected_source_commit_sha=SOURCE_COMMIT,
            expected_validator_build_sha256=VALIDATOR_BUILD_SHA256,
            expected_fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
        )
    with admin.db_connection() as connection:
        connection.execute(
            "UPDATE report_history SET manager = ? WHERE request_id = ?",
            (manager, analysis_id),
        )

    monkeypatch.setattr(app_simple, "_r146_status_for_workspace", lambda _aid: status)
    monkeypatch.setattr(
        app_simple,
        "_r92_resolve_output_artifact",
        lambda raw, **_kwargs: str(raw) if raw else None,
    )
    monkeypatch.setitem(
        app_simple.app.config,
        "OFFLINE_VALIDATION_RECEIPT_TRUSTED_PUBLIC_KEYS",
        trusted_keys,
    )
    monkeypatch.setitem(
        app_simple.app.config,
        "OFFLINE_VALIDATION_RECEIPT_SOURCE_COMMIT_SHA",
        SOURCE_COMMIT,
    )
    monkeypatch.setitem(
        app_simple.app.config,
        "OFFLINE_VALIDATION_RECEIPT_VALIDATOR_BUILD_SHA256",
        VALIDATOR_BUILD_SHA256,
    )
    monkeypatch.setitem(
        app_simple.app.config,
        "OFFLINE_VALIDATION_RECEIPT_FIXTURE_MANIFEST_SHA256",
        FIXTURE_MANIFEST_SHA256,
    )
    actual_receipt_loader = app_simple.load_offline_validation_receipt
    receipt_call = {}

    def capture_projection(_analysis_id, **kwargs):
        receipt_call["projection"] = kwargs["expected_web_projection_sha256"]
        return OfflineValidationReceiptStatus(state="missing")

    monkeypatch.setattr(
        app_simple,
        "load_offline_validation_receipt",
        capture_projection,
    )
    preview_response = client.get(f"/api/decision-workspace/report/{analysis_id}")
    assert preview_response.status_code == 200
    projection_hash = receipt_call["projection"]
    assert projection_hash == preparation["receipt_fields"]["web_projection_sha256"]
    monkeypatch.setattr(
        app_simple,
        "load_offline_validation_receipt",
        actual_receipt_loader,
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    issued_at = (now.replace(microsecond=0)).isoformat().replace("+00:00", "Z")
    expires_at = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    envelope = issue_offline_validation_receipt(
        private_key=private_key,
        **preparation["receipt_fields"],
        issued_at_utc=issued_at,
        expires_at_utc=expires_at,
    )
    assert persist_generated_offline_validation_receipt(
        generated,
        envelope,
        analysis_id=analysis_id,
        trusted_public_keys=trusted_keys,
        expected_source_commit_sha=SOURCE_COMMIT,
        expected_validator_build_sha256=VALIDATOR_BUILD_SHA256,
        expected_fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
        now_utc=now,
    ).verified is True

    response = client.get(f"/api/decision-workspace/report/{analysis_id}")

    assert response.status_code == 200
    report = response.get_json()["report"]
    assert report["offline_validation_receipt"]["state"] == "verified_offline_fixture"
    assert report["offline_validation_receipt"]["fixture_validation_passed"] is True
    assert report["customer_share_readiness"]["live_validation_performed"] is False
    assert report["customer_share_readiness"]["production_accuracy_claimed"] is False
    assert report["customer_share_readiness"]["release_ready"] is False
    assert report["customer_share_readiness"]["customer_shareable"] is False
    assert envelope["receipt_id"] not in json.dumps(report)

    word_path.write_bytes(word_path.read_bytes() + b"tampered-after-signing")
    with pytest.raises(ValueError, match="report history"):
        persist_generated_offline_validation_receipt(
            generated,
            envelope,
            analysis_id=analysis_id,
            trusted_public_keys=trusted_keys,
            expected_source_commit_sha=SOURCE_COMMIT,
            expected_validator_build_sha256=VALIDATOR_BUILD_SHA256,
            expected_fixture_manifest_sha256=FIXTURE_MANIFEST_SHA256,
            now_utc=now,
        )


def test_verified_dataclass_is_not_required_for_missing_status(signing_identity):
    # Regression pin: callers cannot create a positive fixture state with a
    # truthy mapping; only the typed verifier result carried by Status counts.
    verified = _verified(signing_identity)
    assert isinstance(verified, VerifiedOfflineValidationReceipt)
    raw_mapping = {"state": "verified_offline_fixture", "receipt": verified}
    result = workspace.apply_offline_validation_receipt_status({}, raw_mapping)  # type: ignore[arg-type]
    assert result["offline_validation_receipt"]["state"] == "missing"
    assert result["customer_share_readiness"]["customer_shareable"] is False
