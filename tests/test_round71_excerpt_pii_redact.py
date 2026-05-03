"""Round 71 / Phase 5 (#25) -- excerpt PII redaction.

Pre-R71 ``_r65_grounding_excerpt`` persisted ``briefing_excerpt`` and
``narrative_excerpt`` to ``analysis_status.json`` verbatim.  When the
briefing or narrative carried the customer name in plain text (which
the R64 grounding pipeline routinely does), the persisted JSON
became a PII bag readable by anyone with read access to the
operator's writable storage area.

Round 71 / Phase 5 (#25) redacts the customer_name from both excerpts
(case-insensitive, first then any subsequent occurrences) by
substituting ``<CUSTOMER>`` BEFORE the length cap is applied.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_excerpt_helper():
    """Import the helper directly from app_simple."""
    import app_simple  # noqa: PLC0415
    return app_simple._r65_grounding_excerpt


def test_round71_excerpt_redacts_customer_name() -> None:
    """When ``customer_name`` is provided, the helper MUST redact it
    from the excerpt (replaced with ``<CUSTOMER>``)."""
    excerpt_fn = _load_excerpt_helper()
    text = "Brian Frazier's portfolio shows 23 critical barriers."
    out = excerpt_fn(text, customer_name="Brian Frazier")
    assert "Brian Frazier" not in out, (
        f"Round 71 / Phase 5 (#25): customer_name must NOT appear in "
        f"the persisted excerpt.  Got: {out!r}"
    )
    assert "<CUSTOMER>" in out, (
        f"Round 71 / Phase 5 (#25): the redacted token must be "
        f"replaced with ``<CUSTOMER>`` so the operator can still see "
        f"the structure of the excerpt.  Got: {out!r}"
    )


def test_round71_excerpt_redaction_is_case_insensitive() -> None:
    """The redaction MUST handle case variants (``brian frazier`` /
    ``BRIAN FRAZIER`` / ``Brian Frazier``)."""
    excerpt_fn = _load_excerpt_helper()
    for variant in ("brian frazier", "BRIAN FRAZIER", "Brian frazier"):
        text = f"The {variant} portfolio shows 23 critical barriers."
        out = excerpt_fn(text, customer_name="Brian Frazier")
        assert variant.lower() not in out.lower() or "<CUSTOMER>" in out, (
            f"Round 71 / Phase 5 (#25): redaction must be case-insensitive "
            f"for variant {variant!r}.  Got: {out!r}"
        )


def test_round71_excerpt_redacts_multiple_occurrences() -> None:
    """If the customer name appears multiple times, ALL occurrences
    MUST be redacted."""
    excerpt_fn = _load_excerpt_helper()
    text = "Acme Corp leads in barriers; Acme Corp also leads in TAC."
    out = excerpt_fn(text, customer_name="Acme Corp")
    assert "Acme Corp" not in out, (
        f"Round 71 / Phase 5 (#25): all occurrences of the customer "
        f"name must be redacted.  Got: {out!r}"
    )
    # Both instances replaced.
    assert out.count("<CUSTOMER>") >= 2, (
        f"Round 71 / Phase 5 (#25): multi-occurrence redaction must "
        f"replace ALL hits.  Got: {out!r}"
    )


def test_round71_excerpt_no_redaction_without_customer_name() -> None:
    """When ``customer_name`` is None or empty, the helper MUST NOT
    insert any ``<CUSTOMER>`` tokens."""
    excerpt_fn = _load_excerpt_helper()
    text = "23 critical barriers and 5 P1 cases."
    out = excerpt_fn(text, customer_name=None)
    assert "<CUSTOMER>" not in out, (
        f"Round 71 / Phase 5 (#25): without a customer_name the helper "
        f"must NOT insert <CUSTOMER> tokens.  Got: {out!r}"
    )


def test_round71_excerpt_redaction_happens_before_length_cap() -> None:
    """The redaction MUST happen BEFORE the length cap so a long
    customer name doesn't blow past the cap."""
    excerpt_fn = _load_excerpt_helper()
    long_name = "Cisco Systems International Operations Holding GmbH AG Ltd"
    text = f"{long_name} reports 23 critical barriers."
    out = excerpt_fn(text, customer_name=long_name, max_len=80)
    assert long_name not in out, (
        f"Round 71 / Phase 5 (#25): redaction must happen BEFORE the "
        f"length cap so the persisted excerpt contains no PII even "
        f"for long names.  Got: {out!r}"
    )
