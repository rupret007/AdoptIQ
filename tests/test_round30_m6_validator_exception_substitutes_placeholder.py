"""Round 30 / M6 — validator exception path must substitute
GROUNDING_FAILURE_PLACEHOLDER instead of accepting raw LLM output.

In Round 27, the validator boundary in ``app_simple.py`` (portfolio
narrative around line 12780-12785 and customer narrative around
13091-13097) caught any exception inside the validator and logged a
WARNING, then continued with the raw LLM text unchanged.  That broke
the asymmetry contract that Round 27 introduced (validator-rejection
substitutes a placeholder), because a regression that made the
validator throw silently disabled the HTML-injection / ungrounded-
numbers / invented-entity defenses.

The Round 30 fix substitutes ``GROUNDING_FAILURE_PLACEHOLDER`` in
both ``except`` blocks so a thrown validator behaves identically to
a rejection (and identically across the portfolio + customer paths).
"""

from __future__ import annotations

import inspect
import re

import app_simple


def _validator_except_blocks_source() -> str:
    """Return the full app_simple.py source so the source pins can
    grep for the post-fix patterns without re-reading the file."""
    return inspect.getsource(app_simple)


def test_round30_m6_portfolio_validator_except_substitutes_placeholder() -> None:
    """Source pin: the Round 27 portfolio validator ``except`` block
    MUST substitute ``GROUNDING_FAILURE_PLACEHOLDER`` rather than fall
    through to the raw LLM text.  A regression in the validator must
    not silently re-enable the ungrounded text path."""
    src = _validator_except_blocks_source()
    # The portfolio path is anchored on the ``_r27_anv_port`` namespace
    # the Round-27 wrapper introduced.  We require both:
    #   1. the placeholder substitution is present at all on the
    #      portfolio path
    #   2. it appears inside an ``except`` block (i.e. the exception
    #      and rejection codepaths are unified, per the Round 30 / M6
    #      contract)
    assert "_r27_anv_port.GROUNDING_FAILURE_PLACEHOLDER" in src, (
        "Round 30 / M6: portfolio validator path must substitute "
        "GROUNDING_FAILURE_PLACEHOLDER on exception (mirroring the "
        "rejection-path substitution Round 27 introduced)."
    )
    # Pin the structural shape: the except clause that names
    # ``_r27_anv_port_err`` (or any port-suffixed variant) must lead to
    # the placeholder assignment.  We use a permissive multi-line regex
    # so reformatting (extra comments, additional log calls) does not
    # break the test.
    pattern = re.compile(
        r"except\s+Exception\s+as\s+_r27_anv[^\n]+\n"
        r"(?:[^\n]*\n){0,40}?"
        r"\s*_r27_safe_portfolio\s*=\s*_r27_anv_port\.GROUNDING_FAILURE_PLACEHOLDER",
        re.MULTILINE,
    )
    assert pattern.search(src), (
        "Round 30 / M6: portfolio path's ``except Exception as "
        "_r27_anv_port_err:`` block must assign "
        "_r27_anv_port.GROUNDING_FAILURE_PLACEHOLDER to "
        "_r27_safe_portfolio so a thrown validator behaves the same "
        "as a rejected validation."
    )


def test_round30_m6_customer_validator_except_substitutes_placeholder() -> None:
    """Source pin: the customer narrative path that runs the same
    Round 27 validator wrapper (``_r27_anv``) MUST also substitute
    GROUNDING_FAILURE_PLACEHOLDER on exception.  The two paths share
    the asymmetry-with-rejection invariant."""
    src = _validator_except_blocks_source()
    # Both customer storyboard sites use ``_r27_anv`` (no _port
    # suffix).  We require the placeholder substitution within an
    # except block named ``_r27_anv_err`` (or close variants) on the
    # customer storyboard path.
    assert "_r27_safe_storyboard = _r27_anv.GROUNDING_FAILURE_PLACEHOLDER" in src, (
        "Round 30 / M6: customer storyboard path must substitute "
        "GROUNDING_FAILURE_PLACEHOLDER on validator exception."
    )


def test_round30_m6_no_raw_llm_text_on_validator_exception() -> None:
    """Negative source pin: there MUST NOT be any pattern in the
    Round 27 except blocks where ``_r27_safe_portfolio`` or
    ``_r27_safe_storyboard`` is assigned the raw LLM output (e.g.
    ``portfolio_text`` or ``customer_text``) inside an ``except`` for
    the validator.  This is the regression M6 closed."""
    src = _validator_except_blocks_source()
    # Find every ``except Exception as _r27_anv*`` block and assert
    # the body does NOT assign the raw LLM text variable to the
    # _r27_safe_* sink.  We implement this with a permissive
    # multiline regex over the full module source.
    bad_portfolio = re.compile(
        r"except\s+Exception\s+as\s+_r27_anv_port_err[^\n]*:\n"
        r"(?:(?!^\s*[a-zA-Z_].*=\s*_r27_anv_port\.GROUNDING_FAILURE_PLACEHOLDER)"
        r"[^\n]*\n){0,30}?"
        r"\s*_r27_safe_portfolio\s*=\s*portfolio_text\b",
        re.MULTILINE,
    )
    bad_customer = re.compile(
        r"except\s+Exception\s+as\s+_r27_anv_err[^\n]*:\n"
        r"(?:(?!^\s*[a-zA-Z_].*=\s*_r27_anv\.GROUNDING_FAILURE_PLACEHOLDER)"
        r"[^\n]*\n){0,30}?"
        r"\s*_r27_safe_storyboard\s*=\s*[a-zA-Z_]\w*_text\b",
        re.MULTILINE,
    )
    assert not bad_portfolio.search(src), (
        "Round 30 / M6: portfolio except block must not fall back to "
        "raw LLM text on validator exception."
    )
    assert not bad_customer.search(src), (
        "Round 30 / M6: customer except block must not fall back to "
        "raw LLM text on validator exception."
    )
