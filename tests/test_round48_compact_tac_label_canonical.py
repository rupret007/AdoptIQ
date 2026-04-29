"""Round 48 / F-COMP-TAC-LABEL-AMBIGUITY regression tests.

Pin the canonical TAC-case label vocabulary in the comprehensive
report prompt template so that LLM-rendered narratives use a
self-explanatory "Total Support Cases (90d)" / "Open + critical
(P1+P2)" pair rather than the ambiguous "TAC Cases: N cases" line
that surfaced in the audit baseline (run 1777445582 -- compact_doc
lines 50 and 57 read "TAC Cases: 36 cases, 2 are P2." and "TAC
Cases: 47 cases." with no in-doc disambiguation).

These tests assert against the prompt template (the source of
truth the LLM is grounded in) rather than the rendered output
because the rendered output is non-deterministic; the template is
the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_BACKEND_PATH = Path(__file__).resolve().parent.parent / "adoptiq_backend.py"


@pytest.fixture(scope="module")
def backend_source() -> str:
    return _BACKEND_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical labels MUST be present in the prompt
# ---------------------------------------------------------------------------


def test_round48_prompt_uses_total_support_cases_90d_label(backend_source: str):
    """The "Customers in Trouble" section template must use
    ``Total Support Cases (90d)`` as the canonical headline label
    for per-customer support volume.  This is the unambiguous label
    that names the time window and the underlying source (CSOne TAC
    cases) in one phrase.
    """

    assert "Total Support Cases (90d)" in backend_source, (
        "Round 48 canonical label 'Total Support Cases (90d)' missing "
        "from prompt template; the LLM will continue to render the "
        "ambiguous 'TAC Cases: N cases' string"
    )


def test_round48_prompt_uses_open_plus_critical_label(backend_source: str):
    """The high-severity slice must be labelled
    ``Open + critical (P1+P2)`` so a reader who sees ``36`` on this
    line cannot confuse it with a raw row count, an open-vs-closed
    count, or a P1-only count.
    """

    assert "Open + critical (P1+P2)" in backend_source, (
        "Round 48 canonical 'Open + critical (P1+P2)' label missing "
        "from prompt template; high-severity TAC slice will remain "
        "ambiguous in LLM-rendered narratives"
    )


def test_round48_prompt_canonical_labels_co_located(backend_source: str):
    """The two canonical labels must appear within the same
    Customers-in-Trouble template stanza (and in this order:
    total before open+critical) so the LLM emits them as a pair on
    a single bullet, not as two unrelated lines elsewhere in the
    document.  Pin them within a 200-character window so a future
    edit that moves one of them away from the other is caught.
    """

    total_idx = backend_source.find("Total Support Cases (90d)")
    open_critical_idx = backend_source.find("Open + critical (P1+P2)")
    assert total_idx != -1 and open_critical_idx != -1
    assert open_critical_idx > total_idx, (
        "'Open + critical (P1+P2)' must appear AFTER "
        "'Total Support Cases (90d)' so the rendered bullet reads "
        "total-first, slice-second"
    )
    assert open_critical_idx - total_idx < 200, (
        "Canonical TAC labels must sit within the same template "
        "stanza (within 200 chars); current gap is "
        f"{open_critical_idx - total_idx} characters"
    )


# ---------------------------------------------------------------------------
# Forbidden ambiguous labels MUST be gone from the prompt
# ---------------------------------------------------------------------------


def test_round48_prompt_no_legacy_tac_cases_y_z_template(backend_source: str):
    """The legacy ``TAC Cases: [Y cases, Z are P1/P2]`` template line
    must no longer appear -- it is the literal source of the audit
    baseline's "TAC Cases: 47 cases." string.  Pin the exact legacy
    shape so a future edit that re-introduces it is caught.
    """

    legacy_pattern = re.compile(
        r"\*\*TAC Cases:\*\*\s*\[Y cases,\s*Z are P1/P2\]"
    )
    assert not legacy_pattern.search(backend_source), (
        "Legacy ambiguous template '**TAC Cases:** [Y cases, Z are "
        "P1/P2]' is still present in adoptiq_backend.py; the LLM "
        "will keep emitting 'TAC Cases: N cases' strings that the "
        "audit baseline flagged as ambiguous"
    )


def test_round48_prompt_includes_negative_constraint_against_tac_cases_n_cases(
    backend_source: str,
):
    """The fix anchor (the F-COMP-TAC-LABEL-AMBIGUITY comment in the
    template) must explicitly forbid the LLM from emitting the bare
    "TAC Cases: N cases" string.  This is a belt-and-braces test:
    the canonical-label assertions above guarantee the new labels
    are present, and this assertion guarantees the negative
    constraint is also present so the LLM is steered away from the
    ambiguous form even when its training prior pulls toward it.
    """

    assert "F-COMP-TAC-LABEL-AMBIGUITY" in backend_source, (
        "Round 48 fix anchor 'F-COMP-TAC-LABEL-AMBIGUITY' missing "
        "from adoptiq_backend.py; reviewers cannot trace the "
        "rationale for the canonical-label change"
    )
    assert 'TAC Cases: N cases' in backend_source, (
        "Negative constraint string 'TAC Cases: N cases' missing "
        "from prompt template; the LLM has no explicit instruction "
        "to avoid the legacy ambiguous form"
    )
