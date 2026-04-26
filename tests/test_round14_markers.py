"""Round 14 marker tests.

Each Round 14 fix carries a ``Round 14 / Phase X.Y`` comment marker on
the touched source line(s).  These tests assert those markers stay in
place so the marker is the canonical "we already fixed this" signal for
future audits.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def test_marker_phase_1_1_bind_host_default_loopback() -> None:
    src = _read("app_simple.py")
    assert "Round 14 / Phase 1.1" in src, (
        "Round 14 / Phase 1.1 marker missing in app_simple.py "
        "(loopback-by-default Flask bind host)."
    )


def test_marker_phase_2_1_data_contracts_columns() -> None:
    src = _read("data_contracts.py")
    assert "Round 14 / Phase 2.1" in src, (
        "Round 14 / Phase 2.1 marker missing in data_contracts.py "
        "(validate_row_contract columns_raw fix)."
    )


def test_marker_phase_2_2_admin_dashboard_helpers() -> None:
    src = _read("enhanced_admin_dashboard_v2.py")
    assert "Round 14 / Phase 2.2" in src, (
        "Round 14 / Phase 2.2 marker missing in enhanced_admin_dashboard_v2.py "
        "(_utc_iso_z / _tz promoted to module scope)."
    )


def test_marker_phase_2_3_orderdict_alias() -> None:
    src = _read("adoptiq_backend.py")
    assert "Round 14 / Phase 2.3" in src, (
        "Round 14 / Phase 2.3 marker missing in adoptiq_backend.py "
        "(OrderedDict alias fix)."
    )


def test_marker_phase_2_4_locals_get_antipattern() -> None:
    src = _read("app_simple.py")
    occurrences = src.count("Round 14 / Phase 2.4")
    assert occurrences >= 3, (
        f"Round 14 / Phase 2.4 marker should appear at least 3 times in app_simple.py "
        f"(once per replaced antipattern site); found {occurrences}."
    )


def test_marker_phase_3_1_shutdown_handler_guarded() -> None:
    src = _read("app_simple.py")
    assert "Round 14 / Phase 3.1" in src, (
        "Round 14 / Phase 3.1 marker missing in app_simple.py "
        "(_shutdown_handler logger guard)."
    )
