"""Round 126 / Build 95 (C1, C2, C3) regression tests.

C1 -- embedded prose grade-letter reconciliation
    ``reconcile_embedded_health_grade_phrases`` rewrites standalone grade
    letters that appear in running prose ("necessitating a 'C' grade",
    "a grade of C", "an A grade") to the canonical letter so the narrative
    can never self-contradict the stamped Customer/Portfolio Health line.

C2 -- guard the grade VALUE to {A,B,C,D,F}
    The grade-line value regex widened from ``[A-Fa-f]`` to ``[A-Za-z]`` so a
    hallucinated invalid letter (e.g. "L" truncated from "LOW") on the grade
    line is overwritten by the canonical stamp rather than shipped.

C3 -- named mini-section for withheld narratives (source-shape pin)
    When the R27 gate withholds the per-customer body, the doc emits a NAMED
    mini-section (heading + factual lines + deterministic grade) instead of a
    bare orphaned placeholder.
"""

from source_shape_utils import assert_in_source
import re

import adoptiq_backend as backend


# ---------------------------------------------------------------------------
# C2 -- stamp overwrites an invalid hallucinated letter
# ---------------------------------------------------------------------------
def test_c2_stamp_overwrites_invalid_letter_L():
    # LLM truncated "LOW" -> "L" on the grade line; canonical band LOW -> "B".
    narrative = "Summary.\nCustomer Health Score: L\nMore prose."
    out = backend.stamp_customer_health_grade(narrative, "B")
    assert "Customer Health Score: B" in out
    assert "Customer Health Score: L" not in out


def test_c2_stamp_overwrites_invalid_letter_Z():
    narrative = "Customer Health Score: Z"
    out = backend.stamp_customer_health_grade(narrative, "A")
    assert out.strip().endswith("Customer Health Score: A")


def test_c2_stamp_is_idempotent_on_canonical():
    narrative = "Customer Health Score: D\nbody"
    once = backend.stamp_customer_health_grade(narrative, "D")
    twice = backend.stamp_customer_health_grade(once, "D")
    assert once == twice
    assert "Customer Health Score: D" in once


def test_c2_portfolio_stamp_overwrites_invalid_letter():
    narrative = "Portfolio Health: M\njustification"
    out = backend.stamp_portfolio_health_grade(narrative, "C")
    assert "Portfolio Health: C" in out
    assert "Portfolio Health: M" not in out


def test_c2_stamp_does_not_touch_multiletter_word():
    # The (?![A-Za-z]) lookahead means a multi-letter word after the colon is
    # NOT treated as a single grade letter, so the stamp inserts/normalizes
    # rather than half-rewriting "Critical" to "Britical".
    narrative = "Customer Health Score: Critical"
    out = backend.stamp_customer_health_grade(narrative, "F")
    assert "Britical" not in out and "Fritical" not in out


# ---------------------------------------------------------------------------
# C1 -- embedded prose grade-letter reconciliation
# ---------------------------------------------------------------------------
def test_c1_rewrites_quoted_letter_grade():
    text = "Risk is elevated, necessitating a 'C' grade for the account."
    out = backend.reconcile_embedded_health_grade_phrases(text, "B")
    assert "'B' grade" in out
    assert "'C' grade" not in out


def test_c1_rewrites_grade_of_letter():
    text = "We assign a grade of C based on the open barriers."
    out = backend.reconcile_embedded_health_grade_phrases(text, "B")
    assert "grade of B" in out
    assert "grade of C" not in out


def test_c1_rewrites_article_letter_grade():
    text = "This warrants a C grade overall."
    out = backend.reconcile_embedded_health_grade_phrases(text, "A")
    assert "a A grade" in out or "an A grade" in out
    assert "a C grade" not in out


def test_c1_idempotent():
    text = "necessitating a 'C' grade"
    once = backend.reconcile_embedded_health_grade_phrases(text, "B")
    twice = backend.reconcile_embedded_health_grade_phrases(once, "B")
    assert once == twice


def test_c1_invalid_canonical_letter_is_noop():
    text = "necessitating a 'C' grade"
    # An out-of-range canonical letter must never corrupt the prose.
    assert backend.reconcile_embedded_health_grade_phrases(text, "Z") == text
    assert backend.reconcile_embedded_health_grade_phrases(text, "") == text
    assert backend.reconcile_embedded_health_grade_phrases(text, None) == text


def test_c1_does_not_touch_unrelated_letters():
    # Names, articles before non-grade words, etc. must be untouched.
    text = "Alice leads a high-touch account with a few open cases."
    out = backend.reconcile_embedded_health_grade_phrases(text, "B")
    assert out == text


def test_c1_does_not_touch_multiletter_band_after_grade_of():
    text = "the grade of LOW does not apply here"
    out = backend.reconcile_embedded_health_grade_phrases(text, "B")
    # "LOW" is multi-letter so the single-letter group + boundary won't match.
    assert "LOW" in out


def test_c1_non_string_input_is_safe():
    assert backend.reconcile_embedded_health_grade_phrases(None, "B") is None
    assert backend.reconcile_embedded_health_grade_phrases("", "B") == ""


# ---------------------------------------------------------------------------
# C3 -- named mini-section for withheld narratives (source-shape pin)
# ---------------------------------------------------------------------------
def _comprehensive_withheld_block() -> str:
    import inspect
    src = inspect.getsource(__import__("app_simple"))
    idx = src.index("Round 126 / Build 95 (C3)")
    return src[idx - 400 : idx + 2800]


def test_c3_withheld_branch_emits_named_heading():
    block = _comprehensive_withheld_block()
    # The withheld branch must add a per-customer heading before the body so
    # the section is a distinct named entry, not an orphan under the prior
    # customer.
    assert_in_source(block, "add_heading(", label='block')
    assert_in_source(block, "Analysis", label='block')


def test_c3_withheld_branch_emits_deterministic_grade_line():
    block = _comprehensive_withheld_block()
    assert_in_source(block, "_r124_deterministic_grade_line", label='block')
    assert_in_source(block, "Customer Health Score:", label='block')


def test_c3_withheld_branch_emits_factual_data_lines():
    block = _comprehensive_withheld_block()
    assert_in_source(block, "Adoption Barriers:", label='block')
    assert_in_source(block, "TAC Cases:", label='block')


# ---------------------------------------------------------------------------
# Source-shape pins -- the widened regex + R126 markers are present
# ---------------------------------------------------------------------------
def test_c2_value_regex_widened_to_full_alpha():
    import inspect
    src = inspect.getsource(backend._build_health_grade_value_re)
    assert_in_source(src, "[A-Za-z]", label='src')
    # The pre-R126 narrow class must be gone from the value position.
    assert "([A-Fa-f])" not in src


def test_c1_helper_exported_and_callable():
    assert callable(backend.reconcile_embedded_health_grade_phrases)


def test_r126_markers_present_in_app_simple():
    import inspect
    src = inspect.getsource(__import__("app_simple"))
    assert_in_source(src, "Round 126 / Build 95 (C1)", label='src')
    assert_in_source(src, "Round 126 / Build 95 (C3)", label='src')
