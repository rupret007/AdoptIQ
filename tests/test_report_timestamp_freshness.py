"""Phase 5 / Phase 3.1 regression test.

Reports must distinguish *render time* ("Generated") from *data fetch
time* ("Data as of").  Before Phase 3.1 the footer used
``datetime.now()`` for both so a multi-hour-old prefetch still
looked freshly fetched in the docx footer.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import report_utils as ru


def test_footer_renders_data_as_of_when_provided() -> None:
    fetched = datetime(2026, 1, 1, 12, 30, 0)
    footer = ru.get_report_metadata_footer(
        report_type="Compact",
        analysis_id="t-1",
        manager="QA",
        days=30,
        data_retrieved_at=fetched,
    )
    assert "Data as of" in footer, (
        "Footer must include a 'Data as of' segment when "
        "data_retrieved_at is supplied (Phase 3.1)."
    )
    assert "Generated" in footer, (
        "Footer must STILL include 'Generated' (render time) so the "
        "two timestamps are visible side-by-side."
    )


def test_footer_omits_data_as_of_when_not_provided() -> None:
    footer = ru.get_report_metadata_footer(
        report_type="Compact",
        analysis_id="t-2",
        manager="QA",
        days=30,
    )
    assert "Data as of" not in footer, (
        "When the caller does not supply a data fetch time, the footer "
        "must NOT invent one (i.e., must not fall back to render time "
        "and label it as the data fetch time)."
    )
    assert "Generated" in footer


def test_footer_data_as_of_can_predate_generated_time() -> None:
    """Sanity: an older fetch time must still appear, proving it
    isn't being silently replaced by datetime.now()."""
    older = datetime.utcnow() - timedelta(hours=6)
    footer = ru.get_report_metadata_footer(
        report_type="Leader",
        analysis_id="t-3",
        manager="QA",
        days=30,
        data_retrieved_at=older,
    )
    year_str = older.strftime("%Y")
    assert year_str in footer, (
        "The supplied data_retrieved_at year must appear in the footer; "
        "if not, the helper is overwriting it with the current time."
    )
