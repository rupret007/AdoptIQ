"""Round 169.5: Grok review pins — honesty, fail-closed, fixture hygiene.

Local/fixture proof is the Cloud/Bob gate. Hosted Actions may stay red
with runner_id=0 / empty steps. AdoptIQ stays PRIVATE. Sim ≠ live.
"""
# Round 169.5

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from adoptiq_backend import _r169_5_explicit_live_validation_performed


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "adoptiq_backend.py"
APP_SIMPLE = ROOT / "app_simple.py"
MAKEFILE = ROOT / "Makefile"
PLAYBOOK = ROOT / "OFFLINE_SIM_PLAYBOOK.md"
HANDOFF = ROOT / "WORK_MAC_CURSOR_HANDOFF.md"
REVIEW = ROOT / "GROK_REVIEW.md"
LOCAL_PROOF = ROOT / "scripts" / "run_offline_sim_local.py"
SIM = ROOT / "scripts" / "run_offline_bob_sim.sh"
R75 = ROOT / "tests" / "test_round75_comp_action_plans_parity_with_leader.py"
SYNTHETIC = ROOT / "testdata" / "synthetic_csone"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        (False, False),
        ("", False),
        ("false", False),
        ("no", False),
        ("off", False),
        (0, False),
        ("0", False),
        (True, True),
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_explicit_live_validation_is_fail_closed(value: object, expected: bool) -> None:
    assert _r169_5_explicit_live_validation_performed(value) is expected


def test_missing_subscription_key_is_not_live() -> None:
    assert _r169_5_explicit_live_validation_performed({}) is False
    # Callers must pass the looked-up value, not the dict.
    assert _r169_5_explicit_live_validation_performed(
        {}.get("live_validation_performed")
    ) is False


def test_fetch_subscription_data_stamps_false_on_every_return() -> None:
    text = BACKEND.read_text(encoding="utf-8")
    start = text.find("def fetch_subscription_data(")
    end = text.find("\ndef search_subscriptions_by_customer(", start)
    body = text[start:end]
    assert body.count("'live_validation_performed': False") == 3
    assert "'live_validation_performed': True" not in body
    assert "live_validation_performed', True)" not in body


def test_app_simple_no_longer_defaults_subscription_live_true() -> None:
    text = APP_SIMPLE.read_text(encoding="utf-8")
    assert 'sub_data.get("live_validation_performed", True)' not in text
    assert "_r169_5_explicit_live_validation_performed(" in text
    assert "Round 169.5" in text


def test_subscription_missing_key_renders_live_validation_no(tmp_path: Path) -> None:
    from test_round146_subscription_artifacts import _run_subscription

    def drop_flag(payload: dict) -> dict:
        out = dict(payload)
        out.pop("live_validation_performed", None)
        return out

    _word_path, source_path = _run_subscription(
        tmp_path,
        analysis_id="sub_SUB-001_1695000100",
        payload_mutator=drop_flag,
    )
    report_info = pd.read_excel(source_path, sheet_name="Report_Info")
    info = dict(zip(report_info["Item"], report_info["Value"], strict=True))
    assert info["Live Validation Performed"] == "No"


def test_subscription_explicit_true_still_renders_yes(tmp_path: Path) -> None:
    from test_round146_subscription_artifacts import _run_subscription

    def force_true(payload: dict) -> dict:
        out = dict(payload)
        out["live_validation_performed"] = True
        return out

    _word_path, source_path = _run_subscription(
        tmp_path,
        analysis_id="sub_SUB-001_1695000200",
        payload_mutator=force_true,
    )
    report_info = pd.read_excel(source_path, sheet_name="Report_Info")
    info = dict(zip(report_info["Item"], report_info["Value"], strict=True))
    assert info["Live Validation Performed"] == "Yes"


def test_csone_corpus_replay_fail_closed_without_dir() -> None:
    env = os.environ.copy()
    env.pop("CSONE_CORPUS_DIR", None)
    completed = subprocess.run(
        ["make", "csone-corpus-replay", "CSONE_CORPUS_DIR="],
        cwd=str(ROOT),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    combined = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 2
    assert "CSONE_CORPUS_DIR is required for csone-corpus-replay" in combined


def test_r75_owner_email_is_fixture_domain() -> None:
    text = R75.read_text(encoding="utf-8")
    assert "sssm@cisco.com" not in text.casefold()
    assert "fixture.owner@example.invalid" in text


def test_synthetic_csone_and_review_stay_secret_free() -> None:
    review = REVIEW.read_text(encoding="utf-8")
    joined = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (
            REVIEW,
            PLAYBOOK,
            HANDOFF,
            *sorted(SYNTHETIC.glob("*.xlsx")),
            SYNTHETIC / "README.md",
        )
        if path.is_file()
    )
    folded = joined.casefold()
    assert "snowflake_password" not in folded
    assert "keeper_token" not in folded
    assert "live_validation_performed=true" not in folded
    assert "release_ready=true" not in folded
    assert "production_accuracy_claimed=true" not in review.casefold()
    assert "stay **private**" in review.casefold() or "stay private" in review.casefold()


def test_playbook_local_proof_is_undraft_gate() -> None:
    playbook = PLAYBOOK.read_text(encoding="utf-8").casefold()
    handoff = HANDOFF.read_text(encoding="utf-8").casefold()
    for text in (playbook, handoff):
        assert "stay **draft**" in text or "keep the pr **draft**" in text
        assert "local proof is the gate" in text or "local/fixture proof is the gate" in text
        assert "do not blame github billing" in text
        assert "runner_id=0" in text
        assert "do not change visibility" in text
        assert "team_config.json" in text
        assert "customer_aliases.defaults.json" in text


def test_offline_sim_local_includes_source_contracts() -> None:
    text = LOCAL_PROOF.read_text(encoding="utf-8")
    assert "run_contracts" in text
    assert "source_contracts" in text
    assert 'SCHEMA_VERSION = "offline-sim-local/v3"' in text
    assert "Round 169.5" in text
    sim = SIM.read_text(encoding="utf-8")
    assert "run_local_source_contracts.py" in sim
    assert "offline-bob-sim/v5" in sim


def test_makefile_corpus_replay_required_message() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "CSONE_CORPUS_DIR is required for csone-corpus-replay" in text
    assert "CSONE_CORPUS_DIR is required for production-simulation" in text


def test_compact_renewal_live_yes_without_fixture_is_documented_residual() -> None:
    # Residual FAIL (not fixed this round): Compact/Renewal stamp Yes whenever
    # LOCAL_ACCEPTANCE_MODE is off. That is application-path, not Jeff's live
    # checklist. Leave live Cisco report presentation alone.
    text = APP_SIMPLE.read_text(encoding="utf-8")
    residual = '"No" if app.config.get("LOCAL_ACCEPTANCE_MODE") else "Yes"'
    assert text.count(residual) >= 2
    review = REVIEW.read_text(encoding="utf-8")
    assert "Compact/Renewal Live Validation Yes" in review
    assert "FAIL" in review
    assert "PASS" in review
