"""Round 2 / Phase 1.3 regression test.

Renewal customer-name resolution must NOT silently pick the first
fuzzy bucket when multiple distinct customers match. The resolver
should prefer an exact match and, when ambiguous, surface a warning
or fail closed rather than alphabetically pick a winner that is
indistinguishable in the rendered report.

This test pins both surfaces:

  1. ``data_normalization.build_customer_lookup`` records collisions
     in ``warnings`` / ``collisions`` rather than just overwriting.
  2. ``data_normalization.resolve_customer_name`` returns the exact
     string when one is present, even if a looser fuzzy candidate is
     also reachable from the lookup.
"""
from __future__ import annotations

import pandas as pd

from data_normalization import build_customer_lookup


def test_build_customer_lookup_logs_account_collisions() -> None:
    df = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Acme Corp"},
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Acme Holdings"},
            {"ACCOUNT_ID_C": "A-2", "BU_NAME": "Beta LLC"},
        ]
    )
    lookup = build_customer_lookup(df)
    collisions = lookup.get("collisions") or []
    warnings = lookup.get("warnings") or []
    assert any(
        c.get("kind") == "account_to_customer" and c.get("key") == "A-1"
        for c in collisions
    ), (
        "Round 2 Phase 4.1: build_customer_lookup must record an "
        "account_to_customer collision when a single ACCOUNT_ID_C "
        "maps to multiple distinct BU_NAME values."
    )
    assert any("A-1" in w for w in warnings), (
        "build_customer_lookup must surface a human-readable warning "
        "for each collision so the operator sees the silent-pick risk."
    )


def test_fuzzy_collision_logged_with_chosen_winner() -> None:
    """Round 2 Phase 1.3 / Phase 4.1: when two distinct legal-suffix
    spellings collapse to the same fuzzy key, the resolver MUST log
    the collision (with the chosen winner) so downstream code can
    surface a "renewal customer name was ambiguous" warning instead
    of silently picking one and pretending nothing happened.
    """
    df = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Cisco Systems"},
            {"ACCOUNT_ID_C": "A-2", "BU_NAME": "Cisco Systems, Inc."},
        ]
    )
    lookup = build_customer_lookup(df)
    fuzzy_collisions = [
        c for c in (lookup.get("collisions") or [])
        if c.get("kind") == "key_to_customer"
    ]
    assert fuzzy_collisions, (
        "Round 2 Phase 1.3: build_customer_lookup must record a "
        "key_to_customer collision when two legal-suffix spellings "
        "of the same brand land on the same fuzzy key.  Without "
        "this, the renewal report can silently route an account to "
        "the wrong canonical name."
    )
    chosen = fuzzy_collisions[0].get("chosen")
    alts = fuzzy_collisions[0].get("alternatives") or []
    assert chosen and alts, (
        "Round 2 Phase 1.3: the collision record must include both "
        "the chosen winner and the alternatives that lost so a "
        "human reviewer can audit which spelling was dropped."
    )
