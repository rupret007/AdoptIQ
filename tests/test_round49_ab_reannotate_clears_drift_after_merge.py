"""Round 49 / F-DV-CONTRACT-DRIFT-R49 -- AB post-merge re-annotate.

Build25 re-audit (~/.adoptiq/adoptiq.27265.log lines 508-509) showed
the adoption_barriers contract still failing despite Round 48's
alias expansion.  Root cause: the contract is run by
``snowflake_prefetch.py`` immediately after the raw fetch from
``EDW_SALES_ETL_DB.SS.C360_CS_TASK_C_VW``, which only carries
``ACCOUNT_ID_C`` (no name column).  R48 widened the alias list to
include the friendly forms (``Customer`` / ``Customer Name`` /
``BU_ACCOUNT_NAME``) -- but those columns don't exist on the raw
view.  They are materialized later via the ``team_subs_df`` merge
in ``app_simple.py`` (renewal ~L11627, compact/EI ~L7143,
comprehensive ~L13247).  At the merge point the ``BU_NAME`` column
finally arrives, but the ``schema_drift`` ``fetch_error`` stamp from
the pre-merge contract run never gets cleared, so the partial-data
banner surfaces a contract violation that no longer reflects reality.

R49-A2 fix: ``annotate_with_contract`` now self-heals.  When called
on a frame that previously stamped ``fetch_error_kind='schema_drift'``
for the SAME dataset and the contract NOW passes (because a downstream
merge materialized the missing column), it pops the stamp.  The 4
AB merge sites in ``app_simple.py`` re-call ``annotate_with_contract``
post-merge so the stamp is cleared whenever the merge actually
populates the customer column.

This test pins:

1. Pre-merge: a raw AB frame with only ``ACCOUNT_ID_C`` is escalated
   to ``schema_drift`` (existing R32 behaviour).
2. Post-merge re-annotate: once ``BU_NAME`` is materialized, calling
   ``annotate_with_contract`` again clears the prior ``schema_drift``
   stamp because the contract now passes.
3. Genuine fetch_error stamps (NOT schema_drift) are preserved -- we
   only clear stamps we own.
4. Cross-dataset stamps (e.g. ``fetch_error_kind='schema_drift'`` from
   a different dataset) are preserved -- we only clear stamps for
   the SAME dataset.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_contracts import annotate_with_contract  # noqa: E402
from snowflake_prefetch import collect_fetch_warnings  # noqa: E402


def test_pre_merge_raw_ab_frame_escalates_schema_drift():
    """Sanity: the raw view (only ACCOUNT_ID_C, no BU_NAME) is the
    classic ``schema_drift`` trigger we still want to surface.
    """
    raw = pd.DataFrame(
        {
            "ID": [f"AB-{i}" for i in range(5)],
            "ACCOUNT_ID_C": [f"a-{i}" for i in range(5)],
            "SUBJECT_C": ["fix"] * 5,
            "AB_STATUS_C": ["Open"] * 5,
            "SEVERITY_C": ["P2"] * 5,
        }
    )
    annotated = annotate_with_contract(raw, dataset="adoption_barriers")
    assert "fetch_error" in annotated.attrs, (
        "Pre-merge AB frame must still escalate schema_drift -- "
        "without a customer-bearing column the contract MUST fail."
    )
    assert annotated.attrs.get("fetch_error_kind") == "schema_drift"
    assert annotated.attrs.get("fetch_error_dataset") == "adoption_barriers"


def test_post_merge_reannotate_clears_schema_drift_stamp():
    """The R49-A2 self-heal: post-merge re-annotation clears the
    stamp because the contract now passes once BU_NAME arrives.
    """
    raw = pd.DataFrame(
        {
            "ID": [f"AB-{i}" for i in range(5)],
            "ACCOUNT_ID_C": [f"a-{i}" for i in range(5)],
            "SUBJECT_C": ["fix"] * 5,
            "AB_STATUS_C": ["Open"] * 5,
            "SEVERITY_C": ["P2"] * 5,
        }
    )
    annotate_with_contract(raw, dataset="adoption_barriers")
    assert raw.attrs.get("fetch_error_kind") == "schema_drift"

    raw["BU_NAME"] = [f"Customer {i}" for i in range(5)]
    raw_attrs = raw.attrs.copy()

    annotate_with_contract(raw, dataset="adoption_barriers")

    assert "fetch_error" not in raw.attrs, (
        "R49-A2: post-merge re-annotate must CLEAR the schema_drift "
        "stamp once the contract passes; got fetch_error="
        f"{raw.attrs.get('fetch_error')!r}"
    )
    assert "fetch_error_kind" not in raw.attrs
    assert "fetch_error_dataset" not in raw.attrs
    assert raw_attrs.get("fetch_error_kind") == "schema_drift", (
        "Sanity: the stamp was indeed present BEFORE the re-annotate."
    )


def test_genuine_fetch_failure_stamp_preserved():
    """The self-heal must only clear stamps we own.  If the loader
    stamped a real fetch failure (e.g. ``fetch_error_kind='timeout'``
    or absent ``fetch_error_kind`` because it pre-dates R32), the
    re-annotate must NOT clear it.
    """
    df = pd.DataFrame(
        {
            "ID": [1],
            "ACCOUNT_ID_C": ["a-1"],
            "BU_NAME": ["Acme"],
            "SUBJECT_C": ["fix"],
            "AB_STATUS_C": ["Open"],
            "SEVERITY_C": ["P2"],
        }
    )
    df.attrs["fetch_error"] = "Snowflake auth failed"
    df.attrs["fetch_error_kind"] = "timeout"

    annotate_with_contract(df, dataset="adoption_barriers")

    assert df.attrs.get("fetch_error") == "Snowflake auth failed", (
        "R49-A2: a genuine fetch_failure stamp (kind != schema_drift) "
        "must be preserved -- the self-heal only clears its own stamps."
    )
    assert df.attrs.get("fetch_error_kind") == "timeout"


def test_cross_dataset_drift_stamp_preserved():
    """If a frame carries a ``schema_drift`` stamp for a DIFFERENT
    dataset (rare but possible during cross-fixture re-use), the
    re-annotate for our dataset must not clear it.
    """
    df = pd.DataFrame(
        {
            "ID": [1],
            "ACCOUNT_ID_C": ["a-1"],
            "BU_NAME": ["Acme"],
            "SUBJECT_C": ["fix"],
            "AB_STATUS_C": ["Open"],
            "SEVERITY_C": ["P2"],
        }
    )
    df.attrs["fetch_error"] = "schema_drift:tac_cases: missing slot"
    df.attrs["fetch_error_kind"] = "schema_drift"
    df.attrs["fetch_error_dataset"] = "tac_cases"

    annotate_with_contract(df, dataset="adoption_barriers")

    assert df.attrs.get("fetch_error_dataset") == "tac_cases", (
        "R49-A2: a schema_drift stamp owned by a different dataset "
        "must be preserved -- the self-heal only matches its own "
        "dataset name."
    )
    assert df.attrs.get("fetch_error_kind") == "schema_drift"


def test_csconsole_ab_post_merge_reannotate_suppresses_warning_promotion():
    """Round 50 follow-up: when compact/comprehensive merge
    csconsole_adoption_barriers with team_subs and re-annotate, the
    stale adoption_barriers schema_drift must no longer be promoted by
    collect_fetch_warnings.
    """
    raw = pd.DataFrame(
        {
            "ID": [f"AB-{i}" for i in range(3)],
            "ACCOUNT_ID_C": [f"a-{i}" for i in range(3)],
            "SUBJECT_C": ["fix"] * 3,
            "AB_STATUS_C": ["Open"] * 3,
            "SEVERITY_C": ["P2"] * 3,
        }
    )
    annotate_with_contract(raw, dataset="adoption_barriers")
    assert raw.attrs.get("fetch_error_kind") == "schema_drift"

    team_subs = pd.DataFrame(
        {
            "ACCOUNT_ID_C": [f"a-{i}" for i in range(3)],
            "BU_NAME": [f"Customer {i}" for i in range(3)],
            "CSSM_EMAIL": [f"user{i}@example.com" for i in range(3)],
        }
    )
    merged = raw.merge(
        team_subs[["ACCOUNT_ID_C", "BU_NAME", "CSSM_EMAIL"]].drop_duplicates(),
        on="ACCOUNT_ID_C",
        how="left",
    )
    annotate_with_contract(merged, dataset="adoption_barriers")
    warnings = collect_fetch_warnings({"csconsole_adoption_barriers": merged})
    problematic = [
        w for w in warnings
        if (w.get("dataset") in {"adoption_barriers", "csconsole_adoption_barriers"})
        and (w.get("kind") == "schema_drift" or "missing slot(s) customer" in str(w.get("error")))
    ]
    assert not problematic, (
        "Round 50 follow-up: csconsole AB post-merge re-annotate should clear "
        "schema_drift so collect_fetch_warnings no longer promotes it; "
        f"got {problematic!r}"
    )


def test_renewal_app_simple_calls_reannotate_post_merge():
    """Pin the wiring: ``app_simple.py`` MUST re-call
    ``annotate_with_contract`` for adoption_barriers after each
    merge with team_subs_df that materializes BU_NAME.  Pre-R49
    this re-call did not exist and the schema_drift stamp persisted
    into Build25's partial-data warnings.
    """
    app_simple = (PROJECT_ROOT / "app_simple.py").read_text()
    needle = (
        "Round 49 / F-DV-CONTRACT-DRIFT-R49"
    )
    occurrences = app_simple.count(needle)
    assert occurrences >= 4, (
        f"R49-A2: expected >=4 R49 post-merge re-annotate hooks in "
        f"app_simple.py (renewal raw AB, renewal CSConsole AB, "
        f"compact/EI raw AB, comprehensive raw AB) -- got {occurrences}."
    )

    # Round 50 / F-DV-CONTRACT-DRIFT-CSAB-COMPACT+COMPREHENSIVE:
    # compact and comprehensive must also re-annotate CSConsole AB
    # after team_subs merge BEFORE warning promotion runs.
    assert "F-DV-CONTRACT-DRIFT-CSAB-COMPACT" in app_simple
    assert "F-DV-CONTRACT-DRIFT-CSAB-COMPREHENSIVE" in app_simple
