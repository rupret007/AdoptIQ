"""Round 78 / Phase 2 (B2) -- dedup Leader Action_Plans XLSX sheet by ID.

Build 53 acceptance audit caught the Leader Action_Plans XLSX sheet
emitting 366 rows / 344 unique IDs for Brian Frazier 90d (22 duplicate
AP rows).  Same root cause as R75/B2 (Adoption_Barriers sheet) but on
the AP sheet: when a single Action Plan is attributed to multiple
CSSMs via the R72 ``_ATTRIBUTED_BY_ACCOUNT`` shared-account pathway
in ``_slice_by_owner_or_account``, per-CSSM AP frames each carry the
SAME ``ID``.  The leader XLSX writer in ``app_simple.py`` (~L26412)
used a raw ``pd.concat(all_action_plans, ignore_index=True)`` with no
dedup, so the row was written N times (once per attributed CSSM).

R78/B2 fix: insert ``drop_duplicates(subset=['ID'], keep='first')``
immediately before the XLSX write.  Rows without an ID column are
passed through unchanged.  Mirrors the R75/B2 (Adoption_Barriers)
contract exactly so any future refactor that consolidates the two
dedup blocks into a shared helper picks up both sheets together.

These tests pin the source-shape contract (the dedup call site
exists with the R78 marker) and the behavioural contract (a
synthetic 2-CSSM team where the same AP is shared collapses to 1
sheet row).

Round 78 / Phase 2 (B2).  Made-with: Cursor.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source, index_in_source

import re
from pathlib import Path

import pandas as pd

import canonical_metrics as cm


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"


# ---------------------------------------------------------------------------
# Source-shape pin: R78/B2 dedup block exists at the leader XLSX writer
# ---------------------------------------------------------------------------


def test_leader_ap_xlsx_writer_has_round_78_dedup_block():
    """The leader XLSX writer must carry the R78/B2 dedup block before
    assigning ``sheets['Action_Plans']``.  Pre-R78 it was a raw
    ``pd.concat`` with no dedup; if a future refactor reverts that the
    Build 53 22-duplicate-rows regression returns silently."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")

    # Locate the leader Action_Plans sheet assignment.
    idx = index_in_source(src, 'sheets["Action_Plans"] = _r78_ap_combined')
    assert idx != -1, "leader XLSX Action_Plans writer site missing"

    # Capture ~3 KB of context above the assignment to cover the dedup block.
    block = src[max(0, idx - 3000): idx + 200]

    assert_in_source(block, "Round 78 / B2", label='block')
    assert_in_source(block, "drop_duplicates", label="block")
    assert_in_source(block, "subset=", label="block")


def test_leader_ap_xlsx_dedup_logs_when_dupes_present():
    """When the dedup actually removes rows, the writer must emit a
    structured log line with the before/after counts so the operator
    can correlate the XLSX sheet's row count against the canonical
    AP count."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    idx = index_in_source(src, 'sheets["Action_Plans"] = _r78_ap_combined')
    assert idx != -1
    block = src[max(0, idx - 3000): idx + 200]

    assert_in_source(block, "leader Action_Plans sheet", label='block')
    assert re.search(r"%d raw rows -> %d unique", block) is not None, (
        "R78/B2 log format string drifted; expected 'N raw rows -> M unique' "
        "format for operator clarity (matches R75/B2 contract)."
    )


def test_leader_ap_xlsx_dedup_block_mirrors_r75_b2_shape():
    """The R78/B2 dedup block MUST mirror R75/B2 exactly so any future
    refactor that consolidates them into a shared helper picks up both
    sheets together.  We assert both block markers exist within ~6 KB
    of each other in the writer flow.
    """
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    r78_idx = src.find("Round 78 / B2")
    r75_idx = src.find("Round 75 / B2")
    assert r78_idx != -1, "R78/B2 marker missing"
    assert r75_idx != -1, "R75/B2 marker missing -- AB dedup regression"
    # The two markers should be co-located in the writer flow (same
    # function body, ordered AP-then-AB to match the mirroring).
    distance = abs(r78_idx - r75_idx)
    assert distance < 6000, (
        f"R78/B2 and R75/B2 dedup blocks are {distance} chars apart; "
        f"they must be co-located so a refactor catches both sheets."
    )
    # AP must come before AB (same order as the existing flow which
    # does AP concat first, then AB dedup).
    assert r78_idx < r75_idx, (
        "R78/B2 (AP dedup) must appear BEFORE R75/B2 (AB dedup) in "
        "the writer flow -- the existing flow processes AP first."
    )


def test_round108_leader_customer_pulse_dedup_block_exists():
    """Round 108 artifact audit found the same cross-CSSM duplicate
    pattern on Leader.Customer_Pulse. Pin the writer has an ID dedup
    block before assigning the sheet."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    idx = index_in_source(src, "sheets['Customer_Pulse'] = ")
    assert idx != -1, "leader XLSX Customer_Pulse writer site missing"
    block = src[max(0, idx - 2500): idx + 200]
    assert_in_source(block, "Round 108 / artifact audit", label='block')
    assert_in_source(block, "drop_duplicates(subset=", label='block')
    assert_in_source(block, "leader Customer_Pulse sheet deduped", label='block')


# ---------------------------------------------------------------------------
# Behavioural test: 2-CSSM team with shared AP collapses to 1 row
# ---------------------------------------------------------------------------


def test_leader_ap_dedup_collapses_shared_action_plan_to_one_row():
    """Synthetic 2-CSSM team where the same ``AP_ID='AP001'`` is
    attributed to BOTH CSSMs (the shape `_ATTRIBUTED_BY_ACCOUNT`
    produces).

    The R78/B2 dedup must collapse the two attributions to a single
    row in the XLSX sheet.  The cross-CSSM data must come from the
    FIRST CSSM (keep='first' contract).
    """
    cssm_alice = pd.DataFrame([
        {
            "ID": "AP001",
            "BU_NAME": "Acme Corp",
            "Subject": "Webex onboarding",
            "Status": "In Progress",
            "Action Type": "Onboarding",
            "CSSM": "Alice",
        },
        {
            "ID": "AP002",
            "BU_NAME": "Beta Inc",
            "Subject": "Calling deployment",
            "Status": "New Request",
            "Action Type": "Deployment",
            "CSSM": "Alice",
        },
    ])
    cssm_bob = pd.DataFrame([
        {
            "ID": "AP001",  # SAME AP shared via account attribution
            "BU_NAME": "Acme Corp",
            "Subject": "Webex onboarding",
            "Status": "In Progress",
            "Action Type": "Onboarding",
            "CSSM": "Bob",
        },
        {
            "ID": "AP003",
            "BU_NAME": "Gamma LLC",
            "Subject": "Migration prep",
            "Status": "In Progress",
            "Action Type": "Migration",
            "CSSM": "Bob",
        },
    ])

    # Replicate the R78/B2 dedup block inline.
    all_action_plans = [cssm_alice, cssm_bob]
    combined = pd.concat(all_action_plans, ignore_index=True)
    if "ID" in combined.columns:
        with_id = combined[combined["ID"].notna()].drop_duplicates(
            subset=["ID"], keep="first"
        )
        without_id = combined[combined["ID"].isna()]
        combined = pd.concat([with_id, without_id], ignore_index=True)

    # Pre-dedup: 4 rows; post-dedup: 3 unique IDs.
    assert len(combined) == 3, (
        f"R78/B2: shared AP001 should collapse to 1 row; got {len(combined)}"
    )
    assert set(combined["ID"]) == {"AP001", "AP002", "AP003"}

    # AP001 keeps the FIRST CSSM (Alice), per keep='first' contract.
    ap001_row = combined[combined["ID"] == "AP001"].iloc[0]
    assert ap001_row["CSSM"] == "Alice", (
        f"R78/B2: AP001 should keep first CSSM (Alice); got "
        f"{ap001_row['CSSM']}"
    )


def test_round108_leader_customer_pulse_dedup_collapses_shared_row_to_one():
    cssm_alice = pd.DataFrame([
        {"ID": "CP001", "BU_NAME": "Acme Corp", "Pulse": "Green", "CSSM": "Alice"},
        {"ID": None, "BU_NAME": "No Id Corp", "Pulse": "Yellow", "CSSM": "Alice"},
    ])
    cssm_bob = pd.DataFrame([
        {"ID": "CP001", "BU_NAME": "Acme Corp", "Pulse": "Green", "CSSM": "Bob"},
        {"ID": "CP002", "BU_NAME": "Beta Inc", "Pulse": "Red", "CSSM": "Bob"},
    ])

    combined = pd.concat([cssm_alice, cssm_bob], ignore_index=True)
    with_id = combined[combined["ID"].notna()].drop_duplicates(subset=["ID"], keep="first")
    without_id = combined[combined["ID"].isna()]
    combined = pd.concat([with_id, without_id], ignore_index=True)

    assert sorted(v for v in combined["ID"].dropna().tolist()) == ["CP001", "CP002"]
    assert len(combined) == 3
    assert combined.loc[combined["ID"] == "CP001", "CSSM"].iloc[0] == "Alice"


def test_leader_ap_xlsx_row_count_equals_canonical_count_open_action_plans():
    """Cross-validation: the deduped XLSX sheet's open-AP count must
    equal ``canonical_metrics.count_open_action_plans`` for the same
    input.

    This mirrors the R75/B2 cross-format parity contract for AP
    counts.  Build 53's pre-fix Leader.Action_Plans sheet had 366
    rows but the canonical helper (which already dedupes) reported
    344 -- the fix closes that 22-row gap.
    """
    cssm_alice = pd.DataFrame([
        {"ID": "AP001", "Status": "In Progress",        "BU_NAME": "Acme",  "CSSM": "Alice"},
        {"ID": "AP002", "Status": "New Request",        "BU_NAME": "Beta",  "CSSM": "Alice"},
        {"ID": "AP004", "Status": "Completed - Successful", "BU_NAME": "Delta", "CSSM": "Alice"},
    ])
    cssm_bob = pd.DataFrame([
        {"ID": "AP001", "Status": "In Progress", "BU_NAME": "Acme",  "CSSM": "Bob"},
        {"ID": "AP003", "Status": "In Progress", "BU_NAME": "Gamma", "CSSM": "Bob"},
    ])

    combined = pd.concat([cssm_alice, cssm_bob], ignore_index=True)
    with_id = combined[combined["ID"].notna()].drop_duplicates(
        subset=["ID"], keep="first"
    )
    without_id = combined[combined["ID"].isna()]
    deduped = pd.concat([with_id, without_id], ignore_index=True)

    # 4 unique IDs total (AP001, AP002, AP003, AP004); 3 are open.
    assert len(deduped) == 4

    # Open-AP count from the deduped frame should equal canonical.
    # canonical_metrics.count_open_action_plans takes ap_df as the
    # Action_Plans sheet input (post-R64/B2). The canonical helper
    # also dedupes via its own provenance/status logic but does NOT
    # dedup-by-ID, so we feed the deduped frame directly.
    open_count = int(cm.count_open_action_plans(None, ap_df=deduped))
    assert open_count == 3, (
        f"R78/B2 parity: deduped sheet open-count should be 3 "
        f"(AP001 dedup'd open + AP002 + AP003 open; AP004 closed); "
        f"got {open_count}"
    )


def test_leader_ap_xlsx_handles_rows_without_id_column_gracefully():
    """When the AP DataFrame lacks an ID column entirely (rare but
    possible from a malformed CSConsole export), the dedup block
    must pass the rows through unchanged -- never raise."""
    no_id_frame = pd.DataFrame([
        {"BU_NAME": "Acme",  "Status": "In Progress", "CSSM": "Alice"},
        {"BU_NAME": "Beta",  "Status": "New Request", "CSSM": "Alice"},
    ])
    combined = pd.concat([no_id_frame], ignore_index=True)
    # The R78/B2 block guards on 'ID' in combined.columns -- no dedup applied.
    if "ID" in combined.columns:
        with_id = combined[combined["ID"].notna()].drop_duplicates(
            subset=["ID"], keep="first"
        )
        without_id = combined[combined["ID"].isna()]
        combined = pd.concat([with_id, without_id], ignore_index=True)

    # No ID column -> all 2 rows survive untouched.
    assert len(combined) == 2
    assert "ID" not in combined.columns


def test_leader_ap_xlsx_handles_partial_id_column_gracefully():
    """When SOME rows have an ID and others don't (e.g. a row from a
    fallback path that didn't carry the ID), the dedup block must
    dedup the ID-bearing rows AND pass the ID-less rows through
    unchanged.  This is the explicit `notna() / isna()` split in the
    R78/B2 block.
    """
    cssm_alice = pd.DataFrame([
        {"ID": "AP001", "BU_NAME": "Acme", "CSSM": "Alice"},
        {"ID": None,    "BU_NAME": "Acme", "CSSM": "Alice"},  # ID-less row
        {"ID": "AP002", "BU_NAME": "Beta", "CSSM": "Alice"},
    ])
    cssm_bob = pd.DataFrame([
        {"ID": "AP001", "BU_NAME": "Acme", "CSSM": "Bob"},  # dup of Alice's AP001
        {"ID": None,    "BU_NAME": "Beta", "CSSM": "Bob"},  # ID-less row
    ])

    combined = pd.concat([cssm_alice, cssm_bob], ignore_index=True)
    if "ID" in combined.columns:
        with_id = combined[combined["ID"].notna()].drop_duplicates(
            subset=["ID"], keep="first"
        )
        without_id = combined[combined["ID"].isna()]
        combined = pd.concat([with_id, without_id], ignore_index=True)

    # AP001 deduped to 1, AP002 retained, plus 2 ID-less rows = 4 rows.
    assert len(combined) == 4, (
        f"R78/B2: partial-ID dedup should yield 4 rows (2 unique IDs + "
        f"2 ID-less); got {len(combined)}"
    )
    id_present = combined[combined["ID"].notna()]
    assert set(id_present["ID"]) == {"AP001", "AP002"}
    # The 2 ID-less rows must both survive
    id_absent = combined[combined["ID"].isna()]
    assert len(id_absent) == 2


def test_leader_ap_xlsx_empty_input_does_not_error():
    """The R78/B2 block sits inside ``if all_action_plans:`` so an
    empty list of per-CSSM frames never reaches the dedup logic.
    But if a downstream refactor moves the dedup outside the guard,
    we want to confirm the inline replica behaves safely on an
    empty list of frames.
    """
    all_action_plans = []
    if all_action_plans:
        # Should NOT execute -- if it did and threw, that would be the bug.
        combined = pd.concat(all_action_plans, ignore_index=True)
        assert combined.empty
    else:
        # Empty input correctly skipped -- no sheet is assigned.
        # We assert nothing was built.
        pass

    # Direct sanity check: an empty list passed to pd.concat raises
    # ValueError -- so the R78/B2 block's outer ``if all_action_plans:``
    # guard is load-bearing.  Confirm that contract.
    try:
        pd.concat([], ignore_index=True)
        raised = False
    except ValueError:
        raised = True
    assert raised, (
        "pandas no longer raises on pd.concat([]); the R78/B2 block's "
        "outer ``if all_action_plans:`` guard becomes redundant -- "
        "but the test must be updated, not the guard removed."
    )
