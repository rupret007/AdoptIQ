"""Round 82 / Build 58 (Phase A) -- secondary-CSSM attribution + DSM
column-discovery diagnostic.

Pre-R82 ``adoptiq_backend.get_subscriptions_for_team`` walked a
hardcoded 4-tuple of primary email columns (``PRIMARY_DSM_EMAIL`` ->
``CSSM_EMAIL`` -> ``ASSIGNEE_EMAIL`` -> ``OWNER_EMAIL``) and stopped at
the FIRST match.  When a roster email lived only in a "secondary"
column (Brian Frazier's team had this exact symptom), those
subscriptions never entered ``team_subs_df`` and downstream per-CSSM
slicing in the leader path returned empty frames.  The R82 fix splits
the contract:

* ``_R82_PRIMARY_DSM_EMAIL_COLUMNS`` -- trusted DSM-owner columns
  (preserves pre-R82 first-hit ordering exactly).
* ``_R82_SECONDARY_DSM_EMAIL_CANDIDATES`` -- conservative naming
  patterns for secondary owner columns.  Each candidate that is
  PRESENT in the table generates an ADDITIONAL parameterised
  ``SELECT ... WHERE <secondary_col> IN (...)`` query; rows are
  merged client-side and deduplicated on ``(SUBSCRIPTION_ID,
  ACCOUNT_ID_C, BU_NAME, CSSM_EMAIL)``.
* ``introspect_dsm_columns(ctx)`` returns the full column inventory
  (with primary / secondary / all-email-like classification) so the
  ``GET /api/diag/dsm-columns`` admin endpoint can surface the actual
  column names without external Snowflake access.
* ``_r82_persist_team_subs_diag(status, df)`` in ``app_simple`` reads
  ``df.attrs['_r82_team_subs_diag']`` and persists the rollup into
  ``analysis_status[<id>]['team_subs_diag']`` so the operator can see
  primary vs secondary row counts per analysis.

These tests pin both the source shape AND the runtime behavior via
synthetic Snowflake cursor mocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Phase A2: candidate tuple SSoT shape
# ---------------------------------------------------------------------------


def test_r82_primary_dsm_email_columns_preserve_pre_r82_order():
    """The primary tuple MUST keep ``PRIMARY_DSM_EMAIL`` first so the
    'I expect to see Alice's primary subscriptions' contract never
    regresses for any team that already worked pre-R82."""
    from adoptiq_backend import _R82_PRIMARY_DSM_EMAIL_COLUMNS

    assert isinstance(_R82_PRIMARY_DSM_EMAIL_COLUMNS, tuple)
    assert _R82_PRIMARY_DSM_EMAIL_COLUMNS == (
        "PRIMARY_DSM_EMAIL",
        "CSSM_EMAIL",
        "ASSIGNEE_EMAIL",
        "OWNER_EMAIL",
    )


def test_r82_secondary_candidates_cover_common_naming_patterns():
    """The secondary candidate tuple MUST include the common naming
    patterns we anticipate.  Operators can extend via a follow-on
    commit when the diagnostic surfaces a new column name."""
    from adoptiq_backend import _R82_SECONDARY_DSM_EMAIL_CANDIDATES

    assert isinstance(_R82_SECONDARY_DSM_EMAIL_CANDIDATES, tuple)
    expected_present = {
        "SECONDARY_DSM_EMAIL",
        "SECONDARY_CSSM_EMAIL",
        "BACKUP_DSM_EMAIL",
        "DELEGATE_DSM_EMAIL",
        "OWNER_EMAIL_2",
    }
    assert expected_present.issubset(set(_R82_SECONDARY_DSM_EMAIL_CANDIDATES))


def test_r82_secondary_candidates_disjoint_from_primary():
    """Secondary candidates MUST NOT overlap with primary columns -- the
    UNION query would double-count subscriptions otherwise."""
    from adoptiq_backend import (
        _R82_PRIMARY_DSM_EMAIL_COLUMNS,
        _R82_SECONDARY_DSM_EMAIL_CANDIDATES,
    )

    primary = set(_R82_PRIMARY_DSM_EMAIL_COLUMNS)
    secondary = set(_R82_SECONDARY_DSM_EMAIL_CANDIDATES)
    assert primary.isdisjoint(secondary), (
        f"Primary and secondary tuples overlap: {primary & secondary}"
    )


# ---------------------------------------------------------------------------
# Phase A1: introspect_dsm_columns helper shape
# ---------------------------------------------------------------------------


def test_introspect_dsm_columns_returns_canonical_schema_on_none_ctx():
    """``ctx=None`` MUST return a structured payload with
    ``error_kind='snowflake_context_unavailable'`` -- never raise."""
    from adoptiq_backend import introspect_dsm_columns

    result = introspect_dsm_columns(None)
    assert isinstance(result, dict)
    assert result.get("ok") is False
    assert result.get("error_kind") == "snowflake_context_unavailable"
    assert result.get("table") == "CX_DB.CX_SWSSBST_BR.dsm_assignment_data"
    # Required keys present even on failure path.
    assert "columns" in result
    assert "primary_email_columns_present" in result
    assert "secondary_email_candidates_present" in result
    assert "all_email_like_columns" in result
    assert "introspected_at" in result


def test_introspect_dsm_columns_classifies_primary_secondary_present():
    """Mocked column set with a mix of primary, secondary, and unrelated
    columns MUST classify them correctly."""
    import adoptiq_backend
    from adoptiq_backend import introspect_dsm_columns

    fake_columns = {
        "PRIMARY_DSM_EMAIL",
        "BACKUP_DSM_EMAIL",
        "OWNER_EMAIL_2",
        "SUBSCRIPTION_ID",
        "BU_NAME",
        "TECHNOLOGY_C",
        "ACCOUNT_ID_C",
    }
    fake_ctx = MagicMock()

    with patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        result = introspect_dsm_columns(fake_ctx)

    assert result["ok"] is True
    assert result["error_kind"] is None
    assert "PRIMARY_DSM_EMAIL" in result["primary_email_columns_present"]
    assert set(result["secondary_email_candidates_present"]) == {
        "BACKUP_DSM_EMAIL",
        "OWNER_EMAIL_2",
    }
    # All ``*_EMAIL`` / ``*_EMAIL_2`` columns surface, sorted.
    assert "PRIMARY_DSM_EMAIL" in result["all_email_like_columns"]
    assert "BACKUP_DSM_EMAIL" in result["all_email_like_columns"]
    assert "OWNER_EMAIL_2" in result["all_email_like_columns"]
    # Non-email columns must NOT appear in any classification slot.
    assert "SUBSCRIPTION_ID" not in result["all_email_like_columns"]
    assert "TECHNOLOGY_C" not in result["primary_email_columns_present"]


def test_introspect_dsm_columns_handles_empty_table_gracefully():
    """Empty column set MUST return ``ok=False`` with
    ``error_kind='dsm_table_introspection_empty'`` instead of
    pretending the table is fine."""
    import adoptiq_backend
    from adoptiq_backend import introspect_dsm_columns

    fake_ctx = MagicMock()
    with patch.object(adoptiq_backend, "_get_table_columns", return_value=set()):
        result = introspect_dsm_columns(fake_ctx)

    assert result["ok"] is False
    assert result["error_kind"] == "dsm_table_introspection_empty"


# ---------------------------------------------------------------------------
# Phase A3: get_subscriptions_for_team UNION behavior
# ---------------------------------------------------------------------------


def _build_mock_cursor_for_columns(rows_per_query: dict):
    """Build a MagicMock cursor that returns different rows depending on
    which email column is in the SQL.  ``rows_per_query`` maps an
    email column name to the rows it should return (a list of tuples
    matching ``(SUBSCRIPTION_ID, ACCOUNT_ID_C, BU_NAME, CSSM_EMAIL)``).
    """
    cursor = MagicMock()
    captured_sqls = []

    descr = (
        ("SUBSCRIPTION_ID",),
        ("ACCOUNT_ID_C",),
        ("BU_NAME",),
        ("CSSM_EMAIL",),
    )

    def _execute(sql, params):
        captured_sqls.append((sql, list(params)))
        for col, rows in rows_per_query.items():
            if f" {col} IN " in sql:
                cursor._next_rows = rows
                cursor._next_descr = descr
                return cursor
        cursor._next_rows = []
        cursor._next_descr = descr
        return cursor

    def _fetchall():
        return getattr(cursor, "_next_rows", [])

    cursor.execute = _execute
    cursor.fetchall = _fetchall
    cursor.description = descr
    cursor.captured_sqls = captured_sqls

    # Make ``cursor.description`` reflect the most-recent execute.
    type(cursor).description = property(  # type: ignore
        lambda self: getattr(self, "_next_descr", descr)
    )
    return cursor


def test_get_subscriptions_for_team_unions_primary_and_secondary_columns():
    """When BOTH primary and secondary columns exist AND each returns
    distinct rows, the merged DataFrame MUST contain rows from BOTH
    paths."""
    import adoptiq_backend
    from adoptiq_backend import get_subscriptions_for_team

    rows_per_query = {
        "PRIMARY_DSM_EMAIL": [
            ("SUB001", "ACC001", "Acme Corp", "alice@example.com"),
            ("SUB002", "ACC002", "Beta Inc", "alice@example.com"),
        ],
        "BACKUP_DSM_EMAIL": [
            ("SUB003", "ACC003", "Gamma LLC", "bob@example.com"),
        ],
    }
    fake_ctx = MagicMock()
    fake_cursor = _build_mock_cursor_for_columns(rows_per_query)
    fake_ctx.cursor.return_value = fake_cursor

    fake_columns = {
        "PRIMARY_DSM_EMAIL",
        "BACKUP_DSM_EMAIL",
        "SUBSCRIPTION_ID",
        "ACCOUNT_ID_C",
        "BU_NAME",
    }

    with patch.object(adoptiq_backend, "is_table_blocked", return_value=False), \
         patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        df = get_subscriptions_for_team(
            fake_ctx,
            ["alice@example.com", "bob@example.com"],
        )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3, f"Expected 3 merged rows, got {len(df)}: {df.to_dict()}"
    assert set(df["BU_NAME"]) == {"Acme Corp", "Beta Inc", "Gamma LLC"}


def test_get_subscriptions_for_team_dedupes_when_primary_and_secondary_overlap():
    """When the SAME row is returned by both primary and secondary
    queries (e.g. denormalised data), dedup on
    ``(SUBSCRIPTION_ID, ACCOUNT_ID_C, BU_NAME, CSSM_EMAIL)`` MUST
    drop the duplicate."""
    import adoptiq_backend
    from adoptiq_backend import get_subscriptions_for_team

    duplicate_row = ("SUB001", "ACC001", "Acme Corp", "alice@example.com")
    rows_per_query = {
        "PRIMARY_DSM_EMAIL": [duplicate_row],
        "SECONDARY_DSM_EMAIL": [duplicate_row],
    }
    fake_ctx = MagicMock()
    fake_cursor = _build_mock_cursor_for_columns(rows_per_query)
    fake_ctx.cursor.return_value = fake_cursor

    fake_columns = {"PRIMARY_DSM_EMAIL", "SECONDARY_DSM_EMAIL"}

    with patch.object(adoptiq_backend, "is_table_blocked", return_value=False), \
         patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        df = get_subscriptions_for_team(fake_ctx, ["alice@example.com"])

    assert len(df) == 1, "Duplicate row MUST be deduped post-merge"
    diag = df.attrs.get("_r82_team_subs_diag")
    assert isinstance(diag, dict)
    assert diag["duplicate_rows_dropped"] == 1


def test_get_subscriptions_for_team_attaches_diag_attrs_with_primary_only():
    """Even when no secondary columns exist, the diag MUST be attached
    so analysis_status persistence still has a payload to write."""
    import adoptiq_backend
    from adoptiq_backend import get_subscriptions_for_team

    rows_per_query = {
        "PRIMARY_DSM_EMAIL": [
            ("SUB001", "ACC001", "Acme Corp", "alice@example.com"),
        ],
    }
    fake_ctx = MagicMock()
    fake_cursor = _build_mock_cursor_for_columns(rows_per_query)
    fake_ctx.cursor.return_value = fake_cursor

    fake_columns = {"PRIMARY_DSM_EMAIL"}  # no secondary
    with patch.object(adoptiq_backend, "is_table_blocked", return_value=False), \
         patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        df = get_subscriptions_for_team(fake_ctx, ["alice@example.com"])

    diag = df.attrs.get("_r82_team_subs_diag")
    assert isinstance(diag, dict)
    assert diag["primary_email_column_used"] == "PRIMARY_DSM_EMAIL"
    assert diag["secondary_email_columns_used"] == []
    assert diag["primary_rows"] == 1
    assert diag["secondary_rows"] == 0
    assert diag["merged_rows"] == 1
    assert diag["error_kind"] is None


def test_get_subscriptions_for_team_returns_empty_when_no_email_columns_exist():
    """If neither primary nor any secondary column exists in the table,
    the function MUST return an empty DataFrame with the
    ``no_email_columns_found`` diag (not raise)."""
    import adoptiq_backend
    from adoptiq_backend import get_subscriptions_for_team

    fake_ctx = MagicMock()
    fake_columns = {"SUBSCRIPTION_ID", "BU_NAME"}  # no email cols at all

    with patch.object(adoptiq_backend, "is_table_blocked", return_value=False), \
         patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        df = get_subscriptions_for_team(fake_ctx, ["alice@example.com"])

    assert df.empty
    diag = df.attrs.get("_r82_team_subs_diag")
    assert isinstance(diag, dict)
    assert diag["error_kind"] == "no_email_columns_found"


def test_get_subscriptions_for_team_with_no_primary_but_with_secondary():
    """Defensive case: primary list yields no hits in the table, but a
    secondary column DOES exist.  The function MUST still return rows
    via the secondary path -- THIS is the Brian-team failure mode the
    fix targets."""
    import adoptiq_backend
    from adoptiq_backend import get_subscriptions_for_team

    rows_per_query = {
        "SECONDARY_DSM_EMAIL": [
            ("SUB099", "ACC099", "Brian's missing customer", "alice@example.com"),
        ],
    }
    fake_ctx = MagicMock()
    fake_cursor = _build_mock_cursor_for_columns(rows_per_query)
    fake_ctx.cursor.return_value = fake_cursor

    # Only the secondary column exists -- this is the defining
    # condition of the bug (no primary, only secondary).
    fake_columns = {"SECONDARY_DSM_EMAIL"}

    with patch.object(adoptiq_backend, "is_table_blocked", return_value=False), \
         patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        df = get_subscriptions_for_team(fake_ctx, ["alice@example.com"])

    assert len(df) == 1, (
        "When only a secondary column exists, the function MUST still "
        "return matching rows -- this is the Brian-team bug"
    )
    assert df.iloc[0]["BU_NAME"] == "Brian's missing customer"
    diag = df.attrs.get("_r82_team_subs_diag")
    assert diag["primary_email_column_used"] is None
    assert diag["secondary_email_columns_used"] == ["SECONDARY_DSM_EMAIL"]
    assert diag["primary_rows"] == 0
    assert diag["secondary_rows"] == 1


def test_get_subscriptions_for_team_parameterized_sql_no_injection_surface():
    """The UNION refactor MUST preserve the parameterized SQL contract --
    each chunk's IN clause uses ``%s`` placeholders bound to the
    cursor.execute call, NEVER string-concatenated."""
    import adoptiq_backend
    from adoptiq_backend import get_subscriptions_for_team

    rows_per_query = {
        "PRIMARY_DSM_EMAIL": [
            ("SUB001", "ACC001", "Acme Corp", "alice'; DROP TABLE--"),
        ],
    }
    fake_ctx = MagicMock()
    fake_cursor = _build_mock_cursor_for_columns(rows_per_query)
    fake_ctx.cursor.return_value = fake_cursor

    fake_columns = {"PRIMARY_DSM_EMAIL"}
    malicious_email = "alice'; DROP TABLE dsm_assignment_data; --"

    with patch.object(adoptiq_backend, "is_table_blocked", return_value=False), \
         patch.object(adoptiq_backend, "_get_table_columns", return_value=fake_columns):
        get_subscriptions_for_team(fake_ctx, [malicious_email])

    # The malicious string MUST appear in the BOUND PARAMETERS, never
    # in the SQL string itself.
    for sql, params in fake_cursor.captured_sqls:
        assert "DROP TABLE" not in sql, "Email value leaked into SQL string"
        assert malicious_email in params, "Email value not properly bound as parameter"
        assert "%s" in sql, "Parameterised placeholders missing"


# ---------------------------------------------------------------------------
# Phase A1: /api/diag/dsm-columns endpoint
# ---------------------------------------------------------------------------


def test_api_diag_dsm_columns_route_registered(app):
    """The new endpoint MUST be registered on the main Flask app."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/diag/dsm-columns" in rules


def test_api_diag_dsm_columns_returns_payload_via_introspect(client):
    """End-to-end: endpoint hits the ``introspect_dsm_columns`` helper
    via a stubbed ``_connect_with_keeper`` and returns its payload."""
    fake_ctx = MagicMock()
    fake_payload = {
        "ok": True,
        "table": "CX_DB.CX_SWSSBST_BR.dsm_assignment_data",
        "columns": ["PRIMARY_DSM_EMAIL", "SECONDARY_DSM_EMAIL", "SUBSCRIPTION_ID"],
        "primary_email_columns_present": ["PRIMARY_DSM_EMAIL"],
        "secondary_email_candidates_present": ["SECONDARY_DSM_EMAIL"],
        "all_email_like_columns": ["PRIMARY_DSM_EMAIL", "SECONDARY_DSM_EMAIL"],
        "introspected_at": "2026-05-04T00:00:00+00:00",
        "error_kind": None,
    }

    with patch("adoptiq_backend._connect_with_keeper", return_value=fake_ctx), \
         patch("adoptiq_backend.introspect_dsm_columns", return_value=fake_payload):
        resp = client.get("/api/diag/dsm-columns")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["table"] == "CX_DB.CX_SWSSBST_BR.dsm_assignment_data"
    assert body["primary_email_columns_present"] == ["PRIMARY_DSM_EMAIL"]
    assert body["secondary_email_candidates_present"] == ["SECONDARY_DSM_EMAIL"]


def test_api_diag_dsm_columns_returns_503_on_introspect_failure(client):
    """Snowflake-unreachable / table-introspection-empty MUST surface as
    HTTP 503 with structured ``error_kind`` -- never bubble a stack."""
    fake_ctx = MagicMock()
    fake_payload = {
        "ok": False,
        "table": "CX_DB.CX_SWSSBST_BR.dsm_assignment_data",
        "columns": [],
        "primary_email_columns_present": [],
        "secondary_email_candidates_present": [],
        "all_email_like_columns": [],
        "introspected_at": "2026-05-04T00:00:00+00:00",
        "error_kind": "dsm_table_introspection_empty",
    }

    with patch("adoptiq_backend._connect_with_keeper", return_value=fake_ctx), \
         patch("adoptiq_backend.introspect_dsm_columns", return_value=fake_payload):
        resp = client.get("/api/diag/dsm-columns")

    assert resp.status_code == 503
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error_kind"] == "dsm_table_introspection_empty"


def test_api_diag_dsm_columns_returns_500_on_harness_failure(client):
    """If the harness itself blows up (Keeper auth fails, etc.) the
    endpoint MUST return HTTP 500 with a generic error message --
    never leak the exception text."""
    with patch(
        "adoptiq_backend._connect_with_keeper",
        side_effect=RuntimeError("Failed to connect to Snowflake"),
    ):
        resp = client.get("/api/diag/dsm-columns")

    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error_kind"] == "diag.dsm_columns.unhandled"
    # Generic message; raw exception text MUST NOT be echoed.
    assert "DSM column-discovery diagnostic failed" in body["error"]


# ---------------------------------------------------------------------------
# Phase A4: _r82_persist_team_subs_diag helper + analysis_status surface
# ---------------------------------------------------------------------------


def test_r82_persist_team_subs_diag_writes_to_status_dict():
    """The helper MUST persist ``df.attrs['_r82_team_subs_diag']`` into
    ``status['team_subs_diag']`` when both are present."""
    from app_simple import _r82_persist_team_subs_diag

    df = pd.DataFrame({"BU_NAME": ["Acme"]})
    df.attrs["_r82_team_subs_diag"] = {
        "primary_email_column_used": "PRIMARY_DSM_EMAIL",
        "secondary_email_columns_used": ["SECONDARY_DSM_EMAIL"],
        "primary_rows": 5,
        "secondary_rows": 3,
        "merged_rows": 8,
    }
    status: dict = {}

    _r82_persist_team_subs_diag(status, df)

    assert "team_subs_diag" in status
    assert status["team_subs_diag"]["primary_rows"] == 5
    assert status["team_subs_diag"]["secondary_rows"] == 3


def test_r82_persist_team_subs_diag_handles_none_status_silently():
    """``status=None`` MUST be a no-op (Ask AI portfolio path doesn't
    drive analysis_status); MUST NOT raise."""
    from app_simple import _r82_persist_team_subs_diag

    df = pd.DataFrame({"BU_NAME": ["Acme"]})
    df.attrs["_r82_team_subs_diag"] = {"primary_rows": 1}

    # Should NOT raise.
    _r82_persist_team_subs_diag(None, df)


def test_r82_persist_team_subs_diag_handles_missing_attrs_silently():
    """If the DataFrame has no ``_r82_team_subs_diag`` attr (e.g. older
    code path that bypassed the new helpers), the function MUST be a
    no-op rather than failing."""
    from app_simple import _r82_persist_team_subs_diag

    df = pd.DataFrame({"BU_NAME": ["Acme"]})  # no attrs set
    status: dict = {}

    _r82_persist_team_subs_diag(status, df)

    assert "team_subs_diag" not in status


# ---------------------------------------------------------------------------
# Source-shape pins: round markers + call-site invocation
# ---------------------------------------------------------------------------


def test_app_simple_calls_persist_diag_from_three_report_paths():
    """All three primary report writers MUST call
    ``_r82_persist_team_subs_diag`` after ``get_subscriptions_for_team``
    so an operator inspecting any of the three flows sees the same
    diag rollup."""
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The helper MUST be defined exactly once.
    assert src.count("def _r82_persist_team_subs_diag(") == 1
    # And it MUST be invoked at three (or more) sites -- the customer
    # renewal, comprehensive, and leader paths.
    invocation_count = src.count("_r82_persist_team_subs_diag(")
    # 1 definition + at least 3 invocations = >= 4 occurrences.
    assert invocation_count >= 4, (
        f"Expected >=3 invocations of _r82_persist_team_subs_diag (1 def "
        f"+ 3 call sites), got {invocation_count} total occurrences."
    )


def test_round82_source_markers_present_in_adoptiq_backend():
    """Round 82 source markers MUST be present in adoptiq_backend.py
    near the candidate tuple and the refactored function so future
    git-diff greps surface the per-file footprint."""
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "_R82_PRIMARY_DSM_EMAIL_COLUMNS" in src
    assert "_R82_SECONDARY_DSM_EMAIL_CANDIDATES" in src
    assert "Round 82 / Phase A" in src
    assert "introspect_dsm_columns" in src


def test_round82_source_markers_present_in_app_simple():
    """Round 82 source markers MUST be present in app_simple.py for
    the diag endpoint and the persistence helper."""
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "/api/diag/dsm-columns" in src
    assert "Round 82 / Phase A1" in src
    assert "Round 82 / Phase A4" in src
    assert "_r82_persist_team_subs_diag" in src
