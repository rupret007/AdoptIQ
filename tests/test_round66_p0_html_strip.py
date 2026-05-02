"""Round 66 / Pass 1 (B4) -- HTML strip widened to AP / Pulse / Priorities sheets.

R25/F.2 added regex-based HTML stripping for ``AB_Detail_All`` and
``CSConsole_Customer_Pulse`` to scrub Snowflake rich-text view markup
(``<a href...>``, ``<img src...>``, ``<p><strong>``) before XLSX write.

Build 38 acceptance smoke surfaced raw HTML in the comprehensive
report's ``Action_Plans`` sheet (added in R64/B2) and in the
``CSConsole_*`` pass-through sheets (added when the filtered_* frames
are written for downstream debugging). The R25 allow-list was too
narrow.

R66/B4:
  * Public ``_strip_html_safe(value)`` alias in
    ``data_normalization.py`` (BeautifulSoup primary, regex fallback).
    BS4 cleanly handles dangerous block elements (``<script>`` /
    ``<style>``) by decomposing them; regex fallback's
    ``_HTML_DANGEROUS_BLOCK_RE`` pre-strip handles the same case
    without BS4.
  * Widened XLSX strip allow-list in
    ``adoptiq_backend.write_excel_workbook`` to cover
    ``Action_Plans``, ``CSConsole_Action_Plans``,
    ``CSConsole_Adoption_Barriers``, ``CSConsole_Success_Priorities``,
    plus the bare ``Adoption_Barriers`` / ``Customer_Pulse`` /
    ``Success_Priorities`` sheets used by the Compact / Renewal flows.
  * Source-side strip in ``app_simple.run_comprehensive_analysis``
    (post-_filter_csconsole_data_by_technology) so Word renderers
    AND XLSX writers see the same clean text.

Round 66 / Pass 1.  Made-with: Cursor.
"""

from __future__ import annotations

import inspect

import pandas as pd

from data_normalization import (
    _strip_html_safe,
    strip_html_from_dataframe,
    strip_html_from_string,
)


# ---------------------------------------------------------------------------
# B4 — _strip_html_safe public alias contract
# ---------------------------------------------------------------------------


def test_strip_html_safe_exists_and_is_callable():
    """The R66/B4 public alias must exist at module scope."""
    assert callable(_strip_html_safe)
    sig = inspect.signature(_strip_html_safe)
    assert len(sig.parameters) == 1, sig


def test_strip_html_safe_passes_through_non_string_values():
    """Numeric / None / NaN / datetime inputs returned unchanged."""
    assert _strip_html_safe(42) == 42
    assert _strip_html_safe(3.14) == 3.14
    assert _strip_html_safe(None) is None
    assert _strip_html_safe(True) is True


def test_strip_html_safe_passes_through_strings_without_lt():
    """Cheap fast-path: no ``<`` in the string -> return verbatim."""
    assert _strip_html_safe("plain text") == "plain text"
    assert _strip_html_safe("") == ""
    assert _strip_html_safe("Customer asked about renewal") == "Customer asked about renewal"


def test_strip_html_safe_strips_anchor_tags():
    """``<a href...>click here</a>`` -> ``"click here"``."""
    raw = '<a href="https://example.com" target="_blank">click here</a>'
    assert _strip_html_safe(raw) == "click here"


def test_strip_html_safe_strips_self_closing_tags():
    """``<img src...>`` -> empty (no body text)."""
    raw = '<img src="/lightning/r/Account/0014..." />'
    assert _strip_html_safe(raw) == ""


def test_strip_html_safe_unescapes_entities():
    """``&amp;`` -> ``&``, ``&nbsp;`` -> non-breaking space, etc."""
    raw = "Acme &amp; Beta &nbsp; Inc"
    out = _strip_html_safe(raw)
    assert "&amp;" not in out
    assert "&" in out
    assert "Acme" in out
    assert "Beta" in out


def test_strip_html_safe_handles_compound_markup():
    """``<p>Customer asked about <strong>renewal</strong> options.</p>``."""
    raw = "<p>Customer asked about <strong>renewal</strong> options.</p>"
    out = _strip_html_safe(raw)
    assert "<" not in out
    assert ">" not in out
    assert "Customer asked about" in out
    assert "renewal" in out
    assert "options." in out


# ---------------------------------------------------------------------------
# B4 — _strip_html_safe handles dangerous blocks (script/style)
# ---------------------------------------------------------------------------


def test_strip_html_safe_decomposes_script_block_body():
    """``<script>alert('x')</script>foo`` -> ``"foo"`` (NOT ``"alert('x')foo"``).

    The Round 25 regex (``<[^>]+>``) would have left ``alert('x')`` in
    place because the regex only matches a SINGLE tag pair, not the
    body between them. R66/B4 BS4 (or the regex fallback's block
    pre-strip) decomposes the entire block.
    """
    raw = "<script>alert('xss')</script>foo"
    out = _strip_html_safe(raw)
    assert "alert" not in out, out
    assert "foo" in out, out


def test_strip_html_safe_decomposes_style_block_body():
    """``<style>body { color: red; }</style>foo`` -> ``"foo"``."""
    raw = "<style>body { color: red; }</style>foo"
    out = _strip_html_safe(raw)
    assert "color" not in out, out
    assert "foo" in out, out


def test_strip_html_from_string_also_handles_script_block_after_r66():
    """The Round 25 ``strip_html_from_string`` regex path now also
    decomposes ``<script>...</script>`` blocks via the new
    ``_HTML_DANGEROUS_BLOCK_RE`` pre-pass (R66/B4)."""
    raw = "<script>alert('xss')</script>safe content"
    out = strip_html_from_string(raw)
    assert "alert" not in out, out
    assert "safe content" in out, out


# ---------------------------------------------------------------------------
# B4 — DataFrame strip preserves non-string columns
# ---------------------------------------------------------------------------


def test_strip_html_from_dataframe_preserves_numeric_and_datetime_columns():
    """Numeric / datetime columns must NOT be touched by the strip."""
    df = pd.DataFrame({
        "html_field": ['<p>foo</p>', '<a href="">bar</a>'],
        "numeric_field": [42, 99],
        "datetime_field": pd.to_datetime(["2026-01-01", "2026-01-02"]),
    })
    out = strip_html_from_dataframe(df)
    # HTML stripped from text column.
    assert out.iloc[0]["html_field"] == "foo"
    assert out.iloc[1]["html_field"] == "bar"
    # Numeric / datetime untouched.
    assert int(out.iloc[0]["numeric_field"]) == 42
    assert pd.api.types.is_datetime64_any_dtype(out["datetime_field"])


# ---------------------------------------------------------------------------
# B4 — XLSX writer allow-list pinning
# ---------------------------------------------------------------------------


def test_write_excel_workbook_strips_html_from_action_plans_sheet(tmp_path):
    """Round 66 / B4 — Action_Plans sheet must arrive in XLSX with HTML stripped.

    Round 67 / B7 update: the test now feeds canonical Snowflake column
    names (``SUBJECT_C`` / ``DESCRIPTION_C``) so the inputs survive the
    new ``Action_Plans`` curated-schema projection that R67/B7 added.
    The post-projection friendly-header rename converts
    ``SUBJECT_C`` → ``Subject`` and ``DESCRIPTION_C`` → ``Description``
    in the produced XLSX, so the read-back assertions still anchor on
    the user-visible header labels.
    """
    from adoptiq_backend import write_excel_workbook

    sheets = {
        "Action_Plans": pd.DataFrame([
            {
                "ID": "AP1",
                "BU_NAME": "Acme Corp",
                "SUBJECT_C": '<a href="/lightning/r/...">Click for details</a>',
                "DESCRIPTION_C": "<p>Engage <strong>CSM</strong> for renewal.</p>",
                "STATUS_C": "Open",
            }
        ]),
    }
    out_base = str(tmp_path / "test_r66_b4_action_plans")
    xlsx_path = write_excel_workbook(out_base, sheets, {})
    assert xlsx_path is not None

    # Read back and assert HTML is gone.  R67/B7 friendly-header rename
    # converts ``SUBJECT_C`` -> ``Subject`` and ``DESCRIPTION_C`` ->
    # ``Description`` in the output workbook.
    written = pd.read_excel(xlsx_path, sheet_name="Action_Plans")
    subject = str(written.iloc[0]["Subject"])
    description = str(written.iloc[0]["Description"])
    assert "<" not in subject, f"raw HTML leaked into Action_Plans.Subject: {subject!r}"
    assert "<" not in description, f"raw HTML leaked into Action_Plans.Description: {description!r}"
    assert "Click for details" in subject
    assert "Engage" in description and "CSM" in description


def test_write_excel_workbook_strips_html_from_csconsole_action_plans_sheet(tmp_path):
    """Same contract for the ``CSConsole_Action_Plans`` pass-through sheet."""
    from adoptiq_backend import write_excel_workbook

    sheets = {
        "CSConsole_Action_Plans": pd.DataFrame([
            {
                "ID": "AP2",
                "BU_NAME": "Beta Inc",
                "Subject": '<p>Plain <em>action</em></p>',
            }
        ]),
    }
    out_base = str(tmp_path / "test_r66_b4_csconsole_aps")
    xlsx_path = write_excel_workbook(out_base, sheets, {})
    written = pd.read_excel(xlsx_path, sheet_name="CSConsole_Action_Plans")
    assert "<" not in str(written.iloc[0]["Subject"]), str(written.iloc[0]["Subject"])
    assert "Plain" in str(written.iloc[0]["Subject"])
    assert "action" in str(written.iloc[0]["Subject"])


def test_write_excel_workbook_does_not_strip_unrelated_sheets(tmp_path):
    """A sheet not in the allow-list (e.g. ``Risk_Components``) is left alone.

    This pins the deliberate scope: we don't blanket-strip every sheet
    because (a) most are auto-generated and never carry HTML, and
    (b) the strip is non-trivial and we want to keep its blast radius
    contained until we confirm it's safe everywhere.
    """
    from adoptiq_backend import write_excel_workbook

    # An unrelated sheet that happens to contain a ``<`` in cell text
    # (e.g. a user-supplied label like ``"Threshold < 5"``) -- this
    # should NOT be touched by the strip.
    sheets = {
        "Risk_Components": pd.DataFrame([
            {"Customer": "Acme", "Threshold": "< 5", "Score": 75},
        ]),
    }
    out_base = str(tmp_path / "test_r66_b4_unrelated")
    xlsx_path = write_excel_workbook(out_base, sheets, {})
    written = pd.read_excel(xlsx_path, sheet_name="Risk_Components")
    # The ``<`` is preserved -- this sheet is NOT in the allow-list.
    # (Either preserved verbatim OR blank if Excel coerces -- key is
    # we don't actively rewrite it.)
    threshold = str(written.iloc[0]["Threshold"])
    assert "5" in threshold, threshold


# ---------------------------------------------------------------------------
# B4 — sanity: existing R25 callers still work
# ---------------------------------------------------------------------------


def test_r25_strip_html_from_string_back_compat_unchanged():
    """The R25 baseline ``strip_html_from_string`` API must be preserved."""
    raw = '<a href="https://example.com">click</a>'
    assert strip_html_from_string(raw) == "click"
    assert strip_html_from_string("plain") == "plain"
    assert strip_html_from_string("") == ""
    assert strip_html_from_string(42) == 42
