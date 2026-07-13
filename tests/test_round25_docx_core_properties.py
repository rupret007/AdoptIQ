"""Round 25 / Phase E: Word .docx core_properties must identify as AdoptIQ.

Pre-Round 25 every generated ``.docx`` carried python-docx defaults --
``author='python-docx'``, an empty ``title``, and a ``created`` date of
``2013-12-23T08:00:00+00:00`` (the date the python-docx default core
properties part was authored upstream).  When a recipient opened the
document and pulled up File > Properties, the doc looked like a 2013
output of an unrelated library rather than a freshly minted AdoptIQ
analysis.

Phase E stamps ``doc.core_properties`` immediately before
``doc.save(...)`` on both Word builders:

- ``CompactReportFormatter.save_document``
- ``ExecutiveReportBuilder.save``

The stamp must:

- Identify the author as ``"AdoptIQ Executive Report Generator"``.
- Set ``created`` and ``modified`` to ``datetime.now(timezone.utc)`` --
  not the python-docx default 2013 epoch.
- Emit a non-empty ``title`` (with portfolio context when supplied).
- Set ``company`` to ``"AdoptIQ"`` where supported by the python-docx
  release.

This test asserts those guarantees end-to-end on both builders by
saving a minimal document and inspecting the resulting core properties
through python-docx's own Document API (i.e. without parsing core.xml
by hand).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

from docx import Document


def _abs_dt_diff_seconds(a: datetime, b: datetime) -> float:
    """Return the absolute difference (seconds) between two datetimes.

    Both ``a`` and ``b`` are expected to be timezone-aware.  We do not
    coerce naive datetimes here -- python-docx stores ``created`` /
    ``modified`` as UTC-aware datetimes and the test expects the same.
    """

    return abs((a - b).total_seconds())


# ---------------------------------------------------------------------------
# CompactReportFormatter
# ---------------------------------------------------------------------------


def test_compact_report_formatter_stamps_core_properties() -> None:
    """``CompactReportFormatter.save_document`` must replace python-docx defaults.

    We stand up a minimal ``CompactReportFormatter`` instance, save it
    to a temp path, then re-open the resulting .docx via the public
    python-docx Document API and assert the core properties match the
    Phase E contract.  Python-docx defaults that must NOT survive:
    ``author='python-docx'`` and a 2013 ``created`` timestamp.
    """

    from compact_report_formatter import CompactReportFormatter

    formatter = CompactReportFormatter()
    formatter.doc.add_paragraph("Round 25 Phase E smoke")

    before = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "round25_phaseE_compact.docx")
        formatter.save_document(
            out,
            customer_name="Brian Frazier All Contact Center",
            report_subject="AdoptIQ Compact Executive Report - All Contact Center - 90d",
        )
        after = datetime.now(timezone.utc)

        doc = Document(out)
        cp = doc.core_properties

        assert cp.author == "AdoptIQ Executive Report Generator", (
            f"Round 25 / Phase E: author must identify AdoptIQ.  Got: "
            f"{cp.author!r}"
        )
        assert "python-docx" not in (cp.author or ""), (
            "Round 25 / Phase E regression: python-docx default author "
            "leaked into the .docx core properties."
        )
        assert cp.last_modified_by == "AdoptIQ Executive Report Generator", (
            f"Round 25 / Phase E: last_modified_by must also identify "
            f"AdoptIQ.  Got: {cp.last_modified_by!r}"
        )

        assert cp.title and "Brian Frazier" in cp.title, (
            f"Round 25 / Phase E: title must include the portfolio "
            f"customer name when provided.  Got: {cp.title!r}"
        )
        # Title must include the report date (YYYY-MM-DD) so the
        # Properties dialog shows when the file was generated.
        today_iso = before.strftime("%Y-%m-%d")
        tomorrow_iso = (before + timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_iso = (before - timedelta(days=1)).strftime("%Y-%m-%d")
        assert any(d in cp.title for d in (today_iso, tomorrow_iso, yesterday_iso)), (
            f"Round 25 / Phase E: title must embed the report date.  "
            f"Got: {cp.title!r}"
        )

        # ``created`` must be within the (before, after) window we
        # bracketed around the save call -- definitely NOT the
        # python-docx default 2013-12-23 epoch.
        assert cp.created is not None, (
            "Round 25 / Phase E: created timestamp must be set."
        )
        assert cp.created.year >= before.year - 1, (
            f"Round 25 / Phase E regression: ``created`` looks like the "
            f"python-docx default 2013 epoch.  Got: {cp.created!r}"
        )
        assert cp.created.year != 2013, (
            f"Round 25 / Phase E: ``created`` must not be the "
            f"python-docx 2013 default.  Got: {cp.created!r}"
        )
        # Tolerate up to 60s clock drift on slow CI.
        assert _abs_dt_diff_seconds(
            cp.created.replace(tzinfo=cp.created.tzinfo or timezone.utc),
            before,
        ) < 120, (
            f"Round 25 / Phase E: ``created`` ({cp.created!r}) is more "
            f"than 120s from the test's bracket {before!r}."
        )

        assert cp.modified is not None, (
            "Round 25 / Phase E: modified timestamp must be set."
        )
        assert cp.modified.year >= before.year - 1, (
            f"Round 25 / Phase E regression: ``modified`` looks like a "
            f"python-docx default.  Got: {cp.modified!r}"
        )

        # Comments + category + subject are non-empty so the .docx
        # carries a self-describing identity in the Properties dialog.
        assert cp.comments and "AdoptIQ" in cp.comments, (
            f"Round 25 / Phase E: comments must mention AdoptIQ.  Got: "
            f"{cp.comments!r}"
        )
        assert cp.subject, "Round 25 / Phase E: subject must be non-empty."


def test_compact_report_formatter_default_title_when_no_customer_name() -> None:
    """Without a customer_name we still emit a non-empty, AdoptIQ-branded title."""

    from compact_report_formatter import CompactReportFormatter

    formatter = CompactReportFormatter()
    formatter.doc.add_paragraph("Round 25 Phase E default-title smoke")

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "round25_phaseE_default_title.docx")
        formatter.save_document(out)  # no customer_name

        doc = Document(out)
        cp = doc.core_properties

        assert cp.title, (
            "Round 25 / Phase E: a non-empty default title must be "
            "emitted even when no customer name is supplied."
        )
        assert "AdoptIQ" in cp.title, (
            f"Round 25 / Phase E: default title must reference AdoptIQ.  "
            f"Got: {cp.title!r}"
        )


# ---------------------------------------------------------------------------
# ExecutiveReportBuilder
# ---------------------------------------------------------------------------


def test_executive_report_builder_stamps_core_properties() -> None:
    """``ExecutiveReportBuilder.save`` must stamp the same AdoptIQ identity.

    The comprehensive builder is the one wired into ``app_simple.py``'s
    ``run_comprehensive_analysis`` path; if it slips we ship "AdoptIQ"
    Word reports authored by python-docx.
    """

    from executive_report_builder import ExecutiveReportBuilder

    builder = ExecutiveReportBuilder()
    builder.doc.add_paragraph("Round 25 Phase E comprehensive smoke")

    before = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "round25_phaseE_comprehensive.docx")
        builder.save(
            out,
            customer_name="Brian Frazier All Contact Center",
            report_subject="AdoptIQ Comprehensive Executive Report - All Contact Center - 90d",
        )

        doc = Document(out)
        cp = doc.core_properties

        assert cp.author == "AdoptIQ Executive Report Generator"
        assert cp.last_modified_by == "AdoptIQ Executive Report Generator"
        assert "python-docx" not in (cp.author or "")
        assert cp.title and "Brian Frazier" in cp.title
        assert cp.created is not None
        assert cp.created.year != 2013
        assert _abs_dt_diff_seconds(
            cp.created.replace(tzinfo=cp.created.tzinfo or timezone.utc),
            before,
        ) < 120
        assert cp.modified is not None
        assert cp.modified.year != 2013
        assert cp.comments and "AdoptIQ" in cp.comments


def test_executive_report_builder_save_works_without_optional_args() -> None:
    """Phase E is backwards-compatible: ``builder.save(path)`` keeps working.

    The ``customer_name`` / ``report_subject`` arguments are optional;
    callers that haven't been updated yet must continue to land a
    valid, AdoptIQ-branded .docx without error.
    """

    from executive_report_builder import ExecutiveReportBuilder

    builder = ExecutiveReportBuilder()
    builder.doc.add_paragraph("Round 25 Phase E backwards-compat smoke")

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "round25_phaseE_compat.docx")
        builder.save(out)  # no kwargs

        doc = Document(out)
        cp = doc.core_properties

        assert cp.author == "AdoptIQ Executive Report Generator"
        assert cp.title  # non-empty default
        assert "AdoptIQ" in cp.title
        assert cp.created is not None
        assert cp.created.year != 2013
