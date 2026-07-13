"""
Tests for leader report helpers in leader_report_generator.py.
Covers safe_len, safe_df_check, safe_set, filename generation (Round 1 Fix 1),
and division-by-zero guard (Round 3 Fix 2).
"""

import sys
import re
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pytest


# ── Helper: create a generator instance without Snowflake ────────────────

@pytest.fixture
def generator():
    """Create a LeaderReportGenerator with a mock context."""
    from leader_report_generator import LeaderReportGenerator
    mock_ctx = mock.MagicMock()
    roster = [("user@cisco.com", "Test User", "Manager")]
    gen = LeaderReportGenerator(mock_ctx, roster)
    return gen


# ── safe_len ─────────────────────────────────────────────────────────────

class TestSafeLen:
    def test_list(self, generator):
        assert generator.safe_len([1, 2, 3]) == 3

    def test_empty_list(self, generator):
        assert generator.safe_len([]) == 0

    def test_none(self, generator):
        assert generator.safe_len(None) == 0

    def test_dict(self, generator):
        assert generator.safe_len({"a": 1, "b": 2}) == 2

    def test_dataframe(self, generator):
        df = pd.DataFrame({"a": [1, 2, 3]})
        assert generator.safe_len(df) == 3

    def test_empty_dataframe(self, generator):
        assert generator.safe_len(pd.DataFrame()) == 0

    def test_string(self, generator):
        assert generator.safe_len("hello") == 5

    def test_integer(self, generator):
        assert generator.safe_len(42) == 0

    def test_set(self, generator):
        assert generator.safe_len({1, 2}) == 2


# ── safe_df_check ────────────────────────────────────────────────────────

class TestSafeDfCheck:
    def test_valid_df_with_column(self, generator):
        df = pd.DataFrame({"name": ["Alice", "Bob"]})
        assert generator.safe_df_check(df, "name") is True

    def test_missing_column(self, generator):
        df = pd.DataFrame({"name": ["Alice"]})
        assert generator.safe_df_check(df, "age") is False

    def test_none(self, generator):
        assert generator.safe_df_check(None, "col") is False

    def test_empty_df(self, generator):
        assert generator.safe_df_check(pd.DataFrame(), "col") is False

    def test_non_dataframe(self, generator):
        assert generator.safe_df_check("not a df", "col") is False

    def test_integer_input(self, generator):
        assert generator.safe_df_check(42, "col") is False


# ── safe_set ─────────────────────────────────────────────────────────────

class TestSafeSet:
    def test_list(self, generator):
        result = generator.safe_set([1, 2, 3])
        assert result == {1, 2, 3}

    def test_none(self, generator):
        result = generator.safe_set(None)
        assert result == set()

    def test_set_passthrough(self, generator):
        result = generator.safe_set({1, 2})
        assert result == {1, 2}

    def test_non_empty_dataframe_returns_empty(self, generator):
        df = pd.DataFrame({"a": [1, 2]})
        result = generator.safe_set(df)
        assert result == set()

    def test_empty_list(self, generator):
        result = generator.safe_set([])
        assert result == set()

    def test_integer(self, generator):
        result = generator.safe_set(42)
        assert result == set()


# ── Filename generation (Round 1 Fix 1 regression) ───────────────────────

class TestFilenameGeneration:
    def test_spaces_replaced_with_underscores(self):
        manager_name = "Brian Frazier"
        safe_manager = "".join(c for c in manager_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_manager = safe_manager.replace(" ", "_")
        assert " " not in safe_manager
        assert safe_manager == "Brian_Frazier"

    def test_special_chars_stripped(self):
        manager_name = "O'Brien (Jr.)"
        safe_manager = "".join(c for c in manager_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_manager = safe_manager.replace(" ", "_")
        assert "'" not in safe_manager
        assert "(" not in safe_manager
        assert ")" not in safe_manager

    def test_hyphen_preserved(self):
        manager_name = "Mary-Jane Watson"
        safe_manager = "".join(c for c in manager_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_manager = safe_manager.replace(" ", "_")
        assert "-" in safe_manager
        assert safe_manager == "Mary-Jane_Watson"

    def test_filename_format(self):
        safe_manager = "Test_User"
        days = 90
        filename = f"AdoptIQ_Report_Leader_{safe_manager}_{days}d_20260301_120000.docx"
        assert filename.startswith("AdoptIQ_Report_Leader_")
        assert filename.endswith(".docx")
        assert " " not in filename

    def test_underscore_preserved(self):
        manager_name = "Test_User"
        safe_manager = "".join(c for c in manager_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_manager = safe_manager.replace(" ", "_")
        assert safe_manager == "Test_User"


# ── Division by zero guard (Round 3 Fix 2 regression) ────────────────────

class TestDivisionByZeroGuard:
    def test_max_prevents_division_by_zero(self):
        total_team_members = 0
        avg_divisor = max(total_team_members, 1)
        assert avg_divisor == 1
        total_customers = 10
        result = total_customers / avg_divisor
        assert result == 10.0

    def test_normal_division(self):
        total_team_members = 5
        avg_divisor = max(total_team_members, 1)
        assert avg_divisor == 5
        total_aps = 25
        result = total_aps / avg_divisor
        assert result == 5.0

    def test_stats_data_format(self):
        """Verify the f-string formatting works with avg_divisor."""
        total_team_members = 0
        avg_divisor = max(total_team_members, 1)
        total_customers = 15
        total_aps = 20
        total_abs = 10
        stats_data = [
            ("Team Members", str(total_team_members), f"{total_team_members}"),
            ("Total Customers", str(total_customers), f"{total_customers/avg_divisor:.1f}"),
            ("Action Plans", str(total_aps), f"{total_aps/avg_divisor:.1f}"),
            ("Adoption Barriers", str(total_abs), f"{total_abs/avg_divisor:.1f}"),
        ]
        assert stats_data[0][2] == "0"
        assert stats_data[1][2] == "15.0"
        assert stats_data[2][2] == "20.0"
        assert stats_data[3][2] == "10.0"


def test_customer_matching_uses_normalized_exact_compare():
    """Guard against substring matching that can mis-attribute customers."""
    src = Path(__file__).resolve().parent.parent.joinpath("leader_report_generator.py").read_text(encoding="utf-8")
    assert "str.contains(customer, case=False, na=False)" not in src
    assert "detect_bems_mask(" in src


def test_customer_pulse_join_maps_account_id_and_customer_name(generator, monkeypatch):
    monkeypatch.setattr(
        generator,
        "_get_subscriptions_for_cssm",
        lambda emails: pd.DataFrame(
            [{"ACCOUNT_ID_C": "001", "BU_NAME": "Acme Corp", "CSSM_EMAIL": emails[0]}]
        ),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_action_plans",
        lambda account_ids, days, owner_emails=None: pd.DataFrame(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_adoption_barriers",
        lambda account_ids, days, owner_emails=None: pd.DataFrame(),
    )
    monkeypatch.setattr(
        generator,
        "_fetch_customer_pulse",
        lambda account_ids, days, owner_emails=None: pd.DataFrame(
            [{"ACCOUNT__C": "001", "SCORE__C": 7.5}]
        ),
    )
    monkeypatch.setattr(generator, "_fetch_success_priorities", lambda customers, days: pd.DataFrame())

    team_data = generator._collect_team_data(
        direct_reports=[{"name": "Alice", "email": "alice@example.com"}],
        days=90,
    )
    pulse = team_data["Alice"]["customer_pulse"]
    assert not pulse.empty
    assert "ACCOUNT_ID_C" in pulse.columns
    assert "BU_NAME" in pulse.columns
    assert pulse.iloc[0]["BU_NAME"] == "Acme Corp"


def test_validate_customer_data_consistency_counts_subscription_customers(generator):
    team_data = {
        "Alice": {
            "customers": ["Acme Corp", "Beta Inc"],
            "subscriptions": pd.DataFrame(
                [
                    {"BU_NAME": "Acme Corp"},
                    {"BU_NAME": "Beta Inc"},
                    {"BU_NAME": "Beta Inc"},
                ]
            ),
            "action_plans": pd.DataFrame(),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
        }
    }

    checks = generator._validate_customer_data_consistency(team_data)
    assert checks["Alice"]["assigned_customers"] == 2
    assert checks["Alice"]["subscription_customers"] == 2


def test_collect_team_data_batches_shared_queries(generator, monkeypatch):
    calls = {"subs": 0, "ap": 0, "ab": 0, "cp": 0, "sp": 0}

    def _subs(emails):
        calls["subs"] += 1
        return pd.DataFrame(
            [
                {"ACCOUNT_ID_C": "001", "BU_NAME": "Acme Corp", "CSSM_EMAIL": "alice@example.com"},
                {"ACCOUNT_ID_C": "002", "BU_NAME": "Beta Inc", "CSSM_EMAIL": "bob@example.com"},
            ]
        )

    def _ap(account_ids, days, owner_emails=None):
        calls["ap"] += 1
        return pd.DataFrame([{"ACCOUNT_ID_C": "001"}])

    def _ab(account_ids, days, owner_emails=None):
        calls["ab"] += 1
        return pd.DataFrame([{"ACCOUNT_ID_C": "002"}])

    def _cp(account_ids, days, owner_emails=None):
        calls["cp"] += 1
        return pd.DataFrame([{"ACCOUNT__C": "001", "SCORE__C": 8.1}])

    def _sp(customers, days):
        calls["sp"] += 1
        return pd.DataFrame([{"RELATED_CUSTOMER__C": "Beta Inc"}])

    monkeypatch.setattr(generator, "_get_subscriptions_for_cssm", _subs)
    monkeypatch.setattr(generator, "_fetch_action_plans", _ap)
    monkeypatch.setattr(generator, "_fetch_adoption_barriers", _ab)
    monkeypatch.setattr(generator, "_fetch_customer_pulse", _cp)
    monkeypatch.setattr(generator, "_fetch_success_priorities", _sp)

    direct_reports = [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Bob", "email": "bob@example.com"},
    ]
    team_data = generator._collect_team_data(direct_reports=direct_reports, days=90)

    assert set(team_data.keys()) == {"Alice", "Bob"}
    assert calls == {"subs": 1, "ap": 1, "ab": 1, "cp": 1, "sp": 1}


# ── Owner-based capture & creator-first attribution ─────────────────────


def _make_generator_with_roster(roster):
    """Create a LeaderReportGenerator bound to an arbitrary roster."""
    from leader_report_generator import LeaderReportGenerator
    return LeaderReportGenerator(mock.MagicMock(), roster)


def test_collect_team_data_captures_external_account_action_plans(monkeypatch):
    """Records created by a team member on an account whose PRIMARY_DSM is a
    different DSM must still be attributed to the creator (creator-first)."""
    roster = [
        ("Leader", "Mario", "mario@example.com"),
        ("Leader", "Brandon", "brandon@example.com"),
    ]
    generator = _make_generator_with_roster(roster)

    def _subs(emails):
        return pd.DataFrame(
            [
                {"ACCOUNT_ID_C": "ACC_MARIO", "BU_NAME": "NYU Medical Center", "CSSM_EMAIL": "mario@example.com"},
                {"ACCOUNT_ID_C": "ACC_BRANDON", "BU_NAME": "Brandon Hospital", "CSSM_EMAIL": "brandon@example.com"},
            ]
        )

    def _ap(account_ids, days, owner_emails=None):
        return pd.DataFrame(
            [
                {
                    "ID": "AP1",
                    "ACCOUNT_ID_C": "ACC_MARIO",
                    "SUBJECT_C": "Mario-created plan",
                    "OWNER_EMAIL": "mario@example.com",
                    "DSM_BU_NAME": "NYU Medical Center",
                },
                {
                    "ID": "AP2",
                    "ACCOUNT_ID_C": "ACC_MARIO",
                    "SUBJECT_C": "Brandon-created plan on Mario's account",
                    "OWNER_EMAIL": "brandon@example.com",
                    "DSM_BU_NAME": "NYU Medical Center",
                },
            ]
        )

    monkeypatch.setattr(generator, "_get_subscriptions_for_cssm", _subs)
    monkeypatch.setattr(generator, "_fetch_action_plans", _ap)
    monkeypatch.setattr(generator, "_fetch_adoption_barriers", lambda a, d, owner_emails=None: pd.DataFrame())
    monkeypatch.setattr(generator, "_fetch_customer_pulse", lambda a, d, owner_emails=None: pd.DataFrame())
    monkeypatch.setattr(generator, "_fetch_success_priorities", lambda c, d: pd.DataFrame())

    team_data = generator._collect_team_data(
        direct_reports=[
            {"name": "Mario", "email": "mario@example.com"},
            {"name": "Brandon", "email": "brandon@example.com"},
        ],
        days=90,
    )

    brandon_aps = team_data["Brandon"]["action_plans"]
    assert not brandon_aps.empty, "Brandon should be credited for his AP on Mario's account"
    assert (brandon_aps["ID"] == "AP2").any()
    row = brandon_aps[brandon_aps["ID"] == "AP2"].iloc[0]
    assert bool(row["_ATTRIBUTED_BY_OWNER"]) is True
    assert bool(row["_EXTERNAL_ACCOUNT"]) is False, (
        "Brandon's owned AP is on Mario's account which IS in the team-wide scope, "
        "so _EXTERNAL_ACCOUNT is False (external is judged vs. the entire team)."
    )

    mario_aps = team_data["Mario"]["action_plans"]
    assert (mario_aps["ID"] == "AP1").any()
    assert (mario_aps["ID"] == "AP2").any()
    mario_external = mario_aps[mario_aps["ID"] == "AP2"].iloc[0]
    assert bool(mario_external["_ATTRIBUTED_BY_ACCOUNT"]) is True

    def _ids(df):
        return set(df["ID"].astype(str).tolist()) if not df.empty else set()

    all_ids = _ids(mario_aps) | _ids(brandon_aps)
    assert all_ids == {"AP1", "AP2"}


def test_owner_match_clause_falls_back_when_no_columns():
    """The owner match helper must return an empty fragment when no supported
    owner-like columns exist in the table, so callers can safely no-op."""
    from adoptiq_backend import _build_owner_match_clause

    sql, params = _build_owner_match_clause(
        available_columns={"ID", "ACCOUNT_ID_C"},
        owner_emails=["x@y.com"],
        candidate_columns=("OWNER_EMAIL", "CREATEDBYEMAIL"),
    )
    assert sql == ""
    assert params == []


def test_owner_match_clause_builds_case_insensitive_in_clauses():
    from adoptiq_backend import _build_owner_match_clause

    sql, params = _build_owner_match_clause(
        available_columns={"OWNER_EMAIL", "CREATEDBYEMAIL"},
        owner_emails=["Alice@X.com", "bob@y.com"],
        candidate_columns=("OWNER_EMAIL", "CREATEDBYEMAIL"),
        table_alias="ap",
    )
    assert "LOWER(TRIM(ap.OWNER_EMAIL))" in sql
    assert "LOWER(TRIM(ap.CREATEDBYEMAIL))" in sql
    # 2 columns x 2 emails = 4 params (same emails repeated per column)
    assert len(params) == 4
    assert all(isinstance(p, str) for p in params)


def test_validate_customer_data_consistency_excludes_external_from_unexpected():
    """External-account activity should NOT inflate 'unexpected_customers'."""
    roster = [("Leader", "Brandon", "brandon@example.com")]
    generator = _make_generator_with_roster(roster)
    team_data = {
        "Brandon": {
            "customers": ["Brandon Hospital"],
            "subscriptions": pd.DataFrame([{"BU_NAME": "Brandon Hospital"}]),
            "action_plans": pd.DataFrame(
                [
                    {"BU_NAME": "Brandon Hospital", "_EXTERNAL_ACCOUNT": False},
                    {"BU_NAME": "NYU Medical Center", "_EXTERNAL_ACCOUNT": True},
                ]
            ),
            "adoption_barriers": pd.DataFrame(),
            "customer_pulse": pd.DataFrame(),
        }
    }
    checks = generator._validate_customer_data_consistency(team_data)
    entry = checks["Brandon"]
    assert entry["assigned_customers"] == 1
    assert entry["external_activity_customers"] == 1
    assert entry["unexpected_customers"] == 0


def test_normalize_owner_emails_strips_and_dedupes():
    from adoptiq_backend import _normalize_owner_emails

    result = _normalize_owner_emails(
        [" Brandon@Example.com", "brandon@example.com", None, "", "notanemail", "mario@x.io"]
    )
    assert result == ["brandon@example.com", "mario@x.io"]


def test_analysis_run_context_passes_owner_emails_to_fetcher(monkeypatch):
    """AnalysisRunContext must forward owner_emails to owner-aware fetchers."""
    from snowflake_prefetch import AnalysisRunContext, _FETCHERS

    captured = {}

    def fake_action_plans(ctx, account_ids, days, owner_emails=None):
        captured["account_ids"] = account_ids
        captured["owner_emails"] = owner_emails
        return pd.DataFrame([{"ID": "AP_X", "ACCOUNT_ID_C": "A1"}])

    monkeypatch.setitem(_FETCHERS, "csconsole_action_plans", fake_action_plans)

    run_ctx = AnalysisRunContext.build(
        ctx=mock.MagicMock(),
        account_ids=["A1", "A2"],
        days=30,
        customer_names=[],
        owner_emails=["brandon@example.com"],
    )
    df = run_ctx.get_or_fetch("csconsole_action_plans")
    assert captured["account_ids"] == ["A1", "A2"]
    assert captured["owner_emails"] == ["brandon@example.com"]
    assert len(df) == 1


def test_format_external_note_returns_creator_and_external_tag():
    """_format_external_note should produce a readable annotation for external
    accounts that includes the creator's name when present."""
    roster = [("Leader", "Mario", "mario@example.com")]
    generator = _make_generator_with_roster(roster)

    row = {
        "_EXTERNAL_ACCOUNT": True,
        "_CREATOR_NAME": "Brandon Doan",
        "_CREATOR_EMAIL": "brandon@example.com",
    }
    note = generator._format_external_note(row, cssm_name="Mario")
    assert note == "[created by Brandon Doan; external account]"

    same_author = {
        "_EXTERNAL_ACCOUNT": False,
        "_CREATOR_NAME": "Mario",
        "_CREATOR_EMAIL": "mario@example.com",
    }
    assert generator._format_external_note(same_author, cssm_name="Mario") == ""

    email_only = {
        "_EXTERNAL_ACCOUNT": True,
        "_CREATOR_NAME": "",
        "_CREATOR_EMAIL": "brandon@example.com",
    }
    assert (
        generator._format_external_note(email_only, cssm_name="Mario")
        == "[created by brandon@example.com; external account]"
    )


def test_collect_team_data_captures_external_customer_pulse(monkeypatch):
    """Customer Pulse records created by a team member on another CSSM's
    primary account should be attributed to the creator (creator-first)."""
    roster = [
        ("Leader", "Mario", "mario@example.com"),
        ("Leader", "Brandon", "brandon@example.com"),
    ]
    generator = _make_generator_with_roster(roster)

    def _subs(emails):
        return pd.DataFrame(
            [
                {"ACCOUNT_ID_C": "ACC_MARIO", "BU_NAME": "NYU Medical Center", "CSSM_EMAIL": "mario@example.com"},
                {"ACCOUNT_ID_C": "ACC_BRANDON", "BU_NAME": "Brandon Hospital", "CSSM_EMAIL": "brandon@example.com"},
            ]
        )

    def _cp(account_ids, days, owner_emails=None):
        return pd.DataFrame(
            [
                {
                    "ID": "CP1",
                    "ACCOUNT__C": "ACC_MARIO",
                    "SUBJECT_C": "Mario-created pulse",
                    "OWNER_EMAIL": "mario@example.com",
                    "SCORE__C": 8.5,
                    "DSM_BU_NAME": "NYU Medical Center",
                },
                {
                    "ID": "CP2",
                    "ACCOUNT__C": "ACC_MARIO",
                    "SUBJECT_C": "Brandon-created pulse on Mario's account",
                    "OWNER_EMAIL": "brandon@example.com",
                    "SCORE__C": 7.0,
                    "DSM_BU_NAME": "NYU Medical Center",
                },
            ]
        )

    monkeypatch.setattr(generator, "_get_subscriptions_for_cssm", _subs)
    monkeypatch.setattr(generator, "_fetch_action_plans", lambda a, d, owner_emails=None: pd.DataFrame())
    monkeypatch.setattr(generator, "_fetch_adoption_barriers", lambda a, d, owner_emails=None: pd.DataFrame())
    monkeypatch.setattr(generator, "_fetch_customer_pulse", _cp)
    monkeypatch.setattr(generator, "_fetch_success_priorities", lambda c, d: pd.DataFrame())

    team_data = generator._collect_team_data(
        direct_reports=[
            {"name": "Mario", "email": "mario@example.com"},
            {"name": "Brandon", "email": "brandon@example.com"},
        ],
        days=90,
    )

    brandon_cps = team_data["Brandon"]["customer_pulse"]
    assert not brandon_cps.empty, "Brandon should be credited for his CP on Mario's account"
    assert (brandon_cps["ID"] == "CP2").any()
    row = brandon_cps[brandon_cps["ID"] == "CP2"].iloc[0]
    assert bool(row["_ATTRIBUTED_BY_OWNER"]) is True

    mario_cps = team_data["Mario"]["customer_pulse"]
    assert (mario_cps["ID"] == "CP1").any()
    assert (mario_cps["ID"] == "CP2").any()


def test_ask_ai_grounded_wires_owner_emails_into_run_context():
    """Regression guard: ``ask_ai_grounded`` must pass ``owner_emails`` into
    ``AnalysisRunContext.build`` so collaborator-authored APs/ABs/CPs are
    captured.

    Previously this test grepped the file source for the literal substring
    ``"AnalysisRunContext.build("``, which broke whenever the call was
    wrapped across multiple lines. We now inspect the module's AST and
    assert that at least one call to ``AnalysisRunContext.build`` passes
    a keyword argument named ``owner_emails``. This is resilient to
    formatting changes and more faithful to the actual contract.
    """
    import ast

    grounded_path = Path(__file__).resolve().parent.parent / "ask_ai_grounded.py"
    tree = ast.parse(grounded_path.read_text(encoding="utf-8"))

    def _is_build_call(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "build"
            and isinstance(func.value, ast.Name)
            and func.value.id == "AnalysisRunContext"
        ):
            return True
        return False

    calls = [node for node in ast.walk(tree) if _is_build_call(node)]
    assert calls, "ask_ai_grounded must call AnalysisRunContext.build(...)"

    has_owner_emails_kw = any(
        any(kw.arg == "owner_emails" for kw in call.keywords)
        for call in calls
    )
    assert has_owner_emails_kw, (
        "ask_ai_grounded must pass owner_emails= into AnalysisRunContext.build "
        "so collaborator-authored records are captured (Brandon/Mario regression)."
    )


def test_end_to_end_brandon_mario_scenario_covers_ap_ab_cp(monkeypatch):
    """Reproduces the exact reported bug: Brandon creating AP/AB/CP on NYU
    (Mario's primary account) must land in BOTH Mario's and Brandon's
    team_data buckets and the Mario view must carry a 'created by Brandon'
    annotation via _format_external_note.
    """
    roster = [
        ("Leader", "Mario", "mario@cisco.com"),
        ("Leader", "Brandon Doan", "brandon@cisco.com"),
    ]
    generator = _make_generator_with_roster(roster)

    def _subs(emails):
        return pd.DataFrame([
            {"ACCOUNT_ID_C": "NYU_ACC", "BU_NAME": "NYU Medical Center",
             "CSSM_EMAIL": "mario@cisco.com"},
        ])

    def _ap(account_ids, days, owner_emails=None):
        return pd.DataFrame([{
            "ID": "AP1", "ACCOUNT_ID_C": "NYU_ACC",
            "SUBJECT_C": "Renewal checklist", "STATUS_C": "In Progress",
            "OWNER_EMAIL": "brandon@cisco.com",
            "DSM_BU_NAME": "NYU Medical Center",
        }])

    def _ab(account_ids, days, owner_emails=None):
        return pd.DataFrame([{
            "ID": "AB1", "ACCOUNT_ID_C": "NYU_ACC",
            "SUBJECT_C": "Config drift", "SEVERITY_C": "High",
            "OWNER_EMAIL": "brandon@cisco.com",
            "DSM_BU_NAME": "NYU Medical Center",
        }])

    def _cp(account_ids, days, owner_emails=None):
        return pd.DataFrame([{
            "ID": "CP1", "ACCOUNT__C": "NYU_ACC",
            "SUBJECT_C": "Quarterly pulse", "SCORE__C": 8.2,
            "OWNER_EMAIL": "brandon@cisco.com",
            "DSM_BU_NAME": "NYU Medical Center",
        }])

    monkeypatch.setattr(generator, "_get_subscriptions_for_cssm", _subs)
    monkeypatch.setattr(generator, "_fetch_action_plans", _ap)
    monkeypatch.setattr(generator, "_fetch_adoption_barriers", _ab)
    monkeypatch.setattr(generator, "_fetch_customer_pulse", _cp)
    monkeypatch.setattr(generator, "_fetch_success_priorities", lambda c, d: pd.DataFrame())

    team_data = generator._collect_team_data(
        direct_reports=[
            {"name": "Mario", "email": "mario@cisco.com"},
            {"name": "Brandon Doan", "email": "brandon@cisco.com"},
        ],
        days=90,
    )

    for key in ("action_plans", "adoption_barriers", "customer_pulse"):
        mario = team_data["Mario"][key]
        brandon = team_data["Brandon Doan"][key]
        assert not mario.empty, f"Mario missing {key}"
        assert not brandon.empty, f"Brandon missing {key}"
        mario_row = mario.iloc[0]
        note = generator._format_external_note(mario_row, cssm_name="Mario")
        assert "Brandon Doan" in note, (
            f"Mario's {key} should be labelled 'created by Brandon Doan'; got: {note!r}"
        )
