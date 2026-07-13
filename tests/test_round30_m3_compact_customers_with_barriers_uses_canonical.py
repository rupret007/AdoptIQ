"""Round 30 / M3 — compact 'Customers with Barriers' tile must route
through the canonical helper so every report agrees on the
denominator and the downstream 'Average Barriers per Customer' math.

Compact previously used an inline ``.nunique()`` shape that applied
``normalize_customer_name`` locally.  Round 25 (then) and Round 30
(now) centralized the same shape into
``canonical_metrics.count_customers_with_barriers`` so the leader,
executive, compact, and Excel surfaces all agree.
"""

from __future__ import annotations

import inspect

import canonical_metrics as cm
import compact_report_formatter
import pandas as pd


def test_round30_m3_canonical_helper_exists() -> None:
    """The canonical helper must be a public-name symbol."""
    assert hasattr(cm, 'count_customers_with_barriers'), (
        "Round 30 / M3: canonical_metrics must export "
        "count_customers_with_barriers."
    )


def test_round30_m3_compact_uses_canonical_helper() -> None:
    """Source pin: ``compact_report_formatter`` MUST call
    ``cm.count_customers_with_barriers`` for the 'Customers with
    Barriers' tile."""
    src = inspect.getsource(compact_report_formatter)
    assert "count_customers_with_barriers" in src, (
        "Round 30 / M3: compact must route the Customers with Barriers "
        "count through cm.count_customers_with_barriers."
    )


def test_round30_m3_canonical_handles_cosmetic_drift() -> None:
    """Behavioural pin: the canonical helper applies the same
    NFKC + whitespace normalization across every caller, so cosmetic
    drift (NBSPs, trailing whitespace) cannot inflate the count.

    Note: ``normalize_customer_name`` deliberately preserves case
    (Round 13 / Phase 3.15) so the displayed name keeps the casing
    the customer recognizes; case-fold is reserved for the join key.
    The cosmetic-drift contract therefore covers NFKC compatibility
    + whitespace collapse + trailing-punctuation strip."""
    df = pd.DataFrame({
        'customer_name': [
            'Acme Corp',
            'Acme Corp ',  # trailing space
            'Acme\u00a0Corp',  # NBSP collapses via NFKC
            'Acme  Corp',  # double-space collapses via re.sub(r"\s+", " ")
            'Beta Inc',
        ],
        'STATUS': ['Open'] * 5,
    })
    n = cm.count_customers_with_barriers(df)
    # The four "Acme" variants must collapse to one canonical key,
    # plus "Beta" = 2 distinct customers.
    assert n == 2, (
        f"Round 30 / M3: canonical helper must collapse cosmetic "
        f"drift (NBSPs, trailing whitespace, double-spaces) into a "
        f"single customer; got {n}, expected 2."
    )


def test_round30_m3_canonical_returns_zero_on_empty() -> None:
    """Empty / None frames must return 0 (not raise)."""
    assert cm.count_customers_with_barriers(None) == 0
    assert cm.count_customers_with_barriers(pd.DataFrame()) == 0
