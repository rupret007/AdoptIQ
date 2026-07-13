"""Round 49 / F-COMP-AAG-SCOPE-LABEL regression tests.

Build25 audit caught the compact docx At-a-Glance dashboard tile
labelling support cases ``Support Cases: 294`` (portfolio-wide
scope) while the per-customer deep-dive used
``Total Support Cases (90d): 36 - Open + critical (P1+P2): 1 (P2)``
for the same portfolio.  Both lines were truthful but read as
off-by-scope side-by-side; the tile is portfolio-wide and the
deep-dive is per-customer.

Round 49 makes the dashboard tile's scope explicit so the two
lines are unambiguously consistent:

* Table header cell: ``Support Cases (90d, portfolio-wide)``
* Metric-source bullet: ``Total Support Cases (90d, portfolio-wide): N``

Tests pin both surface labels and lock-in that the original
ambiguous ``Support Cases`` label is no longer emitted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Source-level wiring pin (cheap)
# ---------------------------------------------------------------------------


def test_compact_aag_dashboard_uses_scope_explicit_table_header() -> None:
    """The compact At-a-Glance dashboard table header for Support
    Cases MUST include explicit scope ``portfolio-wide``."""
    src = (_REPO_ROOT / "compact_report_formatter.py").read_text()
    assert "'Support Cases (90d, portfolio-wide)'" in src, (
        "Round 49 / F-COMP-AAG-SCOPE-LABEL regression: compact dashboard "
        "table header is no longer scope-explicit ``Support Cases (90d, "
        "portfolio-wide)`` -- the table tile will read ambiguously next "
        "to the per-customer deep-dive."
    )


def test_compact_aag_metric_bullet_uses_scope_explicit_label() -> None:
    """The metric-source bullet under the dashboard MUST also use the
    scope-explicit label so the bullet line reads
    ``Total Support Cases (90d, portfolio-wide): N`` instead of the
    ambiguous ``Support Cases: N``."""
    src = (_REPO_ROOT / "compact_report_formatter.py").read_text()
    assert '"Total Support Cases (90d, portfolio-wide)"' in src, (
        "Round 49 / F-COMP-AAG-SCOPE-LABEL regression: compact dashboard "
        "metric bullet is no longer scope-explicit ``Total Support "
        "Cases (90d, portfolio-wide)``."
    )


def test_compact_aag_no_longer_emits_bare_support_cases_metric_label() -> None:
    """The pre-Round-49 ambiguous ``"Support Cases"`` literal used as
    a ``format_metric_with_source`` label MUST no longer appear in
    ``compact_report_formatter.py``.  We bound the regex to the
    metric_facts call site -- the source-citation paragraph at
    ``get_data_sources_paragraph_text`` legitimately uses the bare
    phrase ``Support Cases:`` to label the data source category."""
    src = (_REPO_ROOT / "compact_report_formatter.py").read_text()
    assert (
        'format_metric_with_source("Support Cases", total_support_cases'
        not in src
    ), (
        "Round 49 / F-COMP-AAG-SCOPE-LABEL regression: compact dashboard "
        "still calls format_metric_with_source with the ambiguous bare "
        "``Support Cases`` label."
    )


# ---------------------------------------------------------------------------
# End-to-end exercise via the docx renderer
# ---------------------------------------------------------------------------


def _build_minimal_dashboard_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tiny but valid AB / CSOne frames so add_at_a_glance_dashboard can
    render without external services."""
    ab_data = pd.DataFrame(
        [
            {
                "ID": "ab1",
                "BU_NAME": "Acme",
                "STATUS_C": "Open",
                "SEVERITY_C": "P2",
                "OPEN_DATE_C": "2026-01-15",
            }
        ]
    )
    csone_data = pd.DataFrame(
        [
            {
                "Case #": "1234",
                "Status": "Closed",
                "Severity": "P3",
                "Customer Name: Customer Name": "Acme",
                "Open Date": "2026-01-10",
                "Closed Date": "2026-01-20",
            }
        ]
    )
    return ab_data, csone_data


def test_compact_dashboard_renders_scope_explicit_labels() -> None:
    """End-to-end: render the at-a-glance dashboard against a tiny
    portfolio and assert the scope-explicit labels reach the docx."""
    from compact_report_formatter import CompactReportFormatter

    ab_data, csone_data = _build_minimal_dashboard_inputs()
    formatter = CompactReportFormatter()
    formatter.add_at_a_glance_dashboard(ab_data, csone_data)
    doc = formatter.doc

    rendered_text = "\n".join(
        para.text for para in doc.paragraphs if para.text
    ) + "\n"
    rendered_text += "\n".join(
        cell.text
        for table in doc.tables
        for row in table.rows
        for cell in row.cells
        if cell.text
    )

    assert "Support Cases (90d, portfolio-wide)" in rendered_text, (
        f"Round 49 / F-COMP-AAG-SCOPE-LABEL regression: scope-explicit "
        f"table header missing from rendered docx.  Rendered: "
        f"{rendered_text!r}"
    )
    assert "Total Support Cases (90d, portfolio-wide):" in rendered_text, (
        f"Round 49 / F-COMP-AAG-SCOPE-LABEL regression: scope-explicit "
        f"metric bullet missing from rendered docx.  Rendered: "
        f"{rendered_text!r}"
    )


def test_compact_dashboard_does_not_emit_ambiguous_bare_support_cases_bullet() -> None:
    """End-to-end: the bullet line must NOT start with the ambiguous
    ``Support Cases:`` (followed by a digit).  The metric bullet
    immediately followed by a colon and a count is the off-by-scope
    surface the audit flagged."""
    import re
    from compact_report_formatter import CompactReportFormatter

    ab_data, csone_data = _build_minimal_dashboard_inputs()
    formatter = CompactReportFormatter()
    formatter.add_at_a_glance_dashboard(ab_data, csone_data)
    doc = formatter.doc

    bullet_text = "\n".join(p.text for p in doc.paragraphs if p.text)
    pattern = re.compile(r"(?m)^Support Cases:\s*\d+")
    assert not pattern.search(bullet_text), (
        f"Round 49 / F-COMP-AAG-SCOPE-LABEL regression: rendered docx "
        f"still has an ambiguous ``Support Cases: <N>`` bullet line.  "
        f"Bullets: {bullet_text!r}"
    )
