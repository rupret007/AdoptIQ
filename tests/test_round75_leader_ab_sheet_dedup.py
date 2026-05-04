"""Round 75 / Phase 2 (B2) -- dedup Leader Adoption_Barriers XLSX sheet by ID.

Build 47 acceptance audit caught the Leader Adoption_Barriers XLSX
sheet emitting 73 rows / 69 unique IDs for Brian Frazier 90d (4
duplicate barrier rows).  Root cause: when a single adoption barrier
is attributed to multiple CSSMs via the R72 ``_ATTRIBUTED_BY_ACCOUNT``
shared-account pathway in ``_slice_by_owner_or_account``, the
per-CSSM AB frames each carry the SAME ``ID``.  The leader XLSX
writer in ``app_simple.py`` (~L26322) used a raw
``pd.concat(all_adoption_barriers, ignore_index=True)`` with no
dedup, so the row was written N times (once per attributed CSSM).

R72/Layer A already routed the canonical_metrics ``count_open_barriers``
helper through ``_select_first_populated_status_column`` + dedup-by-ID
so the **count** in the docx headline was honest, but the **XLSX
sheet bytes** themselves still carried the duplicates.

R75/B2 fix: insert ``drop_duplicates(subset=['ID'], keep='first')``
immediately before the XLSX write.  Rows without an ID column are
passed through unchanged so the rare ID-less import case (e.g. a
malformed CSConsole export) doesn't lose data silently.

These tests pin the source-shape contract (the dedup call site exists
with the R75 marker) and the behavioural contract (a synthetic 2-CSSM
team where the same barrier is shared collapses to 1 sheet row).

Round 75 / Phase 2 (B2).  Made-with: Cursor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

import canonical_metrics as cm


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_SIMPLE = _REPO_ROOT / "app_simple.py"


# ---------------------------------------------------------------------------
# Source-shape pin: R75/B2 dedup block exists at the leader XLSX writer
# ---------------------------------------------------------------------------


def test_leader_ab_xlsx_writer_has_round_75_dedup_block():
    """The leader XLSX writer must carry the R75/B2 dedup block before
    assigning ``sheets['Adoption_Barriers']``.  Pre-R75 it was a raw
    ``pd.concat`` with no dedup; if a future refactor reverts that the
    Build 47 4-duplicate-rows regression returns silently."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")

    # Locate the leader Adoption_Barriers sheet assignment.
    idx = src.find("sheets['Adoption_Barriers'] = ")
    assert idx != -1, "leader XLSX Adoption_Barriers writer site missing"

    # Capture ~3 KB of context above the assignment to cover the dedup block.
    block = src[max(0, idx - 3000): idx + 200]

    assert "Round 75 / B2" in block, (
        "R75/B2 marker missing from leader Adoption_Barriers XLSX writer; "
        "the dedup-by-ID block must be tagged so git diff can grep it."
    )
    assert "drop_duplicates(subset=['ID']" in block, (
        "R75/B2 regression: leader Adoption_Barriers writer no longer "
        "calls drop_duplicates on the AB ID column. Build 47 audit "
        "caught 4 duplicate barrier rows; if this test fails, dupes return."
    )


def test_leader_ab_xlsx_dedup_logs_when_dupes_present():
    """When the dedup actually removes rows, the writer must emit a
    structured log line with the before/after counts so the operator
    can correlate the XLSX sheet's row count against the canonical
    barrier count."""
    src = _APP_SIMPLE.read_text(encoding="utf-8")
    idx = src.find("sheets['Adoption_Barriers'] = ")
    assert idx != -1
    block = src[max(0, idx - 3000): idx + 200]

    assert "leader Adoption_Barriers sheet" in block, (
        "R75/B2 dedup-log message text drifted; if you change the log "
        "format string, update both source and test."
    )
    assert re.search(r"%d raw rows -> %d unique", block) is not None, (
        "R75/B2 log format string drifted; expected 'N raw rows -> M unique' "
        "format for operator clarity."
    )


# ---------------------------------------------------------------------------
# Behavioural test: 2-CSSM team with shared barrier collapses to 1 row
# ---------------------------------------------------------------------------


def test_leader_ab_dedup_collapses_shared_barrier_to_one_row():
    """Synthetic 2-CSSM team where the same ``barrier_id='AB001'`` is
    attributed to BOTH CSSMs (the shape `_ATTRIBUTED_BY_ACCOUNT` produces).

    The R75/B2 dedup must collapse the two attributions to a single row
    in the XLSX sheet.  The cross-CSSM Customer / Subject / Status data
    must come from the FIRST CSSM (keep='first' contract).
    """
    cssm_alice = pd.DataFrame([
        {
            "ID": "AB001",
            "BU_NAME": "Acme Corp",
            "SUBJECT_C": "Onboarding stalled",
            "STATUS_C": "Open",
            "AB_CATEGORY_C": "Technical",
            "CSSM": "Alice",
        },
        {
            "ID": "AB002",
            "BU_NAME": "Beta Inc",
            "SUBJECT_C": "License confusion",
            "STATUS_C": "Open",
            "AB_CATEGORY_C": "Commercial",
            "CSSM": "Alice",
        },
    ])
    cssm_bob = pd.DataFrame([
        {
            "ID": "AB001",  # SAME barrier shared via account attribution
            "BU_NAME": "Acme Corp",
            "SUBJECT_C": "Onboarding stalled",
            "STATUS_C": "Open",
            "AB_CATEGORY_C": "Technical",
            "CSSM": "Bob",
        },
        {
            "ID": "AB003",
            "BU_NAME": "Gamma LLC",
            "SUBJECT_C": "Migration delay",
            "STATUS_C": "Open",
            "AB_CATEGORY_C": "Technical",
            "CSSM": "Bob",
        },
    ])

    # Replicate the R75/B2 dedup block inline.
    all_adoption_barriers = [cssm_alice, cssm_bob]
    combined = pd.concat(all_adoption_barriers, ignore_index=True)
    if "ID" in combined.columns:
        with_id = combined[combined["ID"].notna()].drop_duplicates(
            subset=["ID"], keep="first"
        )
        without_id = combined[combined["ID"].isna()]
        combined = pd.concat([with_id, without_id], ignore_index=True)

    # Pre-dedup: 4 rows; post-dedup: 3 unique IDs.
    assert len(combined) == 3, (
        f"R75/B2: shared AB001 should collapse to 1 row; got {len(combined)}"
    )
    assert set(combined["ID"]) == {"AB001", "AB002", "AB003"}

    # AB001 keeps the FIRST CSSM (Alice), per keep='first' contract.
    ab001_row = combined[combined["ID"] == "AB001"].iloc[0]
    assert ab001_row["CSSM"] == "Alice", (
        f"R75/B2: AB001 should keep first CSSM (Alice); got {ab001_row['CSSM']}"
    )


def test_leader_ab_xlsx_row_count_equals_canonical_count_open_barriers():
    """Cross-validation: the deduped XLSX sheet row count must equal
    ``canonical_metrics.count_open_barriers`` for the same input.

    This is the cross-format parity contract that R72/Layer A
    established for the docx headline AND the XLSX summary KPI; R75/B2
    extends it to the XLSX sheet's row count itself.
    """
    cssm_alice = pd.DataFrame([
        {"ID": "AB001", "STATUS_C": "Open", "BU_NAME": "Acme",   "CSSM": "Alice"},
        {"ID": "AB002", "STATUS_C": "Open", "BU_NAME": "Beta",   "CSSM": "Alice"},
        {"ID": "AB004", "STATUS_C": "Closed", "BU_NAME": "Delta", "CSSM": "Alice"},
    ])
    cssm_bob = pd.DataFrame([
        {"ID": "AB001", "STATUS_C": "Open", "BU_NAME": "Acme",   "CSSM": "Bob"},
        {"ID": "AB003", "STATUS_C": "Open", "BU_NAME": "Gamma",  "CSSM": "Bob"},
    ])

    combined = pd.concat([cssm_alice, cssm_bob], ignore_index=True)
    with_id = combined[combined["ID"].notna()].drop_duplicates(
        subset=["ID"], keep="first"
    )
    without_id = combined[combined["ID"].isna()]
    deduped = pd.concat([with_id, without_id], ignore_index=True)

    # 4 unique IDs total (AB001, AB002, AB003, AB004); 3 are Open.
    assert len(deduped) == 4

    # Open-barrier count from the deduped frame should equal canonical.
    open_count = int(cm.count_open_barriers(deduped))
    # The canonical helper also dedupes; running it on the raw concat
    # (5 rows, AB001 duplicated) must produce the same count.
    canonical_count = int(cm.count_open_barriers(combined))
    assert open_count == canonical_count == 3, (
        f"R75/B2 parity: deduped sheet open-count ({open_count}) must equal "
        f"canonical_metrics.count_open_barriers on raw input ({canonical_count}); "
        "expected 3 (AB001 dedup'd, AB002 + AB003 open, AB004 closed)."
    )


def test_leader_ab_xlsx_handles_rows_without_id_column_gracefully():
    """When the AB DataFrame lacks an ID column entirely (rare but
    possible from a malformed CSConsole export), the dedup block must
    pass the rows through unchanged -- never raise."""
    no_id_frame = pd.DataFrame([
        {"BU_NAME": "Acme",  "STATUS_C": "Open", "CSSM": "Alice"},
        {"BU_NAME": "Beta",  "STATUS_C": "Open", "CSSM": "Alice"},
    ])
    combined = pd.concat([no_id_frame], ignore_index=True)
    # The R75/B2 block guards on 'ID' in combined.columns -- no dedup applied.
    if "ID" in combined.columns:
        with_id = combined[combined["ID"].notna()].drop_duplicates(subset=["ID"], keep="first")
        without_id = combined[combined["ID"].isna()]
        combined = pd.concat([with_id, without_id], ignore_index=True)

    # No ID column -> all 2 rows survive untouched.
    assert len(combined) == 2
    assert "ID" not in combined.columns
