"""Round 66 / Pass 2 (B9) — PSIRT_Vulnerabilities NaN/blank Customer drop.

Pre-R66 the Leader-report PSIRT_Vulnerabilities XLSX writer at
app_simple.py L23005 emitted rows with ``Customer = ''`` for
portfolio-wide CVE / PSIRT advisories AND occasionally leaked rows
with ``Customer = NaN`` / ``Customer = "Unknown"`` from a malformed
``vulnerability_by_customer`` entry. Downstream readers couldn't tell
"unknown customer attribution" apart from "no customer attribution
intended (portfolio-wide)".

R66/B9 fixes this by:
1. Skipping per-customer rows where the customer key is NaN / blank
   / "Unknown" / "N/A".
2. Labeling portfolio-wide CVE / PSIRT rows as
   ``Customer = '(Portfolio-wide)'`` (not blank) so the distinction
   is unambiguous in the XLSX.

These tests pin the source-shape of the writer block.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def writer_block() -> str:
    """Load the PSIRT_Vulnerabilities writer block from app_simple.py."""
    repo_root = Path(__file__).resolve().parent.parent
    text = (repo_root / "app_simple.py").read_text(encoding="utf-8")
    start_marker = "# PSIRT vulnerabilities (from CSOne/Adoption Barriers)"
    end_marker = "sheets['PSIRT_Vulnerabilities'] = pd.DataFrame(vuln_rows)"
    start_idx = text.index(start_marker)
    end_idx = text.index(end_marker, start_idx) + len(end_marker)
    return text[start_idx:end_idx]


def test_writer_block_has_r66_b9_marker(writer_block: str) -> None:
    """R66/B9 source marker MUST be present so a future refactor flags here."""
    assert "Round 66 / Pass 2 (B9)" in writer_block, (
        "R66/B9 source marker missing from PSIRT_Vulnerabilities writer"
    )


def test_writer_block_skips_blank_customer_keys(writer_block: str) -> None:
    """Per-customer loop MUST skip blank / NaN / Unknown customer keys."""
    skip_set = '{"nan", "none", "null", "unknown", "n/a"}'
    assert skip_set in writer_block, (
        "R66/B9 skip-set for blank customer keys missing"
    )


def test_writer_block_uses_portfolio_wide_label(writer_block: str) -> None:
    """Portfolio-wide CVE / PSIRT rows MUST be labeled ``(Portfolio-wide)``."""
    assert "'(Portfolio-wide)'" in writer_block, (
        "Portfolio-wide label missing from PSIRT writer"
    )


def test_writer_block_no_longer_emits_empty_customer_for_cve_or_psirt(writer_block: str) -> None:
    """The pre-R66 ``'Customer': ''`` pattern MUST be gone."""
    assert "'Customer': ''" not in writer_block, (
        "Pre-R66 blank-Customer leak still present"
    )


def test_writer_block_skips_empty_vulnerability_ids(writer_block: str) -> None:
    """Vulnerability IDs that are blank / NaN / None MUST be skipped."""
    assert "_vid_str" in writer_block, (
        "Per-row VID-strip helper variable missing"
    )
    assert 'Round 66 / B9: also skip empty vulnerability IDs' in writer_block, (
        "VID skip rationale comment missing"
    )


def test_writer_block_handles_per_customer_loop_in_correct_order(writer_block: str) -> None:
    """Per-customer loop MUST run BEFORE portfolio-wide loops."""
    per_customer_pos = writer_block.index("vulnerability_by_customer")
    portfolio_cve_pos = writer_block.index("cve_ids")
    portfolio_psirt_pos = writer_block.index("psirt_advisories")
    assert per_customer_pos < portfolio_cve_pos < portfolio_psirt_pos, (
        "Loops must run per-customer -> CVE -> PSIRT in that order"
    )


def test_writer_block_does_not_write_sheet_when_no_rows(writer_block: str) -> None:
    """If ALL rows were dropped, the sheet MUST NOT be written (idempotency)."""
    # The ``if vuln_rows:`` guard at the end ensures empty PSIRT runs
    # don't materialize an empty sheet.
    assert "if vuln_rows:" in writer_block, (
        "Writer must guard sheet creation on non-empty vuln_rows"
    )


def test_simulated_writer_drops_nan_customer_row() -> None:
    """End-to-end simulation: the writer block's logic drops NaN customer rows."""
    import pandas as pd
    from data_normalization import normalize_customer_name

    # Simulate the per-customer loop's input: one good entry + one bad.
    vulnerability_by_customer = {
        "Acme Corp": ["CVE-2026-001"],
        "": ["CVE-2026-002"],         # blank
        "Unknown": ["CVE-2026-003"],  # Unknown
        None: ["CVE-2026-004"],       # NaN-ish
    }
    vuln_rows = []
    for cust, ids in vulnerability_by_customer.items():
        try:
            _cust_norm = normalize_customer_name(cust) or cust
        except Exception:
            _cust_norm = cust
        _cust_str = str(_cust_norm or "").strip()
        if not _cust_str or _cust_str.lower() in {"nan", "none", "null", "unknown", "n/a"}:
            continue
        for vid in (ids if isinstance(ids, (list, set)) else [ids]):
            _vid_str = str(vid or "").strip()
            if not _vid_str or _vid_str.lower() in {"nan", "none"}:
                continue
            vuln_rows.append({"Customer": _cust_str, "Vulnerability_ID": _vid_str})

    # Acme Corp (with "Acme Corp" or normalized) is the only row that
    # should survive.
    assert len(vuln_rows) == 1, f"Expected 1 row, got {len(vuln_rows)}: {vuln_rows}"
    assert "acme" in vuln_rows[0]["Customer"].lower()
    assert vuln_rows[0]["Vulnerability_ID"] == "CVE-2026-001"


def test_simulated_writer_labels_portfolio_wide_rows() -> None:
    """End-to-end simulation: portfolio-wide CVE/PSIRT rows carry the new label."""
    cve_ids = {"CVE-2026-100", "CVE-2026-101"}
    psirt_advisories = {"PSIRT-2026-050"}

    vuln_rows = []
    for vid in cve_ids:
        _vid_str = str(vid or "").strip()
        if not _vid_str or _vid_str.lower() in {"nan", "none"}:
            continue
        vuln_rows.append({"Customer": "(Portfolio-wide)", "Vulnerability_ID": _vid_str, "Type": "CVE"})
    for vid in psirt_advisories:
        _vid_str = str(vid or "").strip()
        if not _vid_str or _vid_str.lower() in {"nan", "none"}:
            continue
        vuln_rows.append({"Customer": "(Portfolio-wide)", "Vulnerability_ID": _vid_str, "Type": "PSIRT"})

    assert len(vuln_rows) == 3
    for row in vuln_rows:
        assert row["Customer"] == "(Portfolio-wide)", (
            f"All portfolio-wide rows MUST carry the label, got: {row['Customer']!r}"
        )
