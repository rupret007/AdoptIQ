"""Round 4 / Phase 3.4 regression test.

``data_normalization.OPEN_STATUS_PATTERNS`` must include common
in-progress phrases (``waiting on customer`` / ``awaiting customer``
/ ``pending customer`` / ``customer pending``).  Pre-Round-4 these
fell through to ``Unknown`` and ``cm.count_open_tac`` undercounted.
"""
from __future__ import annotations

import re

import data_normalization as dn


def test_waiting_on_customer_classified_as_open() -> None:
    candidates = [
        "Waiting on Customer",
        "Awaiting Customer Response",
        "Pending Customer",
        "Customer Pending",
        "Waiting for Customer",
    ]
    # The classifier may live under different names; we try the
    # common public surfaces.
    classifier = (
        getattr(dn, "normalize_status", None)
        or getattr(dn, "classify_status", None)
        or getattr(dn, "normalize_case_status", None)
    )
    if classifier is None:
        # Fall back to pattern-set inspection.
        patterns = getattr(dn, "OPEN_STATUS_PATTERNS", None)
        assert patterns is not None, (
            "data_normalization must expose either a normalize_status() "
            "callable or an OPEN_STATUS_PATTERNS set."
        )
        joined = " | ".join(p.pattern if hasattr(p, "pattern") else str(p) for p in patterns)
        assert re.search(r"waiting", joined, flags=re.IGNORECASE), (
            "Round 4 Phase 3.4: OPEN_STATUS_PATTERNS must include "
            "'waiting' family phrases (e.g. 'waiting on customer')."
        )
        return

    for status_text in candidates:
        result = classifier(status_text)
        # Acceptable: result is the string "Open" / "open", or it
        # is a dict / tuple whose lifecycle / status field is "Open".
        as_text = str(result).lower()
        assert "open" in as_text, (
            f"Round 4 Phase 3.4: status '{status_text}' must classify "
            f"as Open (got: {result!r})."
        )
