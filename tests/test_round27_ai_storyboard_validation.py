"""Round 27 / R27-AI-GATE-* -- ai_narrative_validator wiring tests.

Closes the two HIGH AI-grounding findings surfaced by the Round 26
post-mortem recon:

* **F5.2 (HIGH).** ``customer_storyboard = generate_llm_response(...)``
  at ``app_simple.py:~12931`` previously flowed straight to
  ``report_builder.parse_ai_output_and_add(...)`` with NO call to
  ``ai_narrative_validator.validate_narrative``.  This was the
  high-volume per-customer hallucination vector left open by Round 16
  (which only wired the validator into the executive
  ``ai_insights_raw`` path at ``app_simple.py:6975-7018``).

* **F5.1 (MEDIUM partial).** ``portfolio_summary = generate_llm_response(...)``
  at ``app_simple.py:~12609`` already had the Round 25 / Phase B
  numeric-drift validator and the Phase C risk-band validator, but
  lacked the entity / HTML-injection / claim-citation coverage
  ``validate_narrative`` provides.

This file pins:

1. **Source-shape**: the new R27 wiring exists in ``app_simple.py`` --
   the gate markers, the ``validate_narrative`` call, the
   ``GROUNDING_FAILURE_PLACEHOLDER`` substitution, the
   ``ADOPTIQ_R27_LEGACY_AI_GATE`` opt-out flag, and the defensive
   ``try/except`` mirror of Round 16's pattern.
2. **Behavioral**: with the wiring shape these tests describe, the
   validator catches invented entities, ungrounded numbers, HTML/JS
   injection -- the three failure modes the recon flagged.
3. **Asymmetry vs R25B/R25C**: the R27 portfolio gate substitutes a
   placeholder (does NOT raise), unlike R25B/R25C numeric drift which
   raises ``ValueError`` and blocks the build.  This is documented in
   the source comment and pinned here so a future round doesn't
   accidentally upgrade R27 to raise (which would change behavior).

Tests are deterministic and offline.  No live LLM calls.
"""
from __future__ import annotations

from pathlib import Path

import ai_narrative_validator as anv


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_app_simple() -> str:
    return (_REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-shape pins -- the R27 wiring exists at both call sites.
# ---------------------------------------------------------------------------


def test_r27_customer_gate_marker_present_in_app_simple():
    """The customer_storyboard call site must carry the
    ``Round 27 / R27-AI-GATE-CUSTOMER`` marker so
    ``git diff app_simple.py | grep 'Round 27'`` shows the per-file
    footprint per the project convention in CLAUDE.md."""
    src = _read_app_simple()
    # At least 5 marker comments expected: gate-block heading +
    # safe-narrative variable + legacy-flag flag + import-line +
    # final parse_ai_output_and_add line.  Floor of 5 keeps a
    # future polish round from accidentally stripping the markers.
    count = src.count("Round 27 / R27-AI-GATE-CUSTOMER")
    assert count >= 5, (
        f"Expected at least 5 'Round 27 / R27-AI-GATE-CUSTOMER' markers; "
        f"found {count}"
    )


def test_r27_portfolio_gate_marker_present_in_app_simple():
    """The portfolio_summary call site must carry the
    ``Round 27 / R27-AI-GATE-PORTFOLIO`` marker."""
    src = _read_app_simple()
    count = src.count("Round 27 / R27-AI-GATE-PORTFOLIO")
    assert count >= 5, (
        f"Expected at least 5 'Round 27 / R27-AI-GATE-PORTFOLIO' markers; "
        f"found {count}"
    )


def test_r27_customer_gate_calls_validate_narrative_with_briefing_and_entities():
    """The customer-storyboard wiring must pass BOTH
    ``customer_briefing`` and an ``allowed_entities`` set into
    ``validate_narrative``.  The four-slot allowlist (customer name,
    cssm, technology, manager) mirrors the prompt's substitution
    placeholders and is what catches an invented customer name."""
    src = _read_app_simple()
    # The validate_narrative call inside the customer block must
    # reference customer_briefing as the briefing arg.
    assert "validate_narrative(\n                                customer_storyboard,\n                                customer_briefing,\n                                allowed_entities=_r27_allowed_cust," in src, (
        "customer_storyboard validate_narrative call signature drifted"
    )
    # The allowlist must contain all four prompt-template substitutions.
    for entity_slot in (
        "customer_name",
        "cssm_name",
        "specific_technology",
        "status.get('manager')",
    ):
        assert entity_slot in src, f"missing entity slot: {entity_slot}"


def test_r27_portfolio_gate_calls_validate_narrative_with_briefing():
    """The portfolio-summary wiring must pass ``portfolio_briefing``
    to ``validate_narrative`` so entity / number checks anchor on the
    same briefing the LLM saw, not on a re-derived briefing that could
    drift.

    Round 30 / H4 update: the allowlist is passed *directly* now.  The
    earlier ``_r27_allowed_port if _r27_allowed_port else None``
    fallback caused the gate to fail OPEN on an empty entity allowlist
    (the empty set collapsed to ``None`` which ``validate_narrative``
    treats as "skip the entity check entirely").  Post-R30 the call
    site must pass the set directly, so an empty allowlist fails CLOSED
    (every candidate flagged as invented).
    """
    src = _read_app_simple()
    assert "validate_narrative(\n                            portfolio_summary,\n                            portfolio_briefing,\n                            allowed_entities=_r27_allowed_port,\n                        )" in src, (
        "portfolio_summary validate_narrative call signature drifted"
    )
    # Negative pin: the pre-R30 ``or None`` fallback must NOT come back.
    assert (
        "allowed_entities=_r27_allowed_port if _r27_allowed_port else None"
        not in src
    ), (
        "Round 30 / H4 regression: portfolio gate restored the "
        "fail-OPEN ``or None`` fallback that empty-set-collapses to "
        "``None`` and skips the entity check entirely."
    )


def test_r27_substitutes_grounding_failure_placeholder_on_failure():
    """Both call sites must substitute ``GROUNDING_FAILURE_PLACEHOLDER``
    (NOT silently swallow the failure or ship the LLM text anyway)."""
    src = _read_app_simple()
    # Customer gate substitution.
    assert "_r27_safe_storyboard = _r27_anv.GROUNDING_FAILURE_PLACEHOLDER" in src
    # Portfolio gate substitution.
    assert "_r27_safe_portfolio = _r27_anv_port.GROUNDING_FAILURE_PLACEHOLDER" in src
    # And the ``parse_ai_output_and_add`` call that ships to the report
    # must read from the safe variable, not the raw LLM text.
    assert "report_builder.parse_ai_output_and_add(_r27_safe_storyboard)" in src
    assert "report_builder.parse_ai_output_and_add(_r27_safe_portfolio)" in src


def test_r27_legacy_flag_opts_out_at_both_sites():
    """``ADOPTIQ_R27_LEGACY_AI_GATE=1`` must be honored at BOTH call
    sites so an emergency hotfix can disable the gate without a code
    deploy.  Single env var keeps the operator surface minimal."""
    src = _read_app_simple()
    # Two distinct flag-read sites (one per gate).
    assert src.count("ADOPTIQ_R27_LEGACY_AI_GATE") >= 2, (
        "Expected two ADOPTIQ_R27_LEGACY_AI_GATE reads (customer + portfolio gate)"
    )
    # Both gates short-circuit when the flag is truthy.
    assert "if not _r27_legacy_gate_cust:" in src
    assert "if not _r27_legacy_gate_port:" in src


def test_r27_validator_import_failure_does_not_break_report():
    """A validator regression (import failure, regex bug, anything
    raised inside ``validate_narrative``) must NEVER break the report
    pipeline.  Both gates must wrap the validator call in
    ``try/except Exception``.

    Round 30 / M6 update: the original Round-27 contract was
    "exception != rejection -- accept the LLM text as-is on validator
    failure."  That asymmetry silently disables ALL validator checks
    (HTML injection, ungrounded numbers, invented entities) on a
    validator regression.  Post-R30 the contract is symmetric:
    exception is treated identically to a rejection -- both paths
    substitute ``GROUNDING_FAILURE_PLACEHOLDER``.  This test now pins
    the symmetric behavior.
    """
    src = _read_app_simple()
    # Both gates must catch any exception and log a warning.
    for marker in (
        "except Exception as _r27_anv_err:",
        "except Exception as _r27_anv_port_err:",
    ):
        assert marker in src, f"missing defensive except: {marker}"
    # Round 30 / M6: both except blocks must substitute the placeholder
    # rather than accept the LLM output.  The handler narrates the
    # change with "exception treated as rejection" or
    # "substituting placeholder".
    assert src.count("Round 30 / M6") >= 2, (
        "Expected at least two 'Round 30 / M6' markers (one per "
        "validator-exception block) so the post-R30 contract is "
        "discoverable by future readers"
    )
    # The pre-R30 "accepting LLM output as-is" fallback must not come
    # back at the R27 portfolio/customer gate sites.  (Round 16's
    # ai_insights_raw path still uses that older language and may
    # contribute one occurrence; we only forbid the R27 sites here.)
    for r27_block_anchor in (
        "Round 27 / R27-AI-GATE-PORTFOLIO: validator",
        "Round 27 / R27-AI-GATE-CUSTOMER: validator",
    ):
        if r27_block_anchor in src:
            block_start = src.index(r27_block_anchor)
            block = src[block_start:block_start + 2000]
            assert "accepting LLM output as-is" not in block, (
                f"Round 30 / M6 regression near '{r27_block_anchor}': "
                "the validator-exception path reverted to accepting the "
                "LLM text as-is; it must substitute the placeholder."
            )


def test_r27_portfolio_gate_does_not_raise_unlike_r25b_r25c():
    """R25B/R25C numeric+risk-band drift validators ``raise`` (block
    the build).  The R27 entity/injection gate substitutes a
    placeholder and continues.  Pin this asymmetry so a future
    cleanup round doesn't accidentally upgrade R27 to ``raise`` --
    which would change behavior on an entity false-positive (e.g. a
    legitimate customer alias not in the allowlist) by killing the
    entire report rather than just labeling that one section."""
    src = _read_app_simple()
    # The R25B/R25C block raises on numeric drift.
    assert "raise" in src.split("[R25B/R25C] Portfolio numeric or risk-band drift detected; blocking report build")[1].split("# Round 27 / R27-AI-GATE-PORTFOLIO")[0], (
        "R25B/R25C raise on numeric drift unexpectedly removed"
    )
    # The R27 portfolio gate body must NOT contain a bare ``raise``.
    # Slice the source to just the R27 portfolio gate region.
    r27_block_start = src.index("# Round 27 / R27-AI-GATE-PORTFOLIO")
    # End at the ``parse_ai_output_and_add`` line that ships the safe value.
    r27_block_end = src.index(
        "report_builder.parse_ai_output_and_add(_r27_safe_portfolio)",
        r27_block_start,
    )
    r27_block = src[r27_block_start:r27_block_end]
    # The block may contain "raise" in unrelated source comments
    # (e.g. "the validator raised unexpectedly"); we only fail if a
    # bare ``raise`` statement appears.  Grep for the statement form.
    bad_lines = [
        ln
        for ln in r27_block.splitlines()
        if ln.strip() == "raise" or ln.strip().startswith("raise ")
    ]
    assert not bad_lines, (
        f"R27 portfolio gate must not raise (asymmetry vs R25B/R25C); "
        f"found bare raise statements: {bad_lines}"
    )


# ---------------------------------------------------------------------------
# Behavioral pins -- the validator catches the failure modes the recon
# flagged when called with the shape the R27 gates use.
# ---------------------------------------------------------------------------


def test_validate_narrative_rejects_invented_customer_in_storyboard():
    """The customer-storyboard allowed-entity set is
    ``{customer_name, cssm_name, specific_technology, manager}``.  An
    LLM response naming a customer NOT in that set must fail
    ``validate_narrative`` so the gate substitutes the placeholder."""
    briefing = (
        "Customer: Acme Corp\n"
        "CSSM: Jane Doe\n"
        "Technology: Webex Calling\n"
        "Manager: John Smith\n"
        "Open cases: 3\n"
    )
    allowed = {"Acme Corp", "Jane Doe", "Webex Calling", "John Smith"}
    # LLM hallucinates a different customer ("Beta Industries") -- a
    # name not present in the briefing or the allowlist.  This is the
    # exact F5.2 scenario the recon flagged.
    storyboard = (
        "Acme Corp has 3 open cases. We also recommend reviewing "
        "Beta Industries Holdings Inc which has shown similar patterns."
    )
    result = anv.validate_narrative(
        storyboard, briefing, allowed_entities=allowed,
    )
    assert not result.is_valid, (
        "validate_narrative failed to catch invented entity 'Beta Industries Holdings Inc'"
    )
    # The failure should mention an entity-related rule.
    assert any(
        "entity" in failure.lower() or "invented" in failure.lower()
        for failure in result.failures
    ), f"failures missing entity reason: {result.failures}"


def test_validate_narrative_rejects_html_injection_in_portfolio_summary():
    """An LLM response containing a ``<script>`` tag must fail
    validation -- the portfolio gate substitutes the placeholder and
    the report ships clean."""
    briefing = "Manager: Demo Manager\nPortfolio: 5 customers\n"
    portfolio_summary_text = (
        "<script>alert('xss')</script>The portfolio has 5 customers."
    )
    result = anv.validate_narrative(portfolio_summary_text, briefing)
    assert not result.is_valid
    assert any("html" in f.lower() or "injection" in f.lower() for f in result.failures)


def test_validate_narrative_rejects_ungrounded_number_swap():
    """If the briefing says 27 customers and the LLM writes 50,
    ``validate_grounded_numbers`` must reject -- the per-customer and
    per-portfolio gates both depend on this catch.  The ``50`` value
    is deliberately above the ``_COMMON_REFERENCE_NUMBERS`` permissive
    list's ``50`` entry to keep the test resilient to the Round 29
    planned tightening of that whitelist (which will remove ``50``)."""
    briefing = "Manager: Demo Manager\nTotal Customers: 27\nOpen Cases: 14\n"
    # LLM swaps the headline customer count -- exact F5.1 scenario.
    # Use 137 to dodge the current ``_COMMON_REFERENCE_NUMBERS``
    # whitelist (which still contains 50/75/100 etc. -- to be tightened
    # in Round 29 per the deferred R27 follow-ups).
    narrative = (
        "Manager Demo Manager oversees a portfolio of 137 customers "
        "with 14 open cases."
    )
    result = anv.validate_narrative(narrative, briefing)
    assert not result.is_valid
    assert any("number" in f.lower() or "groun" in f.lower() for f in result.failures)


def test_validate_narrative_accepts_well_grounded_storyboard():
    """A storyboard that names only allowed entities, uses only
    briefing numbers, and has no injection content must pass --
    pinning the false-positive rate so the gate doesn't reject
    well-formed narratives."""
    briefing = (
        "Customer: Acme Corp\n"
        "CSSM: Jane Doe\n"
        "Technology: Webex Calling\n"
        "Manager: John Smith\n"
        "Open cases: 3\n"
    )
    allowed = {"Acme Corp", "Jane Doe", "Webex Calling", "John Smith"}
    storyboard = (
        "Acme Corp has 3 open cases against Webex Calling. CSSM Jane "
        "Doe should engage with the customer this quarter."
    )
    result = anv.validate_narrative(
        storyboard, briefing, allowed_entities=allowed,
    )
    assert result.is_valid, (
        f"clean storyboard rejected; failures={result.failures!r}"
    )


def test_validate_narrative_substitutes_with_grounding_failure_placeholder():
    """The placeholder string the R27 gates substitute on failure must
    stay stable so the user-visible failure message doesn't drift
    silently."""
    expected = (
        "AI insight could not be grounded against the source data and was "
        "withheld from this report. The underlying KPIs in the data tabs "
        "remain authoritative."
    )
    assert anv.GROUNDING_FAILURE_PLACEHOLDER == expected
