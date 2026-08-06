"""Round 25 / Phase C tests: canonical risk-band parity in the trouble-spot list.

Three groups of assertions:

1.  ``_create_briefing_book`` emits a "Canonical Risk Bands" section
    when ``risk_profiles`` is supplied, with each customer mapped to
    one of the five canonical labels (CRITICAL, HIGH, MEDIUM, LOW,
    HEALTHY).
2.  ``PROMPT_PORTFOLIO_TEMPLATE`` carries the Phase C "RISK BAND
    BINDING" preamble that instructs the LLM to use the briefing's
    canonical bands verbatim.
3.  ``validate_word_risk_band_claims`` correctly:
    - Passes a narrative whose CRITICAL+HIGH count matches the
      canonical ``high_risk_customers`` value.
    - Blocks a narrative that names three CRITICAL/HIGH customers
      while canonical says one (the reference Brian Frazier report
      reproduced verbatim).
    - Rejects compound bands like "HIGH/CRITICAL".
    - Surfaces a non-canonical synonym like "MODERATE" as an error.
"""

from __future__ import annotations
from source_shape_utils import assert_in_source

import pandas as pd
import pytest

from adoptiq_backend import _create_briefing_book, PROMPT_PORTFOLIO_TEMPLATE
from report_consistency import validate_word_risk_band_claims


# ---------------------------------------------------------------------------
# Group 1: _create_briefing_book emits canonical risk bands
# ---------------------------------------------------------------------------


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def _minimal_briefing_kwargs() -> dict:
    return {
        "data_scope": "Test Manager's Portfolio",
        "ab_df": _empty_df(),
        "csone_df": _empty_df(),
        "ext_bugs": [],
        "ext_incidents": [],
        "matches": [],
        "matched_df": _empty_df(),
        "db_profile": None,
    }


def test_briefing_emits_canonical_risk_bands_section_when_profiles_supplied() -> None:
    """The briefing must list each customer with their canonical band.

    Five customers spanning CRITICAL / HIGH / MEDIUM / HEALTHY are
    emitted in canonical-band-rank order (CRITICAL first, then HIGH,
    then MEDIUM, then HEALTHY).  Note the briefing function returns a
    single newline-joined string, so we split it for line-level
    assertions.
    """

    risk_profiles = {
        "FARMERS": {"risk_score": 95.0, "risk_level": "CRITICAL"},
        "NATIONAL GRID": {"risk_score": 85.0, "risk_level": "CRITICAL"},
        "WINTRUST": {"risk_score": 65.0, "risk_level": "HIGH"},
        "ACME": {"risk_score": 45.0, "risk_level": "MEDIUM"},
        "HEALTHY CO": {"risk_score": 5.0, "risk_level": "HEALTHY"},
    }
    text = _create_briefing_book(
        **_minimal_briefing_kwargs(),
        risk_profiles=risk_profiles,
    )
    lines = text.splitlines()
    # Header is present.
    assert_in_source(text, "### Canonical Risk Bands (Round 25 / Phase C)", label='text')
    # Each customer is named with their canonical band.
    for name, profile in risk_profiles.items():
        band = profile["risk_level"]
        assert any(
            name in line and f"Risk Level: {band}" in line for line in lines
        ), (
            f"Round 25 / Phase C: briefing must list {name} with "
            f"canonical band {band!r}.  Briefing:\n" + text
        )

    # Order: CRITICAL block precedes HIGH which precedes MEDIUM.
    farmers_line = next(i for i, line in enumerate(lines) if "FARMERS" in line)
    wintrust_line = next(i for i, line in enumerate(lines) if "WINTRUST" in line)
    acme_line = next(i for i, line in enumerate(lines) if "ACME" in line)
    assert farmers_line < wintrust_line < acme_line, (
        "Round 25 / Phase C: canonical risk-band section must be sorted "
        "by band rank (CRITICAL > HIGH > MEDIUM > LOW > HEALTHY)."
    )


def test_briefing_falls_back_to_score_based_band_when_label_missing() -> None:
    """A profile without ``risk_level`` is bucketed by its ``risk_score``."""

    risk_profiles = {
        "ScoreOnly_Critical": {"risk_score": 92.0},  # >= 80 -> CRITICAL
        "ScoreOnly_High": {"risk_score": 70.0},      # >= 60 -> HIGH
    }
    text = _create_briefing_book(
        **_minimal_briefing_kwargs(),
        risk_profiles=risk_profiles,
    )
    assert_in_source(text, "ScoreOnly_Critical" in text and "Risk Level: CRITICAL", label='text')
    assert_in_source(text, "ScoreOnly_High" in text and "Risk Level: HIGH", label='text')


def test_briefing_skips_section_when_no_risk_profiles() -> None:
    """Without risk profiles the section is omitted (no false truth source)."""

    text = _create_briefing_book(
        **_minimal_briefing_kwargs(),
        risk_profiles=None,
    )
    assert "Canonical Risk Bands (Round 25 / Phase C)" not in text


# ---------------------------------------------------------------------------
# Group 2: PROMPT_PORTFOLIO_TEMPLATE carries the Phase C preamble
# ---------------------------------------------------------------------------


def test_template_carries_phase_c_risk_band_binding_preamble() -> None:
    """Phase C 'RISK BAND BINDING' section is wired into the prompt."""

    # Render with the Phase B kwargs so we can search the rendered body.
    rendered = PROMPT_PORTFOLIO_TEMPLATE.format(
        MANAGER="Test", TECHNOLOGY="Test",
        TOTAL_CUSTOMERS=37, TOTAL_BARRIERS=67,
        TAC_CASES=412, P1_CASES=9, P2_CASES=41, BEMS_ESCALATIONS=5,
    )
    assert "RISK BAND BINDING" in rendered, (
        "Round 25 / Phase C: the 'All Customers in Trouble' block must "
        "carry the 'RISK BAND BINDING' preamble that binds the LLM to "
        "the briefing's canonical bands."
    )
    # The five canonical labels are explicitly enumerated in the prompt.
    for band in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "HEALTHY"):
        assert band in rendered, (
            f"Round 25 / Phase C: prompt must enumerate canonical band {band}."
        )
    # Forbid compound labels by name.
    assert 'HIGH/CRITICAL' in rendered, (
        "Round 25 / Phase C: prompt must explicitly forbid the "
        "'HIGH/CRITICAL' compound label by name."
    )


# ---------------------------------------------------------------------------
# Group 3: validate_word_risk_band_claims behaviour
# ---------------------------------------------------------------------------


def test_validator_passes_when_critical_high_count_matches() -> None:
    """One CRITICAL line + canonical=1 high_risk_customers -> is_valid."""

    body = (
        "**1. FARMERS - Risk Level: CRITICAL**\n"
        "  problem details...\n"
        "**2. ACME - Risk Level: MEDIUM**\n"
        "  problem details...\n"
    )
    result = validate_word_risk_band_claims(
        body, canonical_high_risk_customers=1
    )
    assert result["is_valid"] is True
    assert result["metrics"]["narrated_critical_high_count"] == 1
    assert result["metrics"]["narrated_risk_levels"] == ["CRITICAL", "MEDIUM"]


def test_validator_blocks_when_narrative_lists_three_critical_high_but_canonical_one() -> None:
    """Reproduces the reference Brian Frazier drift exactly.

    Word dashboard tile said Critical+High = 1.
    LLM trouble-spot block enumerated FARMERS=CRITICAL, NATIONAL GRID=
    CRITICAL, WINTRUST=HIGH (3 CRITICAL+HIGH lines).  This must trip
    the validator.
    """

    body = (
        "**1. FARMERS - Risk Level: CRITICAL**\n"
        "**2. NATIONAL GRID - Risk Level: CRITICAL**\n"
        "**3. WINTRUST - Risk Level: HIGH**\n"
        "**4. ACME - Risk Level: MEDIUM**\n"
    )
    result = validate_word_risk_band_claims(
        body, canonical_high_risk_customers=1
    )
    assert result["is_valid"] is False
    assert result["metrics"]["narrated_critical_high_count"] == 3
    assert any(
        "CRITICAL+HIGH narrative drift" in e for e in result["errors"]
    ), result["errors"]
    drift_error = next(
        e for e in result["errors"] if "CRITICAL+HIGH narrative drift" in e
    )
    assert "1" in drift_error and "3" in drift_error


def test_validator_rejects_compound_band_labels() -> None:
    """Compound labels like HIGH/CRITICAL are non-canonical and must error.

    The Phase C prompt explicitly forbids these, but the LLM will still
    occasionally produce them.  The validator is the safety net.
    """

    body = (
        "**1. FARMERS - Risk Level: HIGH/CRITICAL**\n"
        "**2. ACME - Risk Level: MEDIUM/LOW**\n"
    )
    result = validate_word_risk_band_claims(
        body, canonical_high_risk_customers=1
    )
    assert result["is_valid"] is False
    assert any(
        "non-canonical risk band label" in e for e in result["errors"]
    ), result["errors"]
    invalid = result["metrics"]["invalid_band_labels"]
    assert "HIGH/CRITICAL" in invalid
    assert "MEDIUM/LOW" in invalid


def test_validator_rejects_synonym_band_labels() -> None:
    """Synonyms like MODERATE / SEVERE must be flagged."""

    body = (
        "**1. ACME - Risk Level: MODERATE**\n"
    )
    result = validate_word_risk_band_claims(
        body, canonical_high_risk_customers=0
    )
    assert result["is_valid"] is False
    assert "MODERATE" in result["metrics"]["invalid_band_labels"]


def test_validator_warns_when_no_risk_levels_found() -> None:
    """A doc that omits the trouble-spot block warns rather than errors."""

    body = "## Some other section\n\nNo risk-level lines anywhere here.\n"
    result = validate_word_risk_band_claims(
        body, canonical_high_risk_customers=2
    )
    assert result["is_valid"] is True
    assert any("no Risk Level lines found" in w for w in result["warnings"]), (
        result["warnings"]
    )


def test_validator_raises_when_strict_mode_and_drift() -> None:
    """``raise_on_drift=True`` is the build-blocking gate."""

    body = (
        "**1. FARMERS - Risk Level: CRITICAL**\n"
        "**2. NATIONAL GRID - Risk Level: CRITICAL**\n"
        "**3. WINTRUST - Risk Level: HIGH**\n"
    )
    with pytest.raises(ValueError) as exc_info:
        validate_word_risk_band_claims(
            body,
            canonical_high_risk_customers=1,
            raise_on_drift=True,
        )
    assert "CRITICAL+HIGH narrative drift" in str(exc_info.value)


def test_validator_accepts_docx_like_document_for_phase_c() -> None:
    """Validator works on a docx.Document-like object the same as a string."""

    class _FakeParagraph:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeDoc:
        def __init__(self, paragraphs: list[str]) -> None:
            self.paragraphs = [_FakeParagraph(p) for p in paragraphs]

    doc = _FakeDoc([
        "**1. FARMERS - Risk Level: CRITICAL**",
        "**2. ACME - Risk Level: MEDIUM**",
    ])
    result = validate_word_risk_band_claims(
        doc, canonical_high_risk_customers=1
    )
    assert result["is_valid"] is True
