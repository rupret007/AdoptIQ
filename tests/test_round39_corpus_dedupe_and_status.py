"""Round 39 / Phase 3 — Historical Context corpus rendering fixes.

Three issues from the audit:

  1. Stale "Sourced from the SharePoint share..." text in the
     rendered docx (Round 36 retired SharePoint runtime; the OneDrive
     desktop client + local sync is the only path now).
  2. ``status?`` / ``sev?`` literal placeholders rendered when fields
     are missing.
  3. Same case_number printed multiple times per customer because the
     corpus loader returns the case once per source snapshot.
  4. Case numbers printed as floats: ``1141876078.0`` (NaN-coerced
     pandas column dtype leaking through).

Round 39 / Phase 3 fixes:

  * Replace SharePoint paragraph with OneDrive-only wording.
  * Render em-dash (``\u2014``) for missing severity / status.
  * ``_dedupe_cases`` collapses by ``case_number`` keeping the
    most-recent record by ``opened_at``.
  * ``_coerce_case_number`` strips trailing ``.0`` so floats render
    as integer strings.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _coerce_case_number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("1141876078.0", "1141876078"),
    (1141876078.0, "1141876078"),
    ("1141876078", "1141876078"),
    ("CASE-12345", "CASE-12345"),
    ("", ""),
    (None, ""),
])
def test_coerce_case_number(raw, expected):
    from report_corpus_context import _coerce_case_number
    assert _coerce_case_number(raw) == expected


# ---------------------------------------------------------------------------
# _dedupe_cases
# ---------------------------------------------------------------------------


def _case(num, opened="2026-01-01", sev="High", status="Open"):
    from report_corpus_context import HistoricalCase
    return HistoricalCase(
        case_number=num,
        severity=sev,
        status=status,
        summary="x",
        opened_at=opened,
        closed_at="",
    )


def test_dedupe_by_case_number_keeps_most_recent():
    from report_corpus_context import _dedupe_cases
    cases = (
        _case("1141876078", opened="2026-01-01"),
        _case("1141876078", opened="2026-03-15"),  # most recent
        _case("1141876078", opened="2026-02-10"),
        _case("ANOTHER", opened="2026-01-05"),
    )
    out = _dedupe_cases(cases)
    assert len(out) == 2, (
        f"Round 39 / Phase 3.3: expected 2 unique cases after dedupe, "
        f"got {len(out)}: {[c.case_number for c in out]}"
    )
    # The kept 1141876078 record must be the 2026-03-15 one.
    kept = [c for c in out if c.case_number == "1141876078"]
    assert len(kept) == 1
    assert kept[0].opened_at == "2026-03-15"


def test_dedupe_preserves_cases_without_case_number():
    """Cases that lack a case_number must NOT be dropped silently --
    we don't have a key to compare them on."""
    from report_corpus_context import _dedupe_cases
    cases = (
        _case("", opened="2026-01-01"),
        _case("", opened="2026-01-02"),
        _case("CASE-1", opened="2026-01-03"),
    )
    out = _dedupe_cases(cases)
    assert len(out) == 3


# ---------------------------------------------------------------------------
# Render text — placeholders + SharePoint removal.
# ---------------------------------------------------------------------------


class _FakeDoc:
    """Minimal duck-typed Document for ``render_to_word``."""

    def __init__(self):
        self.paragraphs = []
        self.headings = []

    def add_heading(self, text, level=1):
        self.headings.append((text, level))
        return self

    def add_paragraph(self, text=""):
        self.paragraphs.append(text)
        return self

    def all_text(self):
        return "\n".join(self.headings_text() + self.paragraphs)

    def headings_text(self):
        return [t for t, _ in self.headings]


def _ctx_with_one_case():
    from report_corpus_context import (
        HistoricalCase, HistoricalContext, HistoricalEntry,
    )
    case = HistoricalCase(
        case_number="1141876078",
        severity="",  # missing -> em-dash, not "sev?"
        status="",    # missing -> em-dash, not "status?"
        summary="laptop won't connect to webex",
        opened_at="2026-03-01",
        closed_at="",
    )
    entry = HistoricalEntry(
        customer_name="Acme Corp",
        technology="Webex Calling",
        first_seen="2026-01-01",
        last_seen="2026-03-15",
        occurrences=3,
        cases=(case,),
    )
    return HistoricalContext(
        available=True,
        entries=(entry,),
        banner="",
        source_files=("report1.xlsx",),
    )


def test_render_does_not_mention_sharepoint():
    from report_corpus_context import render_to_word
    ctx = _ctx_with_one_case()
    doc = _FakeDoc()
    render_to_word(doc, ctx)
    text = doc.all_text().lower()
    assert "sharepoint" not in text, (
        "Round 39 / Phase 3.1: Round 36 retired the SharePoint runtime "
        "path; the docx must not mention SharePoint."
    )
    # Positive: the new wording mentions OneDrive.
    assert "onedrive" in text, (
        "Round 39 / Phase 3.1: replacement wording should name "
        "OneDrive so the reader knows the actual source."
    )


def test_render_does_not_emit_sev_question_or_status_question():
    from report_corpus_context import render_to_word
    ctx = _ctx_with_one_case()
    doc = _FakeDoc()
    render_to_word(doc, ctx)
    text = doc.all_text()
    assert "sev?" not in text, (
        "Round 39 / Phase 3.2: missing severity must render as "
        "em-dash, not 'sev?'."
    )
    assert "status?" not in text, (
        "Round 39 / Phase 3.2: missing status must render as "
        "em-dash, not 'status?'."
    )


def test_render_dedupes_repeated_case_numbers():
    """Build a context with three identical case numbers -- the
    rendered Prior Cases section must list only one."""
    from report_corpus_context import (
        HistoricalCase, HistoricalContext, HistoricalEntry, render_to_word,
    )

    def _make(opened):
        return HistoricalCase(
            case_number="DUP-1",
            severity="High",
            status="Open",
            summary="x",
            opened_at=opened,
            closed_at="",
        )

    entry = HistoricalEntry(
        customer_name="Acme",
        technology="",
        first_seen="2026-01-01",
        last_seen="2026-03-15",
        occurrences=3,
        cases=(_make("2026-01-01"), _make("2026-02-01"), _make("2026-03-01")),
    )
    ctx = HistoricalContext(
        available=True, entries=(entry,), banner="",
        source_files=("a.xlsx",),
    )
    doc = _FakeDoc()
    render_to_word(doc, ctx)
    text = doc.all_text()
    # Case number "DUP-1" should appear EXACTLY once in the rendered text.
    assert text.count("DUP-1") == 1, (
        f"Round 39 / Phase 3.3: case_number DUP-1 must render once "
        f"after dedupe, got {text.count('DUP-1')} occurrences."
    )


def test_render_coerces_float_case_numbers():
    """A case_number stored as a float (NaN-coerced pandas column)
    must render as an integer string -- no trailing ``.0``."""
    from report_corpus_context import (
        HistoricalCase, HistoricalContext, HistoricalEntry, render_to_word,
    )
    case = HistoricalCase(
        case_number="1141876078.0",  # the leak shape
        severity="High",
        status="Open",
        summary="x",
        opened_at="2026-01-01",
        closed_at="",
    )
    entry = HistoricalEntry(
        customer_name="Acme",
        technology="",
        first_seen="2026-01-01",
        last_seen="2026-01-05",
        occurrences=1,
        cases=(case,),
    )
    ctx = HistoricalContext(
        available=True, entries=(entry,), banner="",
        source_files=("a.xlsx",),
    )
    doc = _FakeDoc()
    render_to_word(doc, ctx)
    text = doc.all_text()
    assert "1141876078" in text
    assert "1141876078.0" not in text, (
        "Round 39 / Phase 3.4: float-shaped case_number must render "
        "as integer string."
    )
