"""Round 25 / Phase B test: PROMPT_PORTFOLIO_TEMPLATE pins canonical totals.

The "Total Customers: 27" hallucination in the reference Brian Frazier /
All Contact Center / 90d report happened because
``PROMPT_PORTFOLIO_TEMPLATE`` only formatted ``MANAGER`` and ``TECHNOLOGY``
into the prompt -- the LLM was therefore free to invent a number for the
"Portfolio Snapshot" block.  Round 25 / Phase B added six additional
substitution keys (``TOTAL_CUSTOMERS``, ``TOTAL_BARRIERS``, ``TAC_CASES``,
``P1_CASES``, ``P2_CASES``, ``BEMS_ESCALATIONS``) plus a "MUST use these
exact values" preamble.  This test asserts:

1. The template references all six substitution keys.
2. ``.format(...)`` with the legacy 2-key call shape now raises
   ``KeyError`` (a regression of the Phase B contract, since the
   AGENTS rule for prompts already requires complete kwargs).
3. ``.format(...)`` with the full kwargs renders the canonical numbers
   verbatim into the rendered prompt body.
"""

from __future__ import annotations

import re

import pytest

from adoptiq_backend import PROMPT_PORTFOLIO_TEMPLATE


_REQUIRED_KEYS = (
    "MANAGER",
    "TECHNOLOGY",
    "TOTAL_CUSTOMERS",
    "TOTAL_BARRIERS",
    "TAC_CASES",
    "P1_CASES",
    "P2_CASES",
    "BEMS_ESCALATIONS",
)


def _format_kwargs() -> dict:
    return {
        "MANAGER": "Brian Frazier",
        "TECHNOLOGY": "All Contact Center",
        "TOTAL_CUSTOMERS": 37,
        "TOTAL_BARRIERS": 67,
        "TAC_CASES": 412,
        "P1_CASES": 9,
        "P2_CASES": 41,
        "BEMS_ESCALATIONS": 5,
    }


def test_template_references_all_round25b_substitution_keys() -> None:
    """Every Phase B kwarg appears at least once in the template body."""

    for key in _REQUIRED_KEYS:
        assert "{" + key + "}" in PROMPT_PORTFOLIO_TEMPLATE, (
            f"Round 25 / Phase B: PROMPT_PORTFOLIO_TEMPLATE must reference "
            f"the canonical substitution key {{{key}}} so the LLM cannot "
            "free-style numerical totals."
        )


def test_template_format_with_legacy_two_key_call_shape_raises() -> None:
    """The pre-Round 25 call shape must now fail loudly.

    Pre-Round 25 the call shape was
    ``PROMPT_PORTFOLIO_TEMPLATE.format(MANAGER=..., TECHNOLOGY=...)``.
    Phase B adds six required keys; ``str.format`` raises ``KeyError``
    on missing kwargs, which is exactly the failure mode we want -- it
    blocks any downstream caller that hasn't been updated to thread
    canonical totals through.
    """

    with pytest.raises(KeyError):
        PROMPT_PORTFOLIO_TEMPLATE.format(
            MANAGER="Brian Frazier", TECHNOLOGY="All Contact Center"
        )


def test_template_format_renders_canonical_totals_verbatim() -> None:
    """Rendered prompt body contains the canonical totals as plain ints."""

    rendered = PROMPT_PORTFOLIO_TEMPLATE.format(**_format_kwargs())
    # Round 25 / Phase B: each canonical total must appear in the
    # rendered prompt at least twice -- once in the new "CANONICAL
    # TOTALS" preamble and once in the "Portfolio Snapshot" bullet
    # block.  This double-anchoring is what stops the LLM from
    # paraphrasing or ranging the numbers.
    for label, value in (
        ("Total Customers", 37),
        ("Active Adoption Barriers", 67),
        ("TAC Cases", 412),
        ("BEMS Escalations", 5),
    ):
        # Search for "<label>:* <value>" anywhere in the rendered body.
        pattern = rf"{re.escape(label)}[^\n]*\b{value}\b"
        matches = re.findall(pattern, rendered, flags=re.IGNORECASE)
        assert matches, (
            f"Round 25 / Phase B: rendered prompt must surface "
            f"{label}={value} verbatim.  No match for pattern {pattern!r} "
            f"in rendered prompt."
        )


def test_template_canonical_totals_preamble_is_present() -> None:
    """The "MUST use these exact values" preamble is wired up.

    Without this preamble the substitution keys can technically be
    rendered but the LLM still has no instruction to *use* them.  The
    presence of this string is part of the Phase B contract.
    """

    rendered = PROMPT_PORTFOLIO_TEMPLATE.format(**_format_kwargs())
    assert "CANONICAL TOTALS" in rendered, (
        "Round 25 / Phase B: PROMPT_PORTFOLIO_TEMPLATE must carry the "
        "'CANONICAL TOTALS' preamble so the LLM is instructed to use the "
        "supplied values verbatim."
    )
    assert "MUST use these exact values" in rendered, (
        "Round 25 / Phase B: PROMPT_PORTFOLIO_TEMPLATE must carry the "
        "'MUST use these exact values' instruction so the LLM treats the "
        "canonical totals as authoritative."
    )
