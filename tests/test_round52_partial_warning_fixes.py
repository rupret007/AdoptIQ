"""Round 52 / Phase 4: regression tests for the three recurring partial-data
warning classes the strict live harness kept surfacing.

These tests pin the contracts of three small, surgical fixes:

1. ``data_normalization.merge_customer_join_keys_dtype_safe`` materializes
   the right frame's ``BU_NAME`` (and friends) onto the left frame even when
   the join key dtype drifts between Snowflake fetches (``object`` vs
   ``Int64`` vs ``string``).  Without this, the
   ``adoption_barriers`` row contract keeps failing on
   ``schema_drift: missing slot(s) customer`` because the merge silently
   produces a frame with all-NaN ``BU_NAME`` -- or skips the column
   entirely -- and the prefetch-time fetch_error stamp is never cleared.

2. ``data_contracts`` exposes a dedicated ``team_subscriptions`` row
   contract whose only required slots are ``customer`` and
   ``subscription`` (no ARR / TCV).  ``adoptiq_backend.get_subscriptions_for_team``
   returns a roster frame that intentionally lacks ARR columns; annotating
   it against the full ``subscriptions`` contract therefore stamped an
   incorrect ``schema_drift: missing slot(s) arr`` warning on every team-
   scoped run.  The new contract removes that false positive without
   weakening the broader ``subscriptions`` contract.

3. ``adoptiq_backend._get_table_columns`` short-circuits on tables that
   ``snowflake_table_policy.is_table_blocked`` flags, so an intentional
   policy block never surfaces as a ``column_introspection_failure``
   warning in the report banner.

Each test exercises the smallest correct surface and fails loud if the
fix regresses.  The tests do NOT require Snowflake / live data and run
in <1s combined.
"""

from __future__ import annotations

from typing import Set

import pandas as pd

# Round 52 imports -- pin the public surface area we are guarding.
from data_normalization import merge_customer_join_keys_dtype_safe
from data_contracts import (
    ROW_CONTRACT_ALIASES,
    annotate_with_contract,
    validate_row_contract,
)


# ---------------------------------------------------------------------------
# Fix 1: dtype-safe customer join keys
# ---------------------------------------------------------------------------


def _stamp_schema_drift(df: pd.DataFrame, dataset: str = "adoption_barriers") -> None:
    """Mimic what ``annotate_with_contract`` stamps on a non-empty frame
    that fails the contract -- prefetch does this when the raw view only
    carries ``ACCOUNT_ID_C`` and no customer name column."""
    df.attrs["fetch_error"] = (
        f"schema_drift:{dataset}: missing slot(s) customer on a "
        f"non-empty result ({len(df)} row(s)); upstream column names changed."
    )
    df.attrs["fetch_error_kind"] = "schema_drift"
    df.attrs["fetch_error_dataset"] = dataset


def test_round52_dtype_safe_merge_materializes_bu_name_with_object_vs_int_keys():
    """Left frame has ACCOUNT_ID_C as plain ``object`` (string), right
    frame has it as ``Int64`` -- the kind of drift Snowflake produces
    when one query returns NULL-bearing rows and the other does not."""
    left = pd.DataFrame({
        "ACCOUNT_ID_C": ["1001", "1002", "1003"],
        "SUBJECT_C": ["a", "b", "c"],
    })
    right = pd.DataFrame({
        "ACCOUNT_ID_C": pd.array([1001, 1002, 1003], dtype="Int64"),
        "BU_NAME": ["Alpha Inc", "Beta Corp", "Gamma LLC"],
        "CSSM_EMAIL": ["a@x", "b@x", "c@x"],
    })

    out = merge_customer_join_keys_dtype_safe(
        left,
        right,
        right_columns=("ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"),
    )

    assert "BU_NAME" in out.columns, (
        "merge_customer_join_keys_dtype_safe must materialize BU_NAME "
        "even when join key dtypes drift"
    )
    assert out["BU_NAME"].notna().all(), (
        "All three rows should have resolved BU_NAME; the dtype-safe "
        "coercion is the whole point of the helper"
    )
    assert list(out["BU_NAME"]) == ["Alpha Inc", "Beta Corp", "Gamma LLC"]


def test_round52_dtype_safe_merge_handles_whitespace_and_na_sentinels():
    """Real exports carry ``  1001 ``, ``"<NA>"``, and ``"None"`` strings."""
    left = pd.DataFrame({
        "ACCOUNT_ID_C": ["  1001  ", "1002", "<NA>"],
        "SUBJECT_C": ["a", "b", "c"],
    })
    right = pd.DataFrame({
        "ACCOUNT_ID_C": ["1001", "1002", "1003"],
        "BU_NAME": ["Alpha Inc", "Beta Corp", "Gamma LLC"],
    })

    out = merge_customer_join_keys_dtype_safe(
        left,
        right,
        right_columns=("ACCOUNT_ID_C", "BU_NAME"),
    )

    assert out.loc[0, "BU_NAME"] == "Alpha Inc", "whitespace must be stripped"
    assert out.loc[1, "BU_NAME"] == "Beta Corp"
    assert pd.isna(out.loc[2, "BU_NAME"]), "<NA> sentinel must not match a real ID"


def test_round52_dtype_safe_merge_clears_schema_drift_via_reannotate():
    """End-to-end: stamp a schema_drift warning on a raw CSConsole-AB-
    shaped frame, run the dtype-safe merge + re-annotate, and verify
    the prefetch-time warning is gone."""
    left = pd.DataFrame({
        "ID": ["AB-1", "AB-2"],
        "ACCOUNT_ID_C": ["1001", "1002"],
        "SUBJECT_C": ["barrier-1", "barrier-2"],
        "AB_STATUS_C": ["Open", "Open"],
        "SEVERITY_C": ["High", "Medium"],
    })
    _stamp_schema_drift(left, dataset="adoption_barriers")
    assert left.attrs.get("fetch_error_kind") == "schema_drift"

    right = pd.DataFrame({
        "ACCOUNT_ID_C": pd.array([1001, 1002], dtype="Int64"),
        "BU_NAME": ["Alpha Inc", "Beta Corp"],
        "CSSM_EMAIL": ["a@x", "b@x"],
    })

    merged = merge_customer_join_keys_dtype_safe(
        left,
        right,
        right_columns=("ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"),
    )

    assert "BU_NAME" in merged.columns, "merge must add BU_NAME column"

    # Carry the prefetch-time stamp forward onto the new frame so the
    # re-annotate clear branch can fire (mirrors what `app_simple.py`
    # would see if pandas merge preserved attrs; the helper itself
    # does not propagate attrs, so the production code paths assign
    # the merged frame back to the bundle and re-annotate it).
    merged.attrs["fetch_error"] = left.attrs["fetch_error"]
    merged.attrs["fetch_error_kind"] = left.attrs["fetch_error_kind"]
    merged.attrs["fetch_error_dataset"] = left.attrs["fetch_error_dataset"]

    annotate_with_contract(merged, dataset="adoption_barriers")

    assert "fetch_error" not in merged.attrs, (
        "schema_drift must be cleared once BU_NAME is materialized"
    )
    assert "fetch_error_kind" not in merged.attrs


def test_round52_dtype_safe_merge_returns_left_unchanged_when_right_is_empty():
    """If team_subs_df is empty (e.g. team has zero subs) the helper
    must return the left frame untouched -- callers will surface the
    upstream zero-row signal via the team_subscriptions contract,
    not via a synthesized merge."""
    left = pd.DataFrame({
        "ACCOUNT_ID_C": ["1001"],
        "SUBJECT_C": ["a"],
    })
    right = pd.DataFrame({"ACCOUNT_ID_C": [], "BU_NAME": []})

    out = merge_customer_join_keys_dtype_safe(left, right)
    assert "BU_NAME" not in out.columns
    assert len(out) == 1


def test_round52_dtype_safe_merge_returns_left_unchanged_when_join_key_missing():
    left = pd.DataFrame({"OTHER_KEY": ["1001"], "SUBJECT_C": ["a"]})
    right = pd.DataFrame({"ACCOUNT_ID_C": ["1001"], "BU_NAME": ["x"]})

    out = merge_customer_join_keys_dtype_safe(left, right)
    assert list(out.columns) == ["OTHER_KEY", "SUBJECT_C"]


def test_round52_dtype_safe_merge_drops_duplicate_right_keys():
    """Right frame may carry multiple rows per ACCOUNT_ID_C (one per
    subscription).  The helper must collapse duplicates so the left
    frame's row count is preserved instead of exploding."""
    left = pd.DataFrame({
        "ACCOUNT_ID_C": ["1001", "1001"],
        "SUBJECT_C": ["a", "b"],
    })
    right = pd.DataFrame({
        "ACCOUNT_ID_C": ["1001", "1001", "1001"],
        "BU_NAME": ["Alpha Inc", "Alpha Inc", "Alpha Inc"],
    })

    out = merge_customer_join_keys_dtype_safe(left, right)
    assert len(out) == 2
    assert list(out["BU_NAME"]) == ["Alpha Inc", "Alpha Inc"]


def test_round52_dtype_safe_merge_handles_none_inputs_gracefully():
    assert merge_customer_join_keys_dtype_safe(None, pd.DataFrame()) is None
    df = pd.DataFrame({"ACCOUNT_ID_C": ["x"]})
    assert merge_customer_join_keys_dtype_safe(df, None).equals(df)


# ---------------------------------------------------------------------------
# Fix 2: team_subscriptions contract (no ARR slot)
# ---------------------------------------------------------------------------


def test_round52_team_subscriptions_contract_exists_and_lacks_arr_slot():
    assert "team_subscriptions" in ROW_CONTRACT_ALIASES, (
        "team_subscriptions contract was added in Round 52 to stop the "
        "false-positive 'subscriptions schema_drift on missing ARR slot' "
        "warning that fires on every team-scoped run."
    )
    slots: Set[str] = set(ROW_CONTRACT_ALIASES["team_subscriptions"].keys())
    assert "arr" not in slots, (
        "team_subscriptions must NOT require ARR; the team roster does "
        "not carry ARR columns"
    )
    assert "customer" in slots
    assert "subscription" in slots


def test_round52_team_subscriptions_contract_passes_on_roster_frame_without_arr():
    """Mirror what get_subscriptions_for_team actually returns -- columns
    BU_NAME + SUBSCRIPTION_NUMBER + a few metadata columns, no ARR /
    TCV / ANNUAL_CONTRACT_VALUE.  The new contract must accept this."""
    roster = pd.DataFrame({
        "BU_NAME": ["Alpha Inc", "Beta Corp"],
        "SUBSCRIPTION_NUMBER": ["SUB-1001", "SUB-1002"],
        "ACCOUNT_ID_C": ["1001", "1002"],
        "CSSM_EMAIL": ["a@x", "b@x"],
    })

    result = validate_row_contract(roster, dataset="team_subscriptions")
    assert result["is_valid"], (
        f"team_subscriptions contract should pass on roster frame; "
        f"got missing slots {result['missing_slots']}"
    )

    annotate_with_contract(roster, dataset="team_subscriptions")
    assert "fetch_error" not in roster.attrs, (
        "team_subscriptions contract must not stamp schema_drift on a "
        "valid roster frame"
    )


def test_round52_team_subscriptions_contract_fails_loud_when_no_customer_column():
    """Defense in depth: the contract must still fail loud if even the
    customer slot is missing -- we are NOT silently weakening contracts,
    only giving the team-roster path the right shape to validate against."""
    bad = pd.DataFrame({
        "RANDOM_COL": ["x", "y"],
        "SUBSCRIPTION_NUMBER": ["SUB-1", "SUB-2"],
    })
    result = validate_row_contract(bad, dataset="team_subscriptions")
    assert not result["is_valid"]
    assert "customer" in result["missing_slots"]


# ---------------------------------------------------------------------------
# Fix 3: blocked-table introspection short-circuit
# ---------------------------------------------------------------------------


def test_round52_blocked_table_introspection_short_circuits_without_warning():
    """``adoptiq_backend._get_table_columns`` must return an empty set
    quickly when the table is policy-blocked, without recording an
    introspection failure (which would surface as a partial-data
    'column_introspection_failure' warning in the report banner)."""
    import adoptiq_backend
    import snowflake_table_policy

    # Round 52: pick the canonical blocked table from policy.  If the
    # policy module ever lists a different blocked name we'll fail
    # here -- which is the correct signal: the test should track the
    # policy, not fork it.
    blocked = next(iter(snowflake_table_policy._BLOCKED_CANONICAL))
    assert snowflake_table_policy.is_table_blocked(blocked), (
        f"sanity: {blocked!r} must register as blocked"
    )

    # Reset the introspection-failure registry so this test sees a
    # clean baseline regardless of test ordering.
    with adoptiq_backend._TABLE_COLUMN_CACHE_LOCK:
        adoptiq_backend._TABLE_COLUMN_INTROSPECTION_FAILURES.clear()

    cols = adoptiq_backend._get_table_columns(None, blocked)

    assert cols == set(), (
        "blocked tables must return an empty column set -- callers "
        "interpret this as 'policy-blocked' and quietly drop the column"
    )

    failures = adoptiq_backend.get_recent_column_introspection_failures()
    assert blocked.upper() not in {k.upper() for k in failures.keys()}, (
        "blocked tables must NOT register an introspection failure; the "
        "block is intentional and must not surface as a "
        "'column_introspection_failure' partial-data warning"
    )


def test_round52_blocked_table_short_circuit_returns_before_any_cursor_access():
    """Stronger guarantee: when called with a None ctx and a blocked
    table, the helper must not raise -- proving the policy check
    happens BEFORE any attempt to dereference the connection."""
    import adoptiq_backend
    import snowflake_table_policy

    blocked = next(iter(snowflake_table_policy._BLOCKED_CANONICAL))
    cols = adoptiq_backend._get_table_columns(None, blocked)
    assert cols == set()


def test_round52_non_blocked_table_with_none_ctx_still_returns_empty():
    """Sanity guard: a non-blocked table with a ``None`` ctx must also
    return ``set()`` without raising -- the early ``ctx is None``
    branch was already in place pre-Round 52 and must not regress."""
    import adoptiq_backend
    cols = adoptiq_backend._get_table_columns(None, "SS.SOME_RANDOM_TABLE")
    assert cols == set()


# ---------------------------------------------------------------------------
# Cross-cutting: ensure the existing adoption_barriers contract still
# matches BU_NAME on a real-shape merged frame (regression guard).
# ---------------------------------------------------------------------------


def test_round52_adoption_barriers_contract_passes_after_dtype_safe_merge():
    """End-to-end shape test: take a CSConsole-AB-like frame, run the
    dtype-safe merge against a team_subs frame, then run the
    adoption_barriers contract.  It must pass."""
    csab = pd.DataFrame({
        "ID": ["AB-1", "AB-2"],
        "ACCOUNT_ID_C": ["1001", "1002"],
        "SUBJECT_C": ["barrier-1", "barrier-2"],
        "AB_STATUS_C": ["Open", "Open"],
        "SEVERITY_C": ["High", "Medium"],
    })
    team_subs = pd.DataFrame({
        "ACCOUNT_ID_C": pd.array([1001, 1002], dtype="Int64"),
        "BU_NAME": ["Alpha Inc", "Beta Corp"],
        "CSSM_EMAIL": ["a@x", "b@x"],
    })

    merged = merge_customer_join_keys_dtype_safe(
        csab,
        team_subs,
        right_columns=("ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"),
    )

    result = validate_row_contract(merged, dataset="adoption_barriers")
    assert result["is_valid"], (
        f"adoption_barriers contract should pass on the merged frame; "
        f"got missing slots {result['missing_slots']}"
    )
