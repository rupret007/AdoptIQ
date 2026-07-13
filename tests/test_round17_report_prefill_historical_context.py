"""Round 17 / Phase F.1 -- Historical-context report prefill.

Pins the contract of :mod:`report_corpus_context`:

- ``build_historical_context`` always returns a
  :class:`HistoricalContext`, never raises.
- Disabled feature flag: returns ``available=False`` with a
  CORPUS_KNOWLEDGE_ENABLED banner.
- Unconfigured corpus: returns ``available=False`` with the
  "connect to OneDrive" banner.
- No customer names supplied: returns ``available=False`` with a
  "no historical context" banner.
- Configured corpus: returns ``available=True`` with one
  :class:`HistoricalEntry` per matched customer.
- Unmatched customer names are surfaced via ``unmatched``.
- ``render_to_text`` and ``render_to_word`` produce stable,
  deterministic output.  ``render_to_word`` is duck-typed so a
  fake doc with ``add_heading`` / ``add_paragraph`` exercises
  every code path.
- Sentiment trend gets compressed into one of
  {'improving', 'declining', 'flat', None}.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import corpus_retriever
import report_corpus_context
from corpus_indexer import index_folder, open_corpus_db
from report_corpus_context import (
    HistoricalCase,
    HistoricalContext,
    HistoricalEntry,
    HistoricalResolution,
    HistoricalTheme,
    build_historical_context,
    render_to_text,
    render_to_word,
)


_FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures" / "round17"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir(parents=True, exist_ok=True)
    for name in ("synthetic_cases.csv", "synthetic_barriers.csv", "synthetic_pulse.csv"):
        (root / name).write_bytes((_FIXTURES_ROOT / name).read_bytes())
    return root


@pytest.fixture
def configured_corpus(tmp_path: Path):
    root = _seed_corpus(tmp_path)
    db_path = tmp_path / "corpus.db"
    conn = open_corpus_db(db_path)
    try:
        index_folder(conn, root)
        corpus_retriever.configure_connection(conn)
        yield conn
    finally:
        corpus_retriever.configure_connection(None)
        try:
            conn.close()
        except sqlite3.DatabaseError:
            pass


class _FakeDoc:
    """Minimal duck-typed substitute for python-docx ``Document``.

    Records every ``add_heading`` / ``add_paragraph`` call so tests
    can assert on rendered structure without dragging in the heavy
    docx round-trip.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def add_heading(self, text: str, *_args, **_kwargs) -> None:
        self.events.append(("heading", str(text)))

    def add_paragraph(self, text: str = "") -> None:
        self.events.append(("paragraph", str(text)))


# ---------------------------------------------------------------------------
# Disabled / unconfigured paths -- never raise.
# ---------------------------------------------------------------------------


def test_build_historical_context_disabled_returns_disabled_banner():
    out = build_historical_context(["Customer A"], enabled=False)
    assert isinstance(out, HistoricalContext)
    assert out.available is False
    assert "CORPUS_KNOWLEDGE_ENABLED" in out.banner
    assert out.entries == ()


def test_build_historical_context_no_corpus_returns_unavailable_banner():
    corpus_retriever.configure_connection(None)
    out = build_historical_context(["Synthetic Alpha"], enabled=True)
    assert out.available is False
    assert "OneDrive" in out.banner
    assert out.entries == ()


def test_build_historical_context_no_names_returns_no_match_banner():
    corpus_retriever.configure_connection(None)
    out = build_historical_context([], enabled=True)
    assert out.available is False
    assert out.banner
    assert out.entries == ()


def test_build_historical_context_drops_empty_and_dedupes_names(configured_corpus):
    out = build_historical_context(
        ["Synthetic Alpha", "  ", None, "Synthetic Alpha", "Synthetic Beta"],
        enabled=True,
    )
    assert out.available is True
    assert len(out.entries) == 2
    names = sorted(e.customer_name for e in out.entries)
    assert names == sorted({"Synthetic Alpha", "Synthetic Beta"})


def test_build_historical_context_unmatched_names_surfaced(configured_corpus):
    out = build_historical_context(
        ["Synthetic Alpha", "Definitely Not A Customer"],
        enabled=True,
    )
    assert out.available is True
    assert "Definitely Not A Customer" in out.unmatched
    assert any(
        e.customer_name.lower().startswith("synthetic alpha")
        for e in out.entries
    )


def test_build_historical_context_caps_max_customers(configured_corpus):
    out = build_historical_context(
        ["Synthetic Alpha", "Synthetic Beta", "Synthetic Gamma"],
        enabled=True,
        max_customers=2,
    )
    assert len(out.entries) <= 2


# ---------------------------------------------------------------------------
# Connected path -- contract.
# ---------------------------------------------------------------------------


def test_historical_entry_is_typed_and_immutable(configured_corpus):
    out = build_historical_context(["Synthetic Alpha"], enabled=True)
    assert out.available is True
    entry = out.entries[0]
    assert isinstance(entry, HistoricalEntry)
    # Frozen dataclass -- attribute mutation must fail.
    with pytest.raises((AttributeError, Exception)):
        entry.customer_name = "mutated"  # type: ignore[misc]
    assert all(isinstance(c, HistoricalCase) for c in entry.cases)
    assert all(isinstance(b, HistoricalTheme) for b in entry.barriers)
    assert all(isinstance(r, HistoricalResolution) for r in entry.resolutions)


def test_source_files_are_unique_and_non_empty(configured_corpus):
    out = build_historical_context(
        ["Synthetic Alpha", "Synthetic Beta"],
        enabled=True,
    )
    if out.source_files:
        assert len(set(out.source_files)) == len(out.source_files)
        assert all(s for s in out.source_files)


def test_sentiment_direction_is_one_of_known_values(configured_corpus):
    out = build_historical_context(
        ["Synthetic Alpha", "Synthetic Beta"],
        enabled=True,
    )
    for entry in out.entries:
        if entry.sentiment_direction is not None:
            assert entry.sentiment_direction in {"improving", "declining", "flat"}


def test_sentiment_direction_helper_handles_short_inputs():
    assert report_corpus_context._sentiment_direction([]) is None
    assert report_corpus_context._sentiment_direction([0.5]) is None
    assert report_corpus_context._sentiment_direction([0.0, 0.0]) == "flat"
    assert report_corpus_context._sentiment_direction([0.0, 1.0]) == "improving"
    assert report_corpus_context._sentiment_direction([1.0, 0.0]) == "declining"


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def test_render_to_text_when_unavailable_includes_banner():
    ctx = HistoricalContext(available=False, banner="custom banner")
    out = render_to_text(ctx)
    assert "Historical Context" in out
    assert "custom banner" in out


def test_render_to_text_includes_customer_data(configured_corpus):
    ctx = build_historical_context(["Synthetic Alpha"], enabled=True)
    out = render_to_text(ctx)
    assert "Historical Context" in out
    assert "Synthetic Alpha" in out
    # No leaked control characters.
    assert "\x00" not in out
    assert "\x07" not in out


def test_render_to_word_emits_heading_and_paragraphs(configured_corpus):
    ctx = build_historical_context(["Synthetic Alpha"], enabled=True)
    doc = _FakeDoc()
    render_to_word(doc, ctx)
    kinds = [k for k, _t in doc.events]
    assert "heading" in kinds
    assert "paragraph" in kinds
    # The first heading is the section title.
    assert doc.events[0] == ("heading", "Historical Context")


def test_render_to_word_unavailable_only_emits_banner():
    ctx = HistoricalContext(available=False, banner="No corpus")
    doc = _FakeDoc()
    render_to_word(doc, ctx)
    # First event must be the section heading; banner must appear.
    assert doc.events[0] == ("heading", "Historical Context")
    assert any(t == "No corpus" for _k, t in doc.events)


def test_render_to_word_handles_doc_without_required_api():
    # An object missing ``add_heading`` / ``add_paragraph`` must
    # silently no-op rather than raise.
    class _NotADoc:
        pass

    ctx = HistoricalContext(available=False, banner="No corpus")
    render_to_word(_NotADoc(), ctx)  # must not raise


# ---------------------------------------------------------------------------
# Defensive helpers
# ---------------------------------------------------------------------------


def test_safe_str_truncates_long_text():
    out = report_corpus_context._safe_str("x" * 5000, limit=64)
    assert len(out) <= 64
    assert out.endswith("\u2026")


def test_safe_str_drops_control_characters():
    out = report_corpus_context._safe_str("a\x00b\x07c\x08d")
    assert "\x00" not in out
    assert "\x07" not in out
    assert "a" in out and "d" in out


def test_safe_str_handles_none():
    assert report_corpus_context._safe_str(None) == ""


def test_is_safe_chunk_handles_validator_missing(monkeypatch):
    # Round 18 / Phase 5.1: contract corrected from fail-open to
    # fail-closed.  If the validator import or call fails, the
    # corpus safety gate now drops the chunk (returns False)
    # rather than letting unverified text through to a Word /
    # Excel report.  The report itself is still not broken --
    # ``_is_safe_chunk`` does not raise, the chunk is simply
    # silently omitted.
    import sys
    import builtins

    real_import = builtins.__import__

    def _fail_validator(name, *args, **kwargs):
        if name == "ai_narrative_validator":
            raise RuntimeError("boom")
        return real_import(name, *args, **kwargs)

    sys.modules.pop("ai_narrative_validator", None)
    monkeypatch.setattr(builtins, "__import__", _fail_validator)
    assert report_corpus_context._is_safe_chunk("safe text") is False
