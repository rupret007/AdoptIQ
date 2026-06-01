"""Round 122 / Build 91 - residual display-sentinel sweep + Ask AI recall.

Three contracts pinned here:

H1 (corpus-context):
    ``report_corpus_context._relabel_corpus_sentinel`` relabels a bare
    ``Other/Unknown``-style sentinel in a Historical-Context theme/technology
    label to ``"Other / Unclassified"`` (the same label R121/G3 uses for the
    BE Focus Areas tables) BUT leaves a blank value blank so an absent
    technology still renders no ``"(...)"`` bracket.  The relabel happens at
    ``build_historical_context`` construction time so BOTH render paths
    (``render_to_text`` + ``render_to_word``) inherit it.

H2 (xlsx export):
    ``data_normalization.relabel_display_sentinels`` rewrites bare sentinels
    in named columns to a display label on a shallow copy -- count-safe (no
    row drop) and canonical-safe (the caller's frame is never mutated).  The
    XLSX export SSoT ``report_export_schema.apply_export_schema`` wires it so
    every workbook writer (Compact/Renewal/Leader/subscription + Comprehensive)
    inherits the relabel: ``case_type_class`` -> "Unclassified",
    ``sub_technology`` / ``ab_category_final`` -> "Other / Unclassified".

Ask AI accuracy:
    The cross-encoder candidate pool widened 30 -> 40
    (``Config.ASK_AI_RERANK_CANDIDATE_K``) with the in-code fallback in
    ``ask_ai_grounded`` kept in parity.  The synthetic eval (lexical mode)
    stays a 75/75 regression guard; the recall uplift is validated on the
    live VPN smoke.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import be_priority_scorer as bes
import report_corpus_context as rcc
from data_normalization import relabel_display_sentinels
from report_export_schema import apply_export_schema


# ---------------------------------------------------------------------------
# H1 - corpus-context sentinel relabel
# ---------------------------------------------------------------------------


# Round 122 / H1
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Other/Unknown", "Other / Unclassified"),
        ("Other / Unknown", "Other / Unclassified"),
        ("unknown", "Other / Unclassified"),
        ("Unknown", "Other / Unclassified"),
        ("  UNKNOWN  ", "Other / Unclassified"),
        ("nan", "Other / Unclassified"),
        ("none", "Other / Unclassified"),
        ("n/a", "Other / Unclassified"),
    ],
)
def test_h1_relabel_corpus_sentinel_maps_sentinels(raw, expected):
    assert rcc._relabel_corpus_sentinel(raw) == expected


# Round 122 / H1
def test_h1_relabel_corpus_sentinel_preserves_blank_and_genuine():
    # Blank stays blank so no spurious "(...)" bracket / header bit.
    assert rcc._relabel_corpus_sentinel("") == ""
    assert rcc._relabel_corpus_sentinel("   ") == "   "
    # A genuine label that merely CONTAINS "unknown" is preserved.
    assert rcc._relabel_corpus_sentinel("Unknown Protocol") == "Unknown Protocol"
    assert rcc._relabel_corpus_sentinel("Webex Calling") == "Webex Calling"


# Round 122 / H1
def test_h1_label_parity_with_be_priority_scorer():
    """The corpus-context label must match the R121/G3 BE-priority label so
    the Historical Context section and the BE Focus Areas tables read the
    same."""
    assert rcc._relabel_corpus_sentinel("Other/Unknown") == bes.relabel_unclassified(
        "Other/Unknown"
    )
    assert rcc._relabel_corpus_sentinel("unknown") == bes.relabel_unclassified("unknown")


# Round 122 / H1
def _fake_history_with_sentinels():
    """A corpus_retriever-shaped history whose technology/theme carry the
    raw ``Other/Unknown`` / ``Unknown`` sentinels a pre-R122 corpus stored."""
    return SimpleNamespace(
        name="Acme Corp",
        manager=None,
        technology="Other/Unknown",
        first_seen="2026-01-01",
        last_seen="2026-04-01",
        occurrences=4,
        cases=(),
        barriers=(
            SimpleNamespace(
                technology="Other/Unknown",
                theme="general",
                occurrences=3,
                first_seen="2026-01-01",
                last_seen="2026-03-01",
            ),
            SimpleNamespace(
                technology="Unknown",
                theme="Unknown",
                occurrences=1,
                first_seen="2026-02-01",
                last_seen="2026-02-15",
            ),
        ),
        sentiment_trend=(),
        top_resolutions=(),
    )


# Round 122 / H1
def _build_context_with_fake_corpus(monkeypatch):
    monkeypatch.setattr(rcc, "_is_safe_chunk", lambda *_a, **_k: True, raising=False)

    fake_cr = SimpleNamespace(
        is_configured=lambda: True,
        get_customer_history=lambda *_a, **_k: _fake_history_with_sentinels(),
    )
    import sys

    monkeypatch.setitem(sys.modules, "corpus_retriever", fake_cr)
    return rcc.build_historical_context(["Acme Corp"], technology="Other/Unknown")


# Round 122 / H1
def test_h1_build_historical_context_relabels_at_construction(monkeypatch):
    ctx = _build_context_with_fake_corpus(monkeypatch)
    assert ctx.available is True
    assert len(ctx.entries) == 1
    entry = ctx.entries[0]
    # Entry header technology relabeled.
    assert entry.technology == "Other / Unclassified"
    # Theme technology relabeled at construction.
    techs = {b.technology for b in entry.barriers}
    assert "Other/Unknown" not in techs
    assert "Other / Unclassified" in techs
    # The sentinel theme text "Unknown" is also relabeled; "general" preserved.
    themes = {b.theme for b in entry.barriers}
    assert "general" in themes
    assert "Unknown" not in themes


# Round 122 / H1
def test_h1_render_to_text_inherits_relabel(monkeypatch):
    ctx = _build_context_with_fake_corpus(monkeypatch)
    rendered = rcc.render_to_text(ctx)
    assert "Other/Unknown" not in rendered
    assert "Other / Unclassified" in rendered


# Round 122 / H1
def test_h1_render_to_word_inherits_relabel(monkeypatch):
    class _FakeDoc:
        def __init__(self):
            self.calls: list[str] = []

        def add_heading(self, text, level=1):
            self.calls.append(f"H{level}:{text}")

        def add_paragraph(self, text):
            self.calls.append(f"P:{text}")

    ctx = _build_context_with_fake_corpus(monkeypatch)
    doc = _FakeDoc()
    rcc.render_to_word(doc, ctx)
    rendered = "\n".join(doc.calls)
    assert "Other/Unknown" not in rendered
    assert "Other / Unclassified" in rendered


# Round 122 / H1
def test_h1_blank_technology_renders_no_bracket():
    """A theme with blank technology must render no '(...)' bracket -- the
    relabel must NOT turn a blank into a label."""
    theme = rcc.HistoricalTheme(
        theme=rcc._relabel_corpus_sentinel(""),
        occurrences=2,
        technology=rcc._relabel_corpus_sentinel(""),
    )
    entry = rcc.HistoricalEntry(
        customer_name="Acme",
        technology=rcc._relabel_corpus_sentinel("") or None,
        first_seen=None,
        last_seen=None,
        occurrences=2,
        barriers=(theme,),
    )
    ctx = rcc.HistoricalContext(available=True, banner="", entries=(entry,))
    rendered = rcc.render_to_text(ctx)
    # No relabel leaked into a blank field...
    assert "Other / Unclassified" not in rendered
    # ...and no technology "(...)" bracket was emitted before the "--" dash.
    # (The only "(" in the line is from "occurrence(s)", after the dash.)
    assert ") --" not in rendered
    assert "-- 2 occurrence(s)" in rendered


# ---------------------------------------------------------------------------
# H2 - xlsx display-sentinel relabel
# ---------------------------------------------------------------------------


# Round 122 / H2
def test_h2_relabel_named_columns_only():
    df = pd.DataFrame(
        {
            "case_type_class": ["unknown", "Technical", "UNKNOWN"],
            "sub_technology": ["Other/Unknown", "Webex Calling", "Other / Unknown"],
            "title": ["unknown blocker", "real title", "other/unknown thing"],
        }
    )
    out = relabel_display_sentinels(
        df,
        {"case_type_class": "Unclassified", "sub_technology": "Other / Unclassified"},
    )
    assert list(out["case_type_class"]) == ["Unclassified", "Technical", "Unclassified"]
    assert list(out["sub_technology"]) == [
        "Other / Unclassified",
        "Webex Calling",
        "Other / Unclassified",
    ]
    # An unnamed column is never touched even if it contains a sentinel word.
    assert list(out["title"]) == ["unknown blocker", "real title", "other/unknown thing"]


# Round 122 / H2
def test_h2_count_safe_no_row_drift():
    df = pd.DataFrame({"sub_technology": ["Other/Unknown"] * 17})
    out = relabel_display_sentinels(df, {"sub_technology": "Other / Unclassified"})
    assert len(out) == len(df) == 17


# Round 122 / H2
def test_h2_canonical_frame_not_mutated():
    df = pd.DataFrame(
        {"case_type_class": ["unknown", "Break-Fix"], "keep": [1, 2]}
    )
    snapshot = df.copy(deep=True)
    out = relabel_display_sentinels(df, {"case_type_class": "Unclassified"})
    # Input frame untouched (canonical safe).
    pd.testing.assert_frame_equal(df, snapshot)
    # Output is a distinct, relabeled frame.
    assert out is not df
    assert list(out["case_type_class"]) == ["Unclassified", "Break-Fix"]


# Round 122 / H2
def test_h2_preserves_blank_nan_and_legit_labels():
    df = pd.DataFrame(
        {
            "sub_technology": ["", None, float("nan"), "Uncategorized", "Webex"],
        }
    )
    out = relabel_display_sentinels(df, {"sub_technology": "Other / Unclassified"})
    vals = list(out["sub_technology"])
    assert vals[0] == ""           # blank preserved
    assert vals[1] is None         # None preserved
    assert pd.isna(vals[2])        # NaN preserved
    assert vals[3] == "Uncategorized"  # legit label preserved (not a sentinel)
    assert vals[4] == "Webex"


# Round 122 / H2
def test_h2_absent_columns_and_empty_df_are_noops():
    df = pd.DataFrame({"other": [1, 2]})
    assert relabel_display_sentinels(df, {"sub_technology": "X"}) is df
    empty = pd.DataFrame(columns=["case_type_class"])
    assert relabel_display_sentinels(empty, {"case_type_class": "Unclassified"}) is empty
    assert relabel_display_sentinels(None, {"a": "b"}) is None


# Round 122 / H2
def test_h2_apply_export_schema_relabels_sub_technology():
    """End-to-end: the XLSX export SSoT relabels the raw ``sub_technology``
    sentinel (kept lowercase by the friendly-header pass)."""
    df = pd.DataFrame(
        {
            "sub_technology": ["Other/Unknown", "Webex Calling"],
            "title": ["a", "b"],
        }
    )
    out = apply_export_schema(df, sheet_name=None)
    assert list(out["sub_technology"]) == ["Other / Unclassified", "Webex Calling"]
    # Row count preserved through the export projection.
    assert len(out) == 2


# Round 122 / H2
def test_h2_apply_export_schema_relabels_case_type_class():
    df = pd.DataFrame({"case_type_class": ["unknown", "Break-Fix"], "x": [1, 2]})
    out = apply_export_schema(df, sheet_name=None)
    assert list(out["case_type_class"]) == ["Unclassified", "Break-Fix"]


# Round 122 / H2
def test_h2_apply_export_schema_relabels_renamed_category_column():
    """``ab_category_final`` is renamed to "Barrier Category (Final)" by the
    friendly-header pass; the relabel runs post-rename and must still catch a
    sentinel under the friendly name."""
    df = pd.DataFrame(
        {"ab_category_final": ["Other/Unknown", "Onboarding"], "id": [1, 2]}
    )
    out = apply_export_schema(df, sheet_name=None)
    col = "Barrier Category (Final)"
    assert col in out.columns
    assert list(out[col]) == ["Other / Unclassified", "Onboarding"]


# ---------------------------------------------------------------------------
# Ask AI accuracy - candidate-pool widening + composer seam
# ---------------------------------------------------------------------------


# Round 122 / Ask AI
def test_askai_rerank_candidate_k_widened_to_40():
    from config import Config

    assert Config.ASK_AI_RERANK_CANDIDATE_K == 40


# Round 122 / Ask AI
def test_askai_in_code_fallback_parity_with_config():
    """The in-code fallback in ask_ai_grounded must track the Config default
    (40) so the candidate pool can never silently narrow to the old 30."""
    import pathlib

    src = pathlib.Path("ask_ai_grounded.py").read_text(encoding="utf-8")
    assert "ASK_AI_RERANK_CANDIDATE_K\", 40)" in src
    assert "candidate_k = 40" in src
    # The pre-R122 bare-30 fallbacks must be gone.
    assert "ASK_AI_RERANK_CANDIDATE_K\", 30)" not in src
    assert "candidate_k = 30" not in src


# Round 122 / Ask AI
def test_askai_composer_seam_present():
    import ask_ai_grounded

    assert callable(getattr(ask_ai_grounded, "compose_grounded_answer", None))
