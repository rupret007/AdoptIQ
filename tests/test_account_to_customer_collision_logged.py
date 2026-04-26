"""Round 2 / Phase 4.1 regression test.

When the same ``ACCOUNT_ID_C`` maps to multiple distinct ``BU_NAME``
values, ``data_normalization.build_customer_lookup`` must:

  * pick a deterministic winner (alphabetically first BU_NAME so the
    choice does not depend on row order),
  * record the collision in the returned ``collisions`` list,
  * surface a human-readable string in ``warnings``,
  * log a warning so the operator can see it in the run log.

This is the "no silent overwrites" guarantee.
"""
from __future__ import annotations

import logging

import pandas as pd

from data_normalization import build_customer_lookup


def test_collision_winner_is_alphabetical_and_deterministic() -> None:
    df_order_a = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Zeta Corp"},
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Alpha Corp"},
        ]
    )
    df_order_b = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Alpha Corp"},
            {"ACCOUNT_ID_C": "A-1", "BU_NAME": "Zeta Corp"},
        ]
    )
    lookup_a = build_customer_lookup(df_order_a)
    lookup_b = build_customer_lookup(df_order_b)
    assert (
        lookup_a["account_to_customer"]["A-1"]
        == lookup_b["account_to_customer"]["A-1"]
        == "Alpha Corp"
    ), (
        "Round 2 Phase 4.1: collision winner must be the "
        "alphabetically first BU_NAME so the choice is deterministic "
        "across run-to-run row ordering."
    )


def test_collision_emits_logged_warning(caplog) -> None:
    df = pd.DataFrame(
        [
            {"ACCOUNT_ID_C": "A-99", "BU_NAME": "Acme Corp"},
            {"ACCOUNT_ID_C": "A-99", "BU_NAME": "Acme Holdings"},
        ]
    )
    with caplog.at_level(logging.WARNING, logger="data_normalization"):
        lookup = build_customer_lookup(df)
    assert any(
        "A-99" in rec.getMessage() and "collision" in rec.getMessage().lower()
        for rec in caplog.records
    ), (
        "Round 2 Phase 4.1: build_customer_lookup must log a "
        "warning when an account_to_customer collision is detected; "
        "silent overwrites violate the audit trail requirement."
    )
    assert any(c.get("kind") == "account_to_customer" for c in lookup.get("collisions") or []), (
        "Round 2 Phase 4.1: build_customer_lookup must also include "
        "the collision in the returned `collisions` list so callers "
        "can route it to partial_data_warnings."
    )
