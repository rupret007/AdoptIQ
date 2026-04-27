"""Round 25 / Phase B test: post-render Word numeric drift validator.

Synthetic LLM outputs are fed into ``report_consistency.validate_word_numeric_drift``
to verify:

- Drift between narrated and canonical totals raises ``ValueError`` (or
  populates ``errors``) when the narrative disagrees with the canonical
  pipeline -- this is the gate that blocks
  "Total Customers: 27" while the dashboard tile says 37.
- Agreeing narratives pass cleanly.
- Missing labels in the narrative produce a non-blocking *warning* (so a
  malformed Portfolio Snapshot doesn't silently ship without anyone
  noticing).
- Multiple narrated values for the same label are all checked (so a
  doc that names "Total Customers: 37" once and "Total Customers: 27"
  once still trips the validator).
"""

from __future__ import annotations

import pytest

from report_consistency import validate_word_numeric_drift


_CANONICAL_TOTALS = {
    "total_customers": 37,
    "total_barriers": 67,
    "total_cases": 412,
    "bems_count": 5,
}


def _good_portfolio_snapshot() -> str:
    """Narrative that matches the canonical totals exactly."""
    return (
        "## Executive Summary: What's Really Happening\n"
        "\n"
        "Portfolio Snapshot:\n"
        "- Total Customers: 37\n"
        "- Active Adoption Barriers: 67 (12 critical, 30 high, 25 medium)\n"
        "- TAC Cases: 412 (with 9 P1 and 41 P2)\n"
        "- BEMS Escalations: 5 - THIS IS CRITICAL\n"
    )


def _drifted_portfolio_snapshot() -> str:
    """Narrative reproducing the Round 25 reference report drift.

    The reference Brian Frazier / All Contact Center / 90d report
    showed canonical=37 but narrated="Total Customers: 27" -- this
    test fixture reproduces that exact drift.
    """
    return (
        "## Executive Summary: What's Really Happening\n"
        "\n"
        "Portfolio Snapshot:\n"
        "- Total Customers: 27\n"  # <-- drift
        "- Active Adoption Barriers: 67\n"
        "- TAC Cases: 412\n"
        "- BEMS Escalations: 5\n"
    )


def test_validator_passes_when_narrative_matches_canonical() -> None:
    """No errors and is_valid=True when narrative agrees with canonical."""

    result = validate_word_numeric_drift(
        _good_portfolio_snapshot(),
        canonical_totals=_CANONICAL_TOTALS,
    )
    assert result["is_valid"] is True, (
        "Round 25 / Phase B: narrative agreeing with canonical totals "
        f"must pass the validator.  Got errors={result.get('errors')}."
    )
    assert not result["errors"], (
        f"Expected no errors, got {result['errors']}."
    )
    # The validator should have parsed each label exactly once
    # (one narrated value per metric).
    metrics = result["metrics"]
    assert metrics["narrated_total_customers"] == [37]
    assert metrics["narrated_total_barriers"] == [67]
    assert metrics["narrated_total_cases"] == [412]
    assert metrics["narrated_bems_count"] == [5]


def test_validator_blocks_when_total_customers_drifts() -> None:
    """The 49 vs 27 vs 37 reference-report drift trips the validator."""

    result = validate_word_numeric_drift(
        _drifted_portfolio_snapshot(),
        canonical_totals=_CANONICAL_TOTALS,
    )
    assert result["is_valid"] is False, (
        "Round 25 / Phase B: the reference drift narrative (canonical "
        "37, narrated 27) MUST trip the validator."
    )
    assert any("Total Customers" in e for e in result["errors"]), (
        "Round 25 / Phase B: drift error must name the offending "
        f"metric.  Got errors={result['errors']}."
    )
    # Phase B contract: error message must include both the canonical
    # value and the drifted narrated value so downstream operators can
    # reconcile without reading the validator source.
    drift_error = next(e for e in result["errors"] if "Total Customers" in e)
    assert "37" in drift_error and "27" in drift_error, (
        "Round 25 / Phase B: drift error must include both canonical "
        f"and narrated values.  Got: {drift_error!r}"
    )


def test_validator_raises_when_strict_mode_and_drift() -> None:
    """``raise_on_drift=True`` raises ValueError, the build-blocking gate."""

    with pytest.raises(ValueError) as exc_info:
        validate_word_numeric_drift(
            _drifted_portfolio_snapshot(),
            canonical_totals=_CANONICAL_TOTALS,
            raise_on_drift=True,
        )
    msg = str(exc_info.value)
    assert "Total Customers" in msg
    assert "37" in msg and "27" in msg


def test_validator_emits_warning_when_label_missing() -> None:
    """A doc that omits a label should warn (not error) so we still ship.

    The contract is "narrated value disagrees with canonical = block";
    a doc that omits the snapshot block entirely is a different kind of
    failure (probably the LLM truncated or restructured) and should not
    block on the same code path.  But we *must* surface the omission as
    a warning so it shows up in the consistency log.
    """

    body_without_total_customers = (
        "## Executive Summary\n"
        "\n"
        "Portfolio Snapshot:\n"
        "- Active Adoption Barriers: 67\n"
        "- TAC Cases: 412\n"
        "- BEMS Escalations: 5\n"
    )
    result = validate_word_numeric_drift(
        body_without_total_customers,
        canonical_totals=_CANONICAL_TOTALS,
    )
    # No drift, so is_valid should remain True...
    assert result["is_valid"] is True
    # ...but the "Total Customers" omission must be surfaced as a warning.
    assert any("Total Customers" in w for w in result["warnings"]), (
        "Round 25 / Phase B: omitted label must produce a warning so "
        f"the consistency log shows the gap.  Got warnings={result['warnings']}."
    )


def test_validator_catches_multiple_narrations_one_drifted() -> None:
    """A doc that names the metric twice and disagrees once must still trip."""

    body = (
        "## Executive Summary\n"
        "\n"
        "Portfolio Snapshot:\n"
        "- Total Customers: 37\n"
        "- Active Adoption Barriers: 67\n"
        "\n"
        "Later in the narrative the LLM contradicts itself:\n"
        "- Total Customers: 27\n"
    )
    result = validate_word_numeric_drift(
        body,
        canonical_totals=_CANONICAL_TOTALS,
    )
    # Both 37 and 27 should be parsed; 27 should trip the validator.
    assert result["metrics"]["narrated_total_customers"] == [37, 27]
    assert result["is_valid"] is False
    assert any("Total Customers" in e for e in result["errors"])


def test_validator_accepts_iterable_of_paragraphs() -> None:
    """Passing a list of paragraph strings should work the same as a doc."""

    paragraphs = [
        "## Executive Summary",
        "Portfolio Snapshot:",
        "- Total Customers: 37",
        "- Active Adoption Barriers: 67",
        "- TAC Cases: 412",
        "- BEMS Escalations: 5",
    ]
    result = validate_word_numeric_drift(
        paragraphs,
        canonical_totals=_CANONICAL_TOTALS,
    )
    assert result["is_valid"] is True


def test_validator_accepts_docx_like_document() -> None:
    """A docx.Document-like object exposing ``.paragraphs`` is accepted."""

    class _FakeParagraph:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeDoc:
        def __init__(self, paragraphs: list[str]) -> None:
            self.paragraphs = [_FakeParagraph(p) for p in paragraphs]

    doc = _FakeDoc([
        "Portfolio Snapshot:",
        "Total Customers: 37",
        "Active Adoption Barriers: 67",
        "TAC Cases: 412",
        "BEMS Escalations: 5",
    ])
    result = validate_word_numeric_drift(
        doc,
        canonical_totals=_CANONICAL_TOTALS,
    )
    assert result["is_valid"] is True


def test_validator_skips_metric_when_canonical_value_missing() -> None:
    """If a metric isn't in canonical_totals, the validator skips it.

    This protects partial-data runs where (e.g.) BEMS counting failed
    upstream and the canonical value is genuinely unavailable -- the
    validator should not invent an error for the metric it has no
    truth source for.
    """

    body = "Total Customers: 37\nActive Adoption Barriers: 67\n"
    result = validate_word_numeric_drift(
        body,
        canonical_totals={"total_customers": 37},  # only one key
    )
    assert result["is_valid"] is True
    assert "total_customers" in result["metrics"]["canonical_totals_checked"]
    assert "total_barriers" not in result["metrics"]["canonical_totals_checked"]
