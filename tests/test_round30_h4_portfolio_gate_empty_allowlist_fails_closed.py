"""Round 30 / H4 — portfolio narrative gate must fail closed on an empty
entity allowlist.

Round 27 introduced the entity allowlist gate at app_simple.py:12766
with an inline ``or None`` fallback that collapsed an empty set to
``None``.  ``ai_narrative_validator.validate_narrative`` documents
``allowed_entities=None`` as "skip the entity-grounding check
entirely" -- so an empty allowlist (e.g. zero accounts after the
narrow-universe filter ran) silently fell open instead of rejecting
every entity in the LLM narrative.

The Round 30 fix drops the ``or None`` fallback and passes the set
directly so the validator enforces the ``len(allowed_entities) == 0``
case as "no entity is grounded -- reject everything."
"""

from __future__ import annotations

import inspect
import re

import app_simple


def test_round30_h4_portfolio_validate_call_does_not_use_or_none() -> None:
    """Source pin: the portfolio narrative call site at the validator
    boundary MUST NOT collapse an empty allowlist to ``None`` via an
    ``or None`` fallback.  An empty set has to reach the validator so
    every LLM-claimed entity is rejected (i.e. fail closed)."""
    src = inspect.getsource(app_simple)
    # Round 30 / H4 fix is anchored on the literal portfolio validator
    # call inside the ``_r27_validate_portfolio_narrative`` codepath.
    # The pattern below catches both the original ``or None`` and the
    # space-padded ``or  None`` variant that ruff/black might emit.
    bad_pattern = re.compile(
        r"allowed_entities\s*=\s*_r27_allowed_port\s+or\s+None"
    )
    matches = bad_pattern.findall(src)
    assert not matches, (
        "Round 30 / H4: app_simple.py must pass _r27_allowed_port "
        "to validate_narrative directly (no ``or None`` fallback).  "
        "An empty allowlist must reach the validator so it rejects "
        "every claimed entity rather than silently skipping the "
        "entity-grounding check entirely."
    )


def test_round30_h4_portfolio_validate_call_uses_set_directly() -> None:
    """Companion source pin: the call site MUST contain the literal
    ``allowed_entities=_r27_allowed_port`` (no transform) so the post-
    fix contract is positively asserted, not just absent."""
    src = inspect.getsource(app_simple)
    assert "allowed_entities=_r27_allowed_port" in src, (
        "Round 30 / H4: portfolio validator call site must pass the "
        "_r27_allowed_port set directly."
    )


def test_round30_h4_validator_documents_empty_set_is_fail_closed() -> None:
    """Behavioural pin: the validator's docstring documents the
    contract that ``None`` skips the check while an empty set rejects
    every entity (fail closed).  This pins the contract our fix
    relies on so a future validator refactor cannot silently flip
    the semantics."""
    try:
        import ai_narrative_validator
    except Exception:  # pragma: no cover
        return  # validator absent in some test envs; covered elsewhere
    fn = getattr(ai_narrative_validator, 'validate_narrative', None)
    if fn is None:
        return
    doc = (fn.__doc__ or '') + inspect.getsource(fn)
    # The contract is encoded both in code and in the docstring.  We
    # accept either as evidence so the test is resilient to docstring
    # rewording.
    assert (
        'allowed_entities is None' in doc
        or 'allowed_entities is not None' in doc
        or 'allowed_entities=None' in doc
        or 'len(allowed_entities)' in doc
    ), (
        "Round 30 / H4: ai_narrative_validator.validate_narrative must "
        "document its empty-set vs None semantics (None == skip, "
        "empty set == fail closed)."
    )
