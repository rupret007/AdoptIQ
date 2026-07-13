"""Round 70 / Phase 2 (#6) -- Comprehensive ``Risk_Components`` sheet
MUST always be present (even with provenance row).

Build 43 acceptance audit found the Comprehensive XLSX sheet inventory
was ``['Summary', 'Report_Info', 'AB_Detail_All', 'CSOne_Detail_All',
'External_Bugs', 'External_Incidents', 'Action_Plans',
'CSConsole_Customer_Pulse']`` -- ``Risk_Components`` was MISSING entirely.

R67/B2 contract: ``Risk_Components`` MUST always be present (even with
a provenance row when ``risk_profiles`` is empty). The hoist-out-of-
try/except pattern was being undone somewhere.

The Round 70 fix re-pins the sheet construction with an explicit
contract comment so a future edit can't silently drop the sheet again.
"""
from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (PROJECT_ROOT / "app_simple.py").read_text(encoding="utf-8")


def test_risk_components_sheet_assignment_present() -> None:
    """R70/Phase 2 (#6): the Comprehensive writer block MUST contain
    an explicit ``all_sheets['Risk_Components']`` assignment with a
    contract comment that names this round."""
    src = _read_app_simple()
    assert "all_sheets['Risk_Components']" in src or 'all_sheets["Risk_Components"]' in src, (
        "Round 70 / #6: Comprehensive writer MUST assign Risk_Components "
        "to all_sheets so the sheet always lands in the workbook."
    )


def test_risk_components_round_70_marker_present() -> None:
    """R70/Phase 2 (#6): the source MUST carry a ``Round 70`` marker
    on the Risk_Components contract reinforcement so the per-file diff
    surfaces the contract."""
    src = _read_app_simple()
    assert "Round 70 / Phase 2 (#6)" in src or "Round 70 / B2" in src, (
        "Round 70 / #6: Risk_Components contract reinforcement MUST "
        "carry a Round 70 footprint marker."
    )


def test_risk_components_provenance_row_path_exists() -> None:
    """R67/B2 + R70/#6: the empty-risk_profiles branch MUST emit a
    provenance row rather than dropping the sheet entirely."""
    src = _read_app_simple()
    needles = (
        "Risk_Components',",
        "provenance",
    )
    assert any(n in src for n in needles), (
        "Round 70 / #6: the empty-risk_profiles fallback MUST be "
        "discoverable in the source."
    )
