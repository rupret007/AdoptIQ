"""Round 73 / Phase 3 (F7): HTML entity decoding for entity-only strings.

Pre-Round 73, the regex helper ``data_normalization.strip_html_from_string``
short-circuited any string without a ``<`` character, so
``"https://example.com/?a=1&amp;b=2"`` and ``"Acme &amp; Beta"`` (no tags
but with ``&amp;``) flowed through to Excel with the literal entity
escape. The column-level helper
``data_normalization.strip_html_from_dataframe`` had a parallel bug --
its column probe only checked for ``<``, so a URL column that contained
only entity-encoded values was skipped entirely.

The Build 46 acceptance audit found Compact / Renewal / Comprehensive /
Leader workbooks all carrying ``&amp;`` in URL cells. This file pins the
fix at three layers:

* ``strip_html_from_string`` accepts entity-only strings and decodes
  them via ``html.unescape``.
* ``strip_html_from_dataframe`` no longer short-circuits columns whose
  only HTML-ish content is entities.
* The BS4-primary ``_strip_html_safe`` path was already correct
  pre-R73 -- a regression here would mean a different bug; the
  parity test below pins both helpers produce the same output for
  entity-only strings so a future re-implementation cannot drift.
"""

from __future__ import annotations

import html
import importlib

import pytest

dn = importlib.import_module("data_normalization")


# ---------------------------------------------------------------------------
# Source-shape pin: ``strip_html_from_string`` must explicitly probe for
# ``&`` and route entity-only strings through ``html.unescape``.
# ---------------------------------------------------------------------------


def test_source_shape_strip_html_from_string_decodes_entity_only_strings():
    from pathlib import Path

    body = (Path(dn.__file__)).read_text(encoding="utf-8")
    # The R73 / F7 marker comment must be present.
    assert "Round 73 / Phase 3 (F7)" in body, (
        "Round 73 / F7: source marker missing -- entity-only decode may "
        "have been reverted"
    )
    # The new entity-only short-circuit must be present.
    assert "has_entity = \"&\" in value" in body or "has_entity = '&' in value" in body, (
        "Round 73 / F7: strip_html_from_string must probe for '&' to "
        "detect entity-only strings"
    )


def test_source_shape_strip_html_from_dataframe_probes_for_entity():
    from pathlib import Path

    body = (Path(dn.__file__)).read_text(encoding="utf-8")
    # The dataframe-level fast-path must probe for ``&`` (entity)
    # in addition to ``<`` (tag).
    assert (
        'has_entity = astr.str.contains("&", regex=False, na=False).any()' in body
    ), (
        "Round 73 / F7: strip_html_from_dataframe must probe for '&' so "
        "entity-only columns are not skipped by the column fast-path"
    )


# ---------------------------------------------------------------------------
# Cell-level: ``strip_html_from_string`` runtime contract on entity-only
# strings (no tags, just entities).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Acme &amp; Beta", "Acme & Beta"),
        ("https://example.com/?a=1&amp;b=2", "https://example.com/?a=1&b=2"),
        ("&lt;not a tag&gt;", "<not a tag>"),
        ("non&nbsp;breaking", "non\xa0breaking"),
        # Numeric entity reference.
        ("copyright &#169;", "copyright \u00a9"),
        # Hex numeric entity reference.
        ("euro sign &#x20AC;", "euro sign \u20ac"),
        # Multiple entities.
        ("a &amp; b &amp; c", "a & b & c"),
    ],
)
def test_strip_html_from_string_decodes_entity_only_strings(raw, expected):
    """Entity-only strings (no ``<``) must now be decoded via
    ``html.unescape``."""

    out = dn.strip_html_from_string(raw)
    assert out == expected, (
        f"Round 73 / F7: strip_html_from_string({raw!r}) returned {out!r}, "
        f"expected {expected!r}"
    )


def test_strip_html_from_string_preserves_entity_decoding_when_tags_present():
    """Sanity: the existing tags-with-entities path is unchanged."""

    raw = '<p>Acme &amp; Beta &amp; Gamma</p>'
    out = dn.strip_html_from_string(raw)
    assert out == "Acme & Beta & Gamma"


def test_strip_html_from_string_passes_through_plain_strings():
    """Strings without ``<`` AND without ``&`` are returned unchanged
    (cheap fast-path -- no markup or entities to process)."""

    raw = "Just a plain string with no markup or entities"
    out = dn.strip_html_from_string(raw)
    assert out is raw  # Same object reference -- pure pass-through.


def test_strip_html_from_string_passes_through_non_strings():
    """Non-string inputs are returned unchanged (numeric / None / NaN)."""

    for value in (None, 42, 3.14, [1, 2], {"a": 1}):
        out = dn.strip_html_from_string(value)
        assert out is value


# ---------------------------------------------------------------------------
# Parity pin: the BS4-primary path and the regex fallback must produce
# identical output for entity-only strings.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "Acme &amp; Beta",
        "https://example.com/?a=1&amp;b=2",
        "non&nbsp;breaking",
        "copyright &#169;",
    ],
)
def test_safe_and_regex_paths_produce_identical_output_for_entity_only(raw):
    """``_strip_html_safe`` (BS4 primary) and ``strip_html_from_string``
    (regex fallback) must produce the SAME output for entity-only
    strings -- the BS4 path was already correct pre-R73, the regex
    path now matches it."""

    via_safe = dn._strip_html_safe(raw)
    via_regex = dn.strip_html_from_string(raw)
    expected = html.unescape(raw)
    assert via_safe == expected
    assert via_regex == expected
    assert via_safe == via_regex


# ---------------------------------------------------------------------------
# DataFrame-level: ``strip_html_from_dataframe`` must NOT skip columns
# whose only HTML-ish content is entities.
# ---------------------------------------------------------------------------


def test_strip_html_from_dataframe_decodes_entity_only_columns():
    """A column carrying only entity-encoded values (no ``<``) MUST be
    decoded -- pre-R73 the column-level fast-path skipped it."""

    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        {
            "url": [
                "https://example.com/?a=1&amp;b=2",
                "https://other.com/?x=1&amp;y=2",
            ],
            "name": ["Acme &amp; Beta", "Gamma &amp; Delta"],
            "id": [1, 2],
        }
    )
    out = dn.strip_html_from_dataframe(df)
    assert list(out["url"]) == [
        "https://example.com/?a=1&b=2",
        "https://other.com/?x=1&y=2",
    ]
    assert list(out["name"]) == ["Acme & Beta", "Gamma & Delta"]
    # Numeric column untouched.
    assert list(out["id"]) == [1, 2]


def test_strip_html_from_dataframe_still_skips_columns_with_no_html_or_entity():
    """A column with no ``<`` AND no ``&`` is still short-circuited
    (cheap fast-path preserved)."""

    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        {
            "plain": ["one", "two", "three"],
            "n": [1, 2, 3],
        }
    )
    out = dn.strip_html_from_dataframe(df)
    # The plain column passed through unchanged.
    assert list(out["plain"]) == ["one", "two", "three"]


def test_strip_html_from_dataframe_handles_mixed_tag_and_entity_columns():
    """Columns with both tags AND entities are processed correctly."""

    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        {
            "mix": [
                "<p>Acme &amp; Beta</p>",
                "Plain &amp; entity-only",
                "<a href='/x?a=1&amp;b=2'>link</a>",
            ],
        }
    )
    out = dn.strip_html_from_dataframe(df)
    assert list(out["mix"]) == [
        "Acme & Beta",
        "Plain & entity-only",
        "link",
    ]


# ---------------------------------------------------------------------------
# Regression pin: existing R66 dangerous-block stripping is unaffected.
# ---------------------------------------------------------------------------


def test_r66_dangerous_block_stripping_still_works():
    """A R66 regression check: ``<script>`` / ``<style>`` block bodies
    must NOT survive into the cleaned output, even with entities."""

    raw = "before <script>alert(&quot;x&quot;);</script> after"
    out = dn.strip_html_from_string(raw)
    assert "alert" not in out
    assert "before" in out
    assert "after" in out


# ---------------------------------------------------------------------------
# Defensive: malformed entity references must not crash the helpers.
# ---------------------------------------------------------------------------


def test_strip_html_from_string_does_not_crash_on_malformed_entity():
    """``html.unescape`` is permissive -- a literal ``&amp`` (no
    semicolon) is left alone. The helper must not crash."""

    raw = "literal &amp without semicolon"
    out = dn.strip_html_from_string(raw)
    # html.unescape is permissive about missing semicolons; just
    # assert no crash and the string is returned (possibly altered).
    assert isinstance(out, str)
    assert "without semicolon" in out
