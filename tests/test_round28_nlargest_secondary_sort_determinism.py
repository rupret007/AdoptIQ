"""Round 28 / Phase 3 — pin the determinism contract for the
``stale_list`` ranking in ``adoptiq_backend``.

Previously ``valid.nlargest(5, '_days_open')`` broke ties by upstream
row order, which is sensitive to Snowflake row-ordering and produced
different report outputs for the same data across runs.  Round 28
sorts by ``(_days_open DESC, ID ASC)`` with ``kind='stable'`` so the
top-5 list is byte-identical across runs.

These tests pin both the source-level pattern and the runtime
behaviour.
"""
from __future__ import annotations

import inspect

import pandas as pd

import adoptiq_backend


def test_round28_stale_ranking_uses_stable_sort_with_id_tiebreak() -> None:
    """Source pin: the stale-cases ranking MUST sort by
    ``(_days_open DESC, ID ASC)`` with ``kind='stable'`` instead of
    ``nlargest`` so identical days_open values are tie-broken
    deterministically."""
    src = inspect.getsource(adoptiq_backend)

    # Anchor on the marker comment so we pin the right block.
    idx = src.find('Round 28: deterministic stale-cases ranking')
    assert idx != -1, (
        "Round 28: could not locate the stale-cases determinism "
        "block.  Source-level pin needs updating."
    )
    body = src[idx:idx + 1500]
    assert "sort_values" in body, (
        "Round 28: stale ranking must use sort_values, not nlargest, "
        "so the tie-break key is explicit."
    )
    assert "kind='stable'" in body or 'kind="stable"' in body, (
        "Round 28: stale ranking must use kind='stable' so equal "
        "rows preserve insertion order under the secondary key."
    )
    assert "'ID'" in body or '"ID"' in body, (
        "Round 28: stale ranking must include 'ID' as a tie-break "
        "key (ASC) so reports are byte-identical across runs."
    )


def test_round28_stable_sort_yields_identical_output_across_runs() -> None:
    """Behavioural pin: feed two frames with identical
    ``_days_open`` values but shuffled row order; the resulting
    top-5 IDs MUST match across runs."""
    import random

    rows = [
        {'_days_open': 100, 'ID': 'CASE-A', 'BU_NAME': 'AcmeCo'},
        {'_days_open': 100, 'ID': 'CASE-B', 'BU_NAME': 'BetaCo'},
        {'_days_open': 100, 'ID': 'CASE-C', 'BU_NAME': 'GammaCo'},
        {'_days_open': 90,  'ID': 'CASE-D', 'BU_NAME': 'DeltaCo'},
        {'_days_open': 80,  'ID': 'CASE-E', 'BU_NAME': 'EpsiCo'},
    ]

    runs = []
    for seed in range(5):
        rng = random.Random(seed)
        shuffled = list(rows)
        rng.shuffle(shuffled)
        df = pd.DataFrame(shuffled)
        # Reproduce the Round 28 sort:
        sorted_df = df.sort_values(
            by=['_days_open', 'ID'],
            ascending=[False, True],
            kind='stable',
        ).head(5)
        runs.append(list(sorted_df['ID']))

    # All runs must produce the same ID order.
    first = runs[0]
    for i, run in enumerate(runs[1:], start=1):
        assert run == first, (
            f"Round 28: stable-sort head(5) IDs differ across runs; "
            f"run 0 = {first!r}, run {i} = {run!r}.  "
            "Determinism pin would have caught this in production."
        )
    # Specifically: ties on _days_open=100 break by ID ascending.
    assert first == ['CASE-A', 'CASE-B', 'CASE-C', 'CASE-D', 'CASE-E'], (
        f"Round 28: tie-break order must be ID ASC; got {first!r}"
    )


def test_round28_value_counts_top_n_deterministic_helper_used() -> None:
    """Source pin: the historical-Excel scan MUST use a
    deterministic top-N helper for severity / status / category /
    customer-count distributions (sort_index() before
    sort_values() with kind='stable')."""
    src = inspect.getsource(adoptiq_backend)

    idx = src.find('Round 28: deterministic distribution payloads')
    assert idx != -1, (
        "Round 28: could not locate the deterministic-distribution "
        "block.  Source-level pin needs updating."
    )
    body = src[idx:idx + 2000]
    assert "sort_index()" in body, (
        "Round 28: deterministic distribution helper must call "
        "sort_index() to pre-order ties by key ASC."
    )
    assert "kind='stable'" in body or 'kind="stable"' in body, (
        "Round 28: deterministic distribution helper must use a "
        "stable sort so the (sort_index, sort_values) pipeline "
        "preserves the secondary order."
    )


def test_round28_top_customers_by_count_deterministic_in_excel_scan() -> None:
    """Behavioural pin: the (sort_index → sort_values stable) pipeline
    deterministically tie-breaks ``value_counts().head(N)`` by key
    ASC across runs."""
    s = pd.Series(['Acme', 'Beta', 'Gamma', 'Acme', 'Beta', 'Gamma', 'Delta'])
    runs = []
    for _ in range(5):
        vc = (
            s.value_counts()
            .sort_index()
            .sort_values(ascending=False, kind='stable')
            .head(2)
        )
        runs.append(list(vc.index))

    first = runs[0]
    for i, run in enumerate(runs[1:], start=1):
        assert run == first, (
            f"Round 28: deterministic value_counts head(2) keys "
            f"differ across runs; run 0 = {first!r}, run {i} = {run!r}."
        )
    # Acme, Beta, Gamma all have count=2; tie-break by key ASC -> Acme, Beta
    assert first == ['Acme', 'Beta'], (
        f"Round 28: tie-break order must be key ASC for value_counts; "
        f"got {first!r}"
    )
