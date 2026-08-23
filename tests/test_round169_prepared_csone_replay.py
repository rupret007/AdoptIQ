"""Round 169 immutable CSOne replay preparation and transport contracts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import warnings
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import openpyxl
import pandas as pd
import pytest

import canonical_metrics as canonical
import decision_report_delivery as delivery
from csone_corpus_replay import (
    CorpusInputLimits,
    PreparedCsoneReplay,
    _timestamp_summary,
    apply_prepared_csone_replay,
    prepare_csone_replay,
    prepared_replay_from_bytes,
    prepared_replay_summary,
    pseudonymize_csone_frame,
    validate_representative_loaders,
)
from local_acceptance_lab import DEFAULT_MANIFEST_PATH, build_scenario_bundle
from local_acceptance_runtime import (
    UnexpectedLiveDependency,
    install_runtime_adapters,
    installation_summary,
)
from scripts import run_round146_acceptance as acceptance


MANIFEST_SHA256 = hashlib.sha256(DEFAULT_MANIFEST_PATH.read_bytes()).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _source_frame(rows: int = 18, *, invalid_date: bool = False) -> pd.DataFrame:
    opened = [f"7/{index % 9 + 1}/2026 8:15 AM" for index in range(rows)]
    if invalid_date:
        opened[-1] = "not-a-supported-date"
    return pd.DataFrame(
        [
            {
                "Customer Name: Customer Name": f"Sensitive Customer {index % 3}",
                "Subscription Reference Id": f"REAL-SUB-{index % 3}",
                "Product: Product Name": "Webex Calling",
                "Tech.": "Calling",
                "Sub Technology": "Calling",
                "Severity": str(index % 4 + 1),
                "Service Tier": "Premium",
                "SR Number": f"REAL-SR-{index:04d}",
                "Case Number": f"REAL-SR-{index:04d}",
                "Title": f"Sensitive support inquiry {index}",
                "Case Status": "Closed",
                "Transaction ID": f"REAL-TXN-{index:04d}" if index % 2 else "",
                "Date/Time Opened": opened[index],
                "Date/Time Closed": f"7/{index % 9 + 2}/2026 9:20 AM",
                "Current Contact Email": f"person{index}@sensitive.example",
                "Case Owner: Full Name": f"Sensitive Owner {index}",
                "Problem Description": f"Sensitive problem {index}",
                "CSE Action Plan": f"Sensitive action {index}",
                "Last Cisco Update": f"Sensitive update {index}",
                "Resolution Summary": f"Sensitive resolution {index}",
                "Problem Details": f"Sensitive details {index}",
                "Customer Activity": f"Sensitive activity {index}",
                "Problem Code": "CONFIG",
                "Resolution Code": "FIXED",
                "Case Origin": "Web",
                "Highest Priority": str(index % 4 + 1),
                "# of Case Owner Changes": index % 4,
            }
            for index in range(rows)
        ]
    )


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    for index, modified in enumerate((2024, 2025, 2026), start=1):
        workbook = openpyxl.Workbook()
        workbook.active.append(["placeholder", "header", "row"])
        workbook.properties.created = datetime(modified, 1, 1, tzinfo=timezone.utc)
        workbook.properties.modified = datetime(modified, 1, 2, tzinfo=timezone.utc)
        workbook.save(root / f"fixture-{index}.xlsx")
        workbook.close()
    return root


def _prepared(tmp_path: Path) -> tuple[PreparedCsoneReplay, list[str], Path]:
    root = _corpus(tmp_path)
    calls: list[str] = []

    def loader(path: str) -> pd.DataFrame:
        calls.append(path)
        return _source_frame()

    prepared = prepare_csone_replay(
        build_scenario_bundle("multi_manager"),
        root,
        loader=loader,
        max_rows=18,
        manifest_sha256=MANIFEST_SHA256,
        strict_breadth=True,
    )
    return prepared, calls, root


def _prepared_with_status(
    tmp_path: Path,
    status: str,
) -> PreparedCsoneReplay:
    root = _corpus(tmp_path)

    def loader(_path: str) -> pd.DataFrame:
        frame = _source_frame()
        frame["Case Status"] = status
        return frame

    return prepare_csone_replay(
        build_scenario_bundle("multi_manager"),
        root,
        loader=loader,
        max_rows=18,
        manifest_sha256=MANIFEST_SHA256,
        strict_breadth=True,
    )


def test_one_preparation_is_exactly_n_plus_s_and_safe(tmp_path: Path) -> None:
    prepared, calls, _root = _prepared(tmp_path)
    summary = prepared_replay_summary(prepared)
    instrumentation = summary["prepared_replay"]["instrumentation"]

    assert instrumentation == {
        "preparation_count": 1,
        "profile_loader_calls": 3,
        "selected_reload_calls": 3,
        "total_loader_calls": 6,
    }
    assert len(calls) == 6
    assert summary["replay"]["privacy_contract"]["validated"] is True
    serialized = prepared.payload.decode("utf-8")
    payload = json.loads(serialized)
    assert "Sensitive" not in serialized
    assert "REAL-SR" not in serialized
    assert str(_root) not in serialized
    assert payload["source_snapshot"]["before_sha256"] == payload[
        "source_snapshot"
    ]["after_sha256"]
    assert (
        payload["source_snapshot"]["before_sha256"]
        == prepared.source_snapshot_sha256
    )


@pytest.mark.parametrize(
    ("source_status", "raw_missing", "populated_unclassified"),
    (("", 18, 0), ("telemetry-state-x9", 0, 18)),
)
def test_status_coverage_preserves_raw_missing_and_unclassified_truth(
    tmp_path: Path,
    source_status: str,
    raw_missing: int,
    populated_unclassified: int,
) -> None:
    prepared = _prepared_with_status(tmp_path, source_status)
    summary = prepared_replay_summary(prepared)
    coverage = summary["replay"]["status_coverage"]

    assert coverage == {
        "schema_version": "csone-status-coverage/v1",
        "row_count": 18,
        "raw_missing_count": raw_missing,
        "raw_missing_ratio": round(raw_missing / 18, 6),
        "normalized_unknown_count": 18,
        "normalized_unknown_ratio": 1.0,
        "populated_unclassified_count": populated_unclassified,
        "classified_count": 0,
        "source_state": "partial",
    }
    assert summary["replay"]["missing_status_rows"] == 18
    assert summary["replay"]["raw_missing_status_rows"] == raw_missing
    expected_hash = hashlib.sha256(_canonical_bytes(coverage)).hexdigest()
    assert summary["replay"]["status_coverage_sha256"] == expected_hash
    assert summary["prepared_replay"]["status_coverage_sha256"] == expected_hash


def test_status_coverage_keeps_hostile_populated_tokens_out_of_raw_missing() -> None:
    source = _source_frame(rows=8)
    source["Case Status"] = [
        "",
        None,
        "null",
        "=CMD()",
        "bad\x07state",
        "x" * 257,
        "telemetry-state-x9",
        "Closed",
    ]

    replay = pseudonymize_csone_frame(
        source,
        build_scenario_bundle("healthy"),
        max_rows=8,
    )

    assert replay.attrs["tac_status_coverage"] == {
        "schema_version": "csone-status-coverage/v1",
        "row_count": 8,
        "raw_missing_count": 3,
        "raw_missing_ratio": 0.375,
        "normalized_unknown_count": 7,
        "normalized_unknown_ratio": 0.875,
        "populated_unclassified_count": 4,
        "classified_count": 1,
        "source_state": "partial",
    }


def test_partial_status_provenance_is_identical_for_tac_bems_and_facts(
    tmp_path: Path,
) -> None:
    prepared = _prepared_with_status(tmp_path, "")
    replayed = apply_prepared_csone_replay(
        build_scenario_bundle("healthy"),
        prepared,
        manifest_sha256=MANIFEST_SHA256,
    )

    tac = replayed.frame("tac_cases")
    bems = replayed.frame("bems_cases")
    assert tac.attrs["source_state"] == "partial"
    assert bems.attrs["source_state"] == "partial"
    assert tac.attrs["tac_status_coverage"] == bems.attrs["tac_status_coverage"]
    assert tac.attrs["status_coverage_sha256"] == bems.attrs[
        "status_coverage_sha256"
    ]
    assert canonical.source_data_state(tac) == canonical.source_data_state(bems)
    assert canonical.source_data_state(tac)["state"] == "partial"
    assert replayed.source_states["tac_cases"] == "partial"
    assert replayed.source_states["bems_cases"] == "partial"
    assert "source_partial" in replayed.warning_codes["tac_cases"]
    assert "source_partial" in replayed.warning_codes["bems_cases"]
    replayed.assert_reconciled()


def test_all_canonical_report_families_consume_partial_tac_status_provenance(
    tmp_path: Path,
) -> None:
    prepared = _prepared_with_status(tmp_path, "telemetry-state-x9")
    replayed = apply_prepared_csone_replay(
        build_scenario_bundle("healthy"),
        prepared,
        manifest_sha256=MANIFEST_SHA256,
    )
    member_data = {
        key: replayed.frame(key)
        for key in (
            "subscriptions",
            "action_plans",
            "adoption_barriers",
            "customer_pulse",
            "tac_cases",
            "success_priorities",
        )
    }
    observed: dict[str, tuple[str, str]] = {}
    for report_type in ("Leader", "Comprehensive", "Compact", "Renewal"):
        facts = delivery.build_report_facts(
            {"Alex Rivera": member_data},
            report_type=report_type,
            scope_type="team",
            scope_value="Alex Rivera team",
            manager_name="Alex Rivera",
            days=90,
            as_of=replayed.as_of_utc,
            data_as_of_utc=replayed.as_of_utc,
            data_as_of_state="available",
            data_mode="guarded offline fixture",
            live_validation_performed=False,
            external_incidents=replayed.frame("external_incidents").to_dict(
                orient="records"
            ),
            external_bugs=replayed.frame("external_bugs").to_dict(
                orient="records"
            ),
        )
        tac_source_state = str(
            facts["source_coverage"]
            .set_index("Source_Sheet")
            .loc["TAC_Cases", "Source_State"]
        )
        observed[report_type] = (
            str(facts["risk_summary"]["source_state"]),
            tac_source_state,
        )

    assert observed == {
        report_type: ("partial", "partial")
        for report_type in ("Leader", "Comprehensive", "Compact", "Renewal")
    }


def test_prepare_cli_emits_only_canonical_payload_on_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    from scripts import run_csone_corpus_replay as runner

    root = _corpus(tmp_path)
    summary_path = tmp_path / "summary.json"
    monkeypatch.setattr(runner.backend, "load_csone_excel", lambda _path: _source_frame())

    exit_code = runner.main(
        [
            "--input-dir",
            str(root),
            "--max-rows",
            "18",
            "--manifest",
            str(DEFAULT_MANIFEST_PATH),
            "--summary",
            str(summary_path),
            "--prepared-stdout",
        ]
    )
    captured = capsysbinary.readouterr()
    prepared = prepared_replay_from_bytes(
        captured.out,
        expected_length=len(captured.out),
        expected_sha256=hashlib.sha256(captured.out).hexdigest(),
        expected_as_of_utc=build_scenario_bundle("multi_manager").as_of_utc,
        expected_manifest_sha256=MANIFEST_SHA256,
        expected_max_rows=18,
    )

    assert exit_code == 0
    assert prepared.payload == captured.out
    assert captured.out.startswith(b'{"clock":')
    assert summary_path.is_file()


def test_same_immutable_replay_deep_copies_into_both_runtimes(tmp_path: Path) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    hashes: list[dict[str, object]] = []
    frames: list[pd.DataFrame] = []
    for scenario in ("healthy", "multi_manager"):
        installation = install_runtime_adapters(
            build_scenario_bundle(scenario),
            prepared_csone_replay=prepared,
            manifest_sha256=MANIFEST_SHA256,
        )
        try:
            hashes.append(installation_summary(installation))
            frames.append(installation.bundle.frame("tac_cases"))
        finally:
            installation.restore()

    for field in (
        "prepared_replay_sha256",
        "prepared_frame_sha256",
        "prepared_coverage_sha256",
        "prepared_source_snapshot_sha256",
    ):
        assert hashes[0][field] == hashes[1][field]
    assert hashes[0]["consumer_loader_calls"] == 0
    assert hashes[1]["consumer_loader_calls"] == 0
    frames[0].loc[0, "Title"] = "mutated consumer copy"
    replayed = apply_prepared_csone_replay(
        build_scenario_bundle("healthy"),
        prepared,
        manifest_sha256=MANIFEST_SHA256,
    )
    assert "mutated consumer copy" not in set(replayed.frame("tac_cases")["Title"])


def test_dynamic_consumer_proof_surfaces_late_source_loader_attempt(
    tmp_path: Path,
) -> None:
    import app_simple

    prepared, _calls, _root = _prepared(tmp_path)
    installation = install_runtime_adapters(
        build_scenario_bundle("healthy"),
        app_simple,
        prepared_csone_replay=prepared,
        manifest_sha256=MANIFEST_SHA256,
    )
    try:
        assert installation_summary(installation)["consumer_loader_calls"] == 0
        with pytest.raises(UnexpectedLiveDependency, match="source workbook load"):
            app_simple.load_csone_excel(tmp_path / "late-source.xlsx")
        assert installation_summary(installation)["consumer_loader_calls"] == 1
    finally:
        installation.restore()


def test_dynamic_consumer_proof_rejects_post_bind_frame_mutation(
    tmp_path: Path,
) -> None:
    import app_simple

    prepared, _calls, _root = _prepared(tmp_path)
    installation = install_runtime_adapters(
        build_scenario_bundle("healthy"),
        app_simple,
        prepared_csone_replay=prepared,
        manifest_sha256=MANIFEST_SHA256,
    )
    try:
        installation.bundle.frames["tac_cases"].loc[0, "Title"] = (
            "mutated after prepared replay bind"
        )
        with pytest.raises(ValueError, match="identity is inconsistent"):
            installation_summary(installation)
    finally:
        installation.restore()


def test_dynamic_consumer_proof_rejects_post_bind_bems_mutation(
    tmp_path: Path,
) -> None:
    import app_simple

    prepared, _calls, _root = _prepared(tmp_path)
    installation = install_runtime_adapters(
        build_scenario_bundle("healthy"),
        app_simple,
        prepared_csone_replay=prepared,
        manifest_sha256=MANIFEST_SHA256,
    )
    try:
        installation.bundle.frames["bems_cases"].loc[0, "Title"] = (
            "mutated BEMS after prepared replay bind"
        )
        with pytest.raises(ValueError, match="identity is inconsistent"):
            installation_summary(installation)
    finally:
        installation.restore()


def test_runtime_installation_context_manager_restores_patches(
    tmp_path: Path,
) -> None:
    import app_simple

    prepared, _calls, _root = _prepared(tmp_path)
    original_loader = app_simple.load_csone_excel

    with install_runtime_adapters(
        build_scenario_bundle("healthy"),
        app_simple,
        prepared_csone_replay=prepared,
        manifest_sha256=MANIFEST_SHA256,
    ) as installation:
        assert app_simple.load_csone_excel is not original_loader
        assert installation_summary(installation)["consumer_loader_calls"] == 0

    assert app_simple.load_csone_excel is original_loader


def test_child_connectivity_reports_post_deserialize_identity(tmp_path: Path) -> None:
    import app_simple

    prepared, _calls, _root = _prepared(tmp_path)
    installation = install_runtime_adapters(
        build_scenario_bundle("healthy"),
        app_simple,
        prepared_csone_replay=prepared,
        manifest_sha256=MANIFEST_SHA256,
    )
    try:
        response = app_simple.app.test_client().get("/api/diag/connectivity")
        payload = response.get_json()
    finally:
        installation.restore()

    assert response.status_code == 200
    assert payload["prepared_replay_sha256"] == prepared.payload_sha256
    assert payload["prepared_frame_sha256"] == prepared.frame_sha256
    assert payload["prepared_coverage_sha256"] == prepared.coverage_sha256
    assert payload["prepared_source_snapshot_sha256"] == prepared.source_snapshot_sha256
    assert payload["consumer_loader_calls"] == 0


def test_local_app_subprocess_deserializes_exact_prepared_stdin(tmp_path: Path) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    state_dir = tmp_path / "state"
    env = dict(os.environ)
    env.update(
        {
            "ADOPTIQ_LOCAL_ACCEPTANCE_STATE_DIR": str(state_dir),
            "ADOPTIQ_OUTPUTS_DIR": str(state_dir / "reports"),
        }
    )
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(acceptance.REPO_ROOT / "scripts" / "run_local_acceptance_app.py"),
            "--enable-local-fixtures",
            "--scenario",
            "healthy",
            "--manifest",
            str(DEFAULT_MANIFEST_PATH),
            "--prepared-replay-stdin",
            "--prepared-replay-bytes",
            str(prepared.byte_length),
            "--prepared-replay-sha256",
            prepared.payload_sha256,
            "--prepared-replay-manifest-sha256",
            MANIFEST_SHA256,
            "--csone-replay-max-rows",
            str(prepared.max_rows),
            "--validate-only",
        ],
        cwd=acceptance.REPO_ROOT,
        env=env,
        input=prepared.payload,
        capture_output=True,
        check=False,
        timeout=60,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["prepared_replay_sha256"] == prepared.payload_sha256
    assert payload["prepared_frame_sha256"] == prepared.frame_sha256
    assert payload["prepared_coverage_sha256"] == prepared.coverage_sha256
    assert payload["consumer_loader_calls"] == 0


def test_parent_probe_rejects_missing_or_parent_assumed_child_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "ok": True,
                "mode": "local_fixture_guarded",
                "live_validation_performed": False,
                # Deliberately omit the child-owned prepared identity.
            }

    monkeypatch.setattr(acceptance.requests, "get", lambda *_args, **_kwargs: Response())
    result = acceptance._probe_prepared_runtime_identity(  # noqa: SLF001
        "http://127.0.0.1:5153", prepared, timeout=1
    )
    assert result["ok"] is False
    assert result["exact_hashes"] is False
    assert result["consumer_loader_calls"] == -1


@pytest.mark.parametrize("mutation", ("truncate", "trailing", "byte"))
def test_transport_rejects_truncation_trailing_and_tamper(
    tmp_path: Path, mutation: str
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    payload = prepared.payload
    if mutation == "truncate":
        payload = payload[:-1]
    elif mutation == "trailing":
        payload += b"x"
    else:
        payload = bytes([payload[0] ^ 1]) + payload[1:]

    with pytest.raises(ValueError):
        prepared_replay_from_bytes(
            payload,
            expected_length=prepared.byte_length,
            expected_sha256=prepared.payload_sha256,
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


def test_transport_rejects_rehashed_privacy_and_hash_contract_tamper(
    tmp_path: Path,
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    root = json.loads(prepared.payload)
    root["tac"]["records"][0]["Title"] = "Sensitive source title"
    payload = json.dumps(
        root, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()

    with pytest.raises(ValueError, match="aggregate hash|policy"):
        prepared_replay_from_bytes(
            payload,
            expected_length=len(payload),
            expected_sha256=digest,
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


def test_transport_rejects_rehashed_status_ratio_tamper(tmp_path: Path) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    root = json.loads(prepared.payload)
    root["tac"]["status_coverage"]["raw_missing_ratio"] = 0.5
    payload = _canonical_bytes(root)

    with pytest.raises(ValueError, match="status coverage ratio"):
        prepared_replay_from_bytes(
            payload,
            expected_length=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


def test_transport_rejects_fully_rehashed_status_contract_not_matching_rows(
    tmp_path: Path,
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    root = json.loads(prepared.payload)
    coverage = root["tac"]["status_coverage"]
    coverage.update(
        {
            "raw_missing_count": 1,
            "raw_missing_ratio": round(1 / 18, 6),
            "normalized_unknown_count": 1,
            "normalized_unknown_ratio": round(1 / 18, 6),
            "populated_unclassified_count": 0,
            "classified_count": 17,
            "source_state": "partial",
        }
    )
    root["hashes"]["status_coverage_sha256"] = hashlib.sha256(
        _canonical_bytes(coverage)
    ).hexdigest()
    payload = _canonical_bytes(root)

    with pytest.raises(ValueError, match="status coverage does not reconcile"):
        prepared_replay_from_bytes(
            payload,
            expected_length=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("workbook_count", "3"),
        ("total_bytes", -1),
    ),
)
def test_transport_rejects_rehashed_source_inventory_count_tamper(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    root = json.loads(prepared.payload)
    root["source_snapshot"][field] = value
    payload = json.dumps(
        root, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()

    with pytest.raises(ValueError, match="source snapshot count"):
        prepared_replay_from_bytes(
            payload,
            expected_length=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


def test_transport_requires_canonical_json_even_with_matching_hash(tmp_path: Path) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    payload = json.dumps(json.loads(prepared.payload), indent=2).encode()

    with pytest.raises(ValueError, match="not canonical"):
        prepared_replay_from_bytes(
            payload,
            expected_length=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


def test_transport_rejects_rehashed_before_after_source_digest_mismatch(
    tmp_path: Path,
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    root = json.loads(prepared.payload)
    root["source_snapshot"]["after_sha256"] = "f" * 64
    payload = json.dumps(
        root, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()

    with pytest.raises(ValueError, match="source snapshot changed"):
        prepared_replay_from_bytes(
            payload,
            expected_length=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("loader_result", "source_path", "/sensitive/customer/export.xlsx"),
        ("selected_file", "raw_customer", "Sensitive Customer"),
        ("stratum", "raw_customer", "Sensitive Customer"),
        ("loader", "breadth_reasons", ["customer-specific failure"]),
        ("loader_result", "strata", ["old", "/sensitive/customer"]),
    ),
)
def test_transport_rejects_rehashed_nested_privacy_schema_injection(
    tmp_path: Path,
    target: str,
    field: str,
    value: object,
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    root = json.loads(prepared.payload)
    if target == "loader_result":
        contract = root["loader_contract"]["results"][0]
        aggregate = "loader_contract"
    elif target == "selected_file":
        contract = root["coverage"]["selected_files"][0]
        aggregate = "coverage"
    elif target == "stratum":
        first_label = next(iter(root["coverage"]["strata"]))
        contract = root["coverage"]["strata"][first_label]
        aggregate = "coverage"
    else:
        contract = root["loader_contract"]
        aggregate = "loader_contract"
    contract[field] = deepcopy(value)
    root["hashes"][f"{aggregate}_sha256"] = hashlib.sha256(
        _canonical_bytes(root[aggregate])
    ).hexdigest()
    payload = _canonical_bytes(root)

    with pytest.raises(ValueError, match="loader|selected-file|stratum|enum"):
        prepared_replay_from_bytes(
            payload,
            expected_length=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


@pytest.mark.parametrize("target", ("aggregate", "representative"))
def test_transport_rejects_rehashed_date_parse_failure_contract(
    tmp_path: Path,
    target: str,
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    root = json.loads(prepared.payload)
    if target == "aggregate":
        root["loader_contract"]["date_parse_failure_count"] = 1
    else:
        root["loader_contract"]["results"][0]["date_parse_failure_count"] = 1
    root["hashes"]["loader_contract_sha256"] = hashlib.sha256(
        _canonical_bytes(root["loader_contract"])
    ).hexdigest()
    payload = _canonical_bytes(root)

    with pytest.raises(ValueError, match="unparseable|loader result"):
        prepared_replay_from_bytes(
            payload,
            expected_length=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_as_of_utc=prepared.as_of_utc,
            expected_manifest_sha256=MANIFEST_SHA256,
            expected_max_rows=prepared.max_rows,
        )


def test_apply_binds_frozen_object_fields_to_payload(tmp_path: Path) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    forged = replace(prepared, manifest_sha256="f" * 64)

    with pytest.raises(ValueError, match="object manifest identity"):
        apply_prepared_csone_replay(
            build_scenario_bundle("healthy"),
            forged,
            manifest_sha256=MANIFEST_SHA256,
        )


@pytest.mark.parametrize(
    ("clock", "manifest", "max_rows"),
    (
        ("2026-08-04T21:00:00Z", MANIFEST_SHA256, 18),
        ("2026-08-03T21:00:00Z", "f" * 64, 18),
        ("2026-08-03T21:00:00Z", MANIFEST_SHA256, 17),
    ),
)
def test_transport_binds_clock_manifest_and_max_rows(
    tmp_path: Path, clock: str, manifest: str, max_rows: int
) -> None:
    prepared, _calls, _root = _prepared(tmp_path)
    with pytest.raises(ValueError):
        prepared_replay_from_bytes(
            prepared.payload,
            expected_length=prepared.byte_length,
            expected_sha256=prepared.payload_sha256,
            expected_as_of_utc=clock,
            expected_manifest_sha256=manifest,
            expected_max_rows=max_rows,
        )


def test_source_stat_change_during_preparation_fails_closed(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    calls = 0

    def loader(_path: str) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        if calls == 4:
            target = sorted(root.glob("*.xlsx"))[0]
            os.utime(target, None)
        return _source_frame()

    with pytest.raises(ValueError, match="changed"):
        prepare_csone_replay(
            build_scenario_bundle("multi_manager"),
            root,
            loader=loader,
            max_rows=18,
            manifest_sha256=MANIFEST_SHA256,
            strict_breadth=True,
        )


def test_same_size_content_change_with_restored_mtime_fails_snapshot(
    tmp_path: Path,
) -> None:
    root = _corpus(tmp_path)
    target = sorted(root.glob("*.xlsx"))[0]
    original_stat = target.stat()
    calls = 0

    def loader(_path: str) -> pd.DataFrame:
        nonlocal calls
        calls += 1
        if calls == 4:
            raw = bytearray(target.read_bytes())
            raw[-1] ^= 1
            target.write_bytes(raw)
            os.utime(
                target,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            assert target.stat().st_size == original_stat.st_size
            assert target.stat().st_mtime_ns == original_stat.st_mtime_ns
        return _source_frame()

    with pytest.raises(ValueError, match="changed"):
        prepare_csone_replay(
            build_scenario_bundle("multi_manager"),
            root,
            loader=loader,
            max_rows=18,
            manifest_sha256=MANIFEST_SHA256,
            strict_breadth=True,
        )


def test_corpus_workbook_count_limit_rejects_before_loader(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    calls: list[str] = []
    limits = replace(CorpusInputLimits(), max_workbooks=2)

    with pytest.raises(ValueError, match="workbook_count"):
        prepare_csone_replay(
            build_scenario_bundle("multi_manager"),
            root,
            loader=lambda path: calls.append(path) or _source_frame(),
            max_rows=18,
            manifest_sha256=MANIFEST_SHA256,
            strict_breadth=True,
            input_limits=limits,
        )

    assert calls == []


def test_corpus_workbook_size_limit_rejects_before_loader(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    calls: list[str] = []
    limits = replace(CorpusInputLimits(), max_workbook_bytes=128)

    with pytest.raises(ValueError, match="workbook_size"):
        validate_representative_loaders(
            root,
            loader=lambda path: calls.append(path) or _source_frame(),
            max_rows=18,
            input_limits=limits,
        )

    assert calls == []


@pytest.mark.parametrize(
    ("limit_field", "limit_value", "failure_kind"),
    (
        ("max_zip_entries", 1, "zip_entry_count"),
        ("max_zip_expansion_ratio", 2.0, "zip_expansion_ratio"),
    ),
)
def test_pathological_xlsx_rejected_before_loader(
    tmp_path: Path,
    limit_field: str,
    limit_value: object,
    failure_kind: str,
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    path = root / "pathological.xlsx"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"0" * (1024 * 1024))
        archive.writestr("xl/workbook.xml", b"1" * 4096)
    calls: list[str] = []
    limits = replace(CorpusInputLimits(), **{limit_field: limit_value})

    with pytest.raises(ValueError, match=failure_kind):
        validate_representative_loaders(
            root,
            loader=lambda source: calls.append(source) or _source_frame(),
            max_rows=18,
            input_limits=limits,
        )

    assert calls == []


def test_explicit_csone_date_format_is_warning_free_and_surfaces_failures() -> None:
    values = pd.Series(
        [
            "7/2/2026 8:15 AM",
            "7/3/2026 9:20 PM",
            "not-a-supported-date",
            "2026-13-99",
        ]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        maximum, failures = _timestamp_summary(values)

    assert maximum == pd.Timestamp("2026-07-03T21:20:00Z").value
    assert failures == 2


def test_pandas_legacy_iso_fallback_coerces_invalid_dates_without_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_to_datetime = pd.to_datetime

    def legacy_to_datetime(*args: object, **kwargs: object) -> object:
        if kwargs.get("format") == "ISO8601":
            raise TypeError("legacy pandas has no ISO8601 format sentinel")
        return real_to_datetime(*args, **kwargs)

    monkeypatch.setattr(pd, "to_datetime", legacy_to_datetime)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        maximum, failures = _timestamp_summary(
            pd.Series(["2026-08-03T12:00:00Z", "2026-13-99"])
        )

    assert maximum == pd.Timestamp("2026-08-03T12:00:00Z").value
    assert failures == 1


def test_strict_prepared_replay_rejects_aggregate_date_parse_failures(
    tmp_path: Path,
) -> None:
    root = _corpus(tmp_path)
    loader = lambda _path: _source_frame(invalid_date=True)
    diagnostic = validate_representative_loaders(
        root,
        loader=loader,
        max_rows=18,
    )
    assert diagnostic["date_parse_failure_count"] == 3

    with pytest.raises(ValueError, match="unparseable declared date"):
        prepare_csone_replay(
            build_scenario_bundle("multi_manager"),
            root,
            loader=loader,
            max_rows=18,
            manifest_sha256=MANIFEST_SHA256,
            strict_breadth=True,
        )


def test_bounded_gate_output_stops_at_cap_without_partial_digest() -> None:
    result = acceptance._run_command(  # noqa: SLF001
        [sys.executable, "-c", "import sys;sys.stdout.write('x'*4096)"],
        timeout_seconds=10,
        output_limit_bytes=1024,
    )

    assert result["ok"] is False
    assert result["output_truncated"] is True
    assert result["stdout_bytes"] == 1025
    assert result["stdout_sha256"] == ""
    assert result["stdout_digest_complete"] is False


def test_bounded_capture_contains_cleanup_permission_error() -> None:
    read_fd, write_fd = os.pipe()
    stream = os.fdopen(read_fd, "rb", buffering=0)
    callback_calls = 0

    def denied_cleanup() -> None:
        nonlocal callback_calls
        callback_calls += 1
        raise PermissionError("host denied process-group cleanup")

    capture = acceptance._BoundedPipeCapture(  # noqa: SLF001
        stream,
        limit_bytes=1,
        on_exceeded=denied_cleanup,
    )
    capture.start()
    os.write(write_fd, b"xx")
    os.close(write_fd)
    capture.join()

    assert capture.exceeded.is_set()
    assert capture.error_kind == "output_limit"
    assert capture.byte_count == 2
    assert callback_calls == 1
    assert capture.thread.is_alive() is False


def test_prepared_runtime_stdin_writer_reaps_child_that_never_reads() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt"
            else 0
        ),
    )

    with pytest.raises(TimeoutError, match="did not consume"):
        acceptance._write_prepared_replay_stdin(  # noqa: SLF001
            process,
            b"x" * (2 * 1024 * 1024),
            timeout_seconds=1,
        )

    assert process.poll() is not None


def test_runtime_log_capture_reaps_before_writing_past_bound(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime.log"
    with log_path.open("wb") as log_handle:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,time;sys.stdout.write('x'*1048576);sys.stdout.flush();time.sleep(60)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )
        assert process.stdout is not None
        capture = acceptance._BoundedPipeCapture(  # noqa: SLF001
            process.stdout,
            limit_bytes=1024,
            sink=log_handle,
            on_exceeded=lambda: acceptance._terminate_gate_process_tree(  # noqa: SLF001
                process
            ),
        )
        capture.start()
        try:
            assert capture.exceeded.wait(timeout=5)
            capture.join(timeout=5)
            assert process.poll() is not None
            assert log_path.stat().st_size <= 1024
            assert capture.sha256 == ""
            assert capture.digest_complete is False
        finally:
            if process.poll() is None:
                acceptance._terminate_gate_process_tree(process)  # noqa: SLF001


def test_prepared_local_profile_materializes_both_runtime_log_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Child status/live evidence must not collide with parent-owned gate fields."""

    clock = "2026-08-03T12:00:00Z"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    summary_path = tmp_path / "matrix-summary.json"
    summary_path.write_text("{}", encoding="utf-8")
    hashes = {
        "prepared_replay_sha256": "a" * 64,
        "prepared_frame_sha256": "b" * 64,
        "prepared_coverage_sha256": "c" * 64,
        "prepared_source_snapshot_sha256": "d" * 64,
    }
    prepared = SimpleNamespace(
        payload_sha256=hashes["prepared_replay_sha256"],
        frame_sha256=hashes["prepared_frame_sha256"],
        coverage_sha256=hashes["prepared_coverage_sha256"],
        source_snapshot_sha256=hashes["prepared_source_snapshot_sha256"],
    )
    producer_gate = {
        "ok": True,
        "status": "passed",
        "prepared_replay_byte_length": 2,
        **hashes,
    }
    observed_identity = {
        "ok": True,
        "status": "passed",
        **hashes,
        "consumer_loader_calls": 0,
        "exact_hashes": True,
        "live_validation_performed": False,
        "production_accuracy_claimed": False,
    }
    runtime_count = 0

    @contextmanager
    def fixture_runtime(*_args: object, **_kwargs: object):
        nonlocal runtime_count
        runtime_count += 1
        log_path = tmp_path / f"runtime-{runtime_count}.log"
        log_path.write_text("bounded fixture runtime evidence\n", encoding="utf-8")
        yield f"http://127.0.0.1:{5152 + runtime_count}", log_path

    monkeypatch.setattr(
        acceptance,
        "load_manifest",
        lambda _path: {"deterministic_clock_utc": clock},
    )
    monkeypatch.setattr(
        acceptance,
        "_run_command",
        lambda *_args, **_kwargs: {"ok": True, "status": "passed"},
    )
    monkeypatch.setattr(
        acceptance,
        "_run_prepared_replay_worker",
        lambda *_args, **_kwargs: (dict(producer_gate), b"{}"),
    )
    monkeypatch.setattr(
        acceptance,
        "prepared_replay_from_bytes",
        lambda *_args, **_kwargs: prepared,
    )
    monkeypatch.setattr(acceptance, "_fixture_runtime", fixture_runtime)
    monkeypatch.setattr(
        acceptance,
        "_probe_prepared_runtime_identity",
        lambda *_args, **_kwargs: dict(observed_identity),
    )
    monkeypatch.setattr(
        acceptance,
        "probe_manager_workspace",
        lambda **_kwargs: {"ok": True, "status": "passed"},
    )
    monkeypatch.setattr(
        acceptance,
        "probe_multi_manager_isolation",
        lambda **_kwargs: {"ok": True, "status": "passed"},
    )
    monkeypatch.setattr(
        acceptance,
        "_find_matrix_summary",
        lambda _path: summary_path,
    )
    monkeypatch.setattr(
        acceptance,
        "_project_matrix",
        lambda *_args, **_kwargs: {"projected_ok": True},
    )
    monkeypatch.setattr(
        acceptance,
        "_project_multi_manager_matrix",
        lambda _payload: {"projected_ok": True},
    )

    args = SimpleNamespace(
        manifest=manifest_path,
        csone_corpus_dir=corpus_dir,
        csone_replay_max_rows=5000,
        days=90,
        request_timeout=1.0,
        scenarios="all",
        skip_ai=False,
        skip_degraded_http=False,
        skip_degraded_reports=False,
        skip_matrix=False,
        skip_replay=True,
    )
    gates = acceptance._local_profile(args, tmp_path / "scratch")  # noqa: SLF001

    assert runtime_count == 2
    for name in ("fixture_runtime_log", "multi_manager_runtime_log"):
        assert gates[name]["ok"] is True
        assert gates[name]["status"] == "passed"
        assert gates[name]["live_validation_performed"] is False
        assert gates[name]["production_accuracy_claimed"] is False
        assert gates[name]["identity_probe_count"] == 2
        assert gates[name]["identity_probe_passed_count"] == 2
        assert gates[name]["consumer_loader_calls"] == 0
        assert "error_kind" not in gates[name]
    assert gates["prepared_replay_identity"]["ok"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertion")
def test_timed_out_gate_reaps_grandchild_process(tmp_path: Path) -> None:
    pid_path = tmp_path / "grandchild.pid"
    code = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid));"
        "time.sleep(60)"
    )
    result = acceptance._run_command(  # noqa: SLF001
        [sys.executable, "-c", code],
        timeout_seconds=1,
        output_limit_bytes=1024,
    )
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert pid_path.is_file()
    grandchild_pid = int(pid_path.read_text())

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("timed-out gate left its grandchild process running")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertion")
def test_successful_gate_leader_cannot_leave_background_grandchild(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "background-grandchild.pid"
    code = (
        "import pathlib,subprocess,sys;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))"
    )
    result = acceptance._run_command(  # noqa: SLF001
        [sys.executable, "-c", code],
        timeout_seconds=10,
        output_limit_bytes=1024,
    )

    assert result["ok"] is True
    grandchild_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("completed gate left its background grandchild running")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertion")
def test_completed_gate_sigkills_background_grandchild_that_ignores_sigterm(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "ignoring-grandchild.pid"
    ready_path = tmp_path / "ignoring-grandchild.ready"
    child_code = (
        "import os,pathlib,signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"pathlib.Path({str(ready_path)!r}).write_text('ready');"
        "time.sleep(60)"
    )
    leader_code = (
        "import pathlib,subprocess,sys,time;"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid));"
        f"ready=pathlib.Path({str(ready_path)!r});"
        "deadline=time.monotonic()+5;"
        "\nwhile not ready.exists() and time.monotonic()<deadline: time.sleep(0.01)"
    )
    result = acceptance._run_command(  # noqa: SLF001
        [sys.executable, "-c", leader_code],
        timeout_seconds=10,
        output_limit_bytes=1024,
    )

    assert result["ok"] is True
    assert ready_path.is_file()
    grandchild_pid = int(pid_path.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("SIGTERM-ignoring background grandchild survived gate cleanup")
