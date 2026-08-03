"""Round 143 supported decision-report rollout acceptance contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from scripts import run_decision_report_acceptance as acceptance


AS_OF = "2026-08-03T12:00:00Z"


def test_public_workbook_inventory_is_the_exact_acceptance_inventory() -> None:
    assert acceptance.delivery.SOURCE_DATA_SHEET_NAMES == (
        "Report_Info",
        "Metric_Lineage",
        "Chart_Data",
        "Action_Plans",
        "Adoption_Barriers",
        "Customer_Pulse",
        "TAC_Cases",
        "BEMS",
        "Subscriptions",
        "Success_Priorities",
        "External_Incidents",
        "External_Bugs",
        "Risk_Components",
        "Member_Summary",
        "Account_Summary",
    )


def test_scope_authorization_probes_fail_closed() -> None:
    probes = acceptance.run_scope_authorization_probes()

    assert probes == {
        "outside_manager_member_rejected": True,
        "ambiguous_shared_account_customer_rejected": True,
        "ok": True,
    }


def test_in_repo_output_must_be_ignored(tmp_path: Path) -> None:
    unsafe = acceptance.REPO_ROOT / "round143-unignored-artifacts"
    with pytest.raises(ValueError, match="must be Git-ignored"):
        acceptance._ensure_safe_output_dir(unsafe)  # noqa: SLF001

    conventional = acceptance._ensure_safe_output_dir(  # noqa: SLF001
        acceptance.REPO_ROOT / ".adoptiq-acceptance"
    )
    assert conventional == acceptance.REPO_ROOT / ".adoptiq-acceptance"
    assert acceptance._ensure_safe_output_dir(tmp_path) == tmp_path.resolve()  # noqa: SLF001


def test_explicit_live_mode_never_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance,
        "probe_live_sources",
        lambda _base_url: {"ok": False, "snowflake": {"ok": False}},
    )

    exit_code = acceptance.main(
        [
            "--mode",
            "live",
            "--manager",
            "Brian Frazier",
            "--days",
            "90",
            "--as-of",
            AS_OF,
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 5
    summary = json.loads(
        (tmp_path / "decision_report_acceptance_summary.json").read_text()
    )
    assert summary["mode_executed"] == "live"
    assert summary["live_validation_performed"] is True
    assert summary["passes"] == []
    assert summary["all_passed"] is False


def test_auto_mode_records_honest_offline_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance,
        "probe_live_sources",
        lambda _base_url: {"ok": False, "snowflake": {"ok": False}},
    )
    monkeypatch.setattr(
        acceptance,
        "run_offline_pass",
        lambda **kwargs: {
            "pass_number": kwargs["pass_number"],
            "mode": "offline",
            "scopes": {scope: {"ok": True} for scope in acceptance.SUPPORTED_SCOPES},
            "ok": True,
        },
    )
    monkeypatch.setattr(
        acceptance,
        "compare_passes",
        lambda _passes, *, live: {"ok": not live, "scopes": {}},
    )

    exit_code = acceptance.main(
        [
            "--mode",
            "auto",
            "--manager",
            "Brian Frazier",
            "--days",
            "90",
            "--as-of",
            AS_OF,
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    summary = json.loads(
        (tmp_path / "decision_report_acceptance_summary.json").read_text()
    )
    assert summary["mode_executed"] == "offline"
    assert summary["live_validation_performed"] is False
    assert "unavailable" in summary["limitations"][0]
    assert summary["all_passed"] is True


def test_compare_passes_requires_byte_identity_offline_but_not_live() -> None:
    def scope_result(word_hash: str, fact_hash: str) -> dict:
        return {
            "ok": True,
            "artifacts": {
                "word_sha256": word_hash,
                "source_data_sha256": "workbook",
            },
            "report_metadata": {
                "fact_contract_sha256": fact_hash,
                "sheet_sha256": {"Action_Plans": "stable"},
            },
            "source_states": {"Action_Plans": "available"},
            "source_counts": {"Action_Plans": 1},
            "record_id_quality": {},
            "action_plan_lifecycle": {"total": 1},
            "tac_bems": {},
        }

    first = {
        "scopes": {
            scope: scope_result("word-one", "fact-one")
            for scope in acceptance.SUPPORTED_SCOPES
        }
    }
    second = {
        "scopes": {
            scope: scope_result("word-two", "fact-two")
            for scope in acceptance.SUPPORTED_SCOPES
        }
    }

    assert acceptance.compare_passes([first, second], live=False)["ok"] is False
    assert acceptance.compare_passes([first, second], live=True)["ok"] is True


@pytest.mark.skipif(
    importlib.util.find_spec("matplotlib") is None,
    reason="real chart acceptance requires the application requirements runtime",
)
def test_full_offline_command_generates_and_validates_all_scopes_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        acceptance,
        "probe_live_sources",
        lambda _base_url: {"ok": False, "snowflake": {"ok": False}},
    )

    exit_code = acceptance.main(
        [
            "--mode",
            "offline",
            "--manager",
            "Brian Frazier",
            "--days",
            "90",
            "--as-of",
            AS_OF,
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    summary = json.loads(
        (tmp_path / "decision_report_acceptance_summary.json").read_text()
    )
    assert summary["all_passed"] is True
    assert summary["mode_executed"] == "offline"
    assert len(summary["passes"]) == 2
    assert summary["repeatability"]["ok"] is True
    for result_pass in summary["passes"]:
        assert set(result_pass["scopes"]) == set(acceptance.SUPPORTED_SCOPES)
        assert all(item["ok"] for item in result_pass["scopes"].values())
    for scope in acceptance.SUPPORTED_SCOPES:
        repeat = summary["repeatability"]["scopes"][scope]
        assert repeat["byte_identical"] is True
        assert repeat["fact_fingerprint_identical"] is True
        assert repeat["sheet_hashes_identical"] is True
        assert repeat["semantic_metrics_identical"] is True


def test_comprehensive_status_exposes_the_canonical_prefetch_clock() -> None:
    source = (acceptance.REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    marker = "data_retrieved_at = comprehensive_prefetch_ctx.data_retrieved_at"
    start = source.index(marker)
    region = source[start : start + 700]

    assert "status['data_retrieved_at'] = data_retrieved_at.isoformat()" in region


def test_live_scope_selection_uses_authorized_roster_and_customer_options() -> None:
    class FakeClient:
        def scope_options(
            self,
            manager: str,
            *,
            member_email: str = "",
            include_customers: bool = False,
        ) -> dict:
            assert manager == "Brian Frazier"
            if include_customers:
                assert member_email == "alex@example.test"
                return {
                    "customers_available": True,
                    "customers": [{"value": "Acme Corporation"}],
                }
            return {
                "members": [
                    {"name": "Alex Rivera", "email": "alex@example.test"},
                    {"name": "Morgan Lee", "email": "morgan@example.test"},
                ]
            }

    selected = acceptance._choose_live_scopes(  # noqa: SLF001
        FakeClient(),
        manager="Brian Frazier",
        member_email="morgan@example.test",
        customer_name="Acme Corporation",
        customer_member_email="alex@example.test",
    )

    assert selected["member"].scope_value == "Morgan Lee (morgan@example.test)"
    assert selected["customer"].scope_value == "Acme Corporation (Alex Rivera)"
    assert selected["comprehensive"].scope_value == "Brian Frazier team"


def test_live_scope_selection_rejects_unavailable_customer_authorization() -> None:
    class FakeClient:
        def scope_options(
            self,
            _manager: str,
            *,
            member_email: str = "",
            include_customers: bool = False,
        ) -> dict:
            if include_customers:
                return {"customers_available": False, "customers": []}
            return {
                "members": [
                    {"name": "Alex Rivera", "email": "alex@example.test"}
                ]
            }

    with pytest.raises(RuntimeError, match="authorization cannot be proven"):
        acceptance._choose_live_scopes(  # noqa: SLF001
            FakeClient(),
            manager="Brian Frazier",
            member_email="",
            customer_name="",
            customer_member_email="",
        )
