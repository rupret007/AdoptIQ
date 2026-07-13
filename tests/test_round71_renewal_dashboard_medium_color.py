"""Round 71 / Phase 0 (#3) -- Renewal dashboard MEDIUM color branch.

Pre-R71 the renewal Word dashboard table colored CRITICAL (red),
HIGH (orange), and LOW (green) but had NO branch for MEDIUM/MODERATE,
so a customer scored as MEDIUM rendered in the default text color
(visually identical to "no risk band assigned"), and HEALTHY had no
branch either.

Round 71 / Phase 0 (#3) adds gold (RGB 218,165,32) for MEDIUM/MODERATE
and a brighter green (40,180,99) for HEALTHY so every band has a
distinct color.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8", errors="replace")


def test_round71_renewal_dashboard_has_medium_moderate_color_branch() -> None:
    """The renewal dashboard table writer MUST color MEDIUM AND MODERATE
    customers with a gold tone so they are visually distinct from
    LOW/HEALTHY (which are green) and HIGH/CRITICAL (which are red)."""
    src = _read_app_simple()
    assert "risk_category in ('MEDIUM', 'MODERATE')" in src or 'risk_category in ("MEDIUM", "MODERATE")' in src, (
        "Round 71 / Phase 0 (#3): renewal dashboard table writer must "
        "include a color branch for both MEDIUM (canonical band key) "
        "and MODERATE (user-facing remap) so the gold tile applies "
        "regardless of which surface the upstream value came from."
    )
    assert "RGBColor(218, 165, 32)" in src, (
        "Round 71 / Phase 0 (#3): the MEDIUM/MODERATE color branch must "
        "use the gold color RGB(218, 165, 32) so the visual contract "
        "matches the rest of the renewal dashboard palette."
    )


def test_round71_renewal_dashboard_has_healthy_color_branch() -> None:
    """A HEALTHY band MUST also have its own bright-green color branch.
    Pre-R71 only LOW had green; HEALTHY was unstyled."""
    src = _read_app_simple()
    assert "risk_category == 'HEALTHY'" in src or 'risk_category == "HEALTHY"' in src, (
        "Round 71 / Phase 0 (#3): renewal dashboard must color HEALTHY "
        "customers with the brighter green tone so HEALTHY is visually "
        "distinct from LOW."
    )
    assert "RGBColor(40, 180, 99)" in src, (
        "Round 71 / Phase 0 (#3): HEALTHY branch must use RGB(40, 180, 99)."
    )
