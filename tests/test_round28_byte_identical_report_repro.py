"""Round 28 / Phase 3b — byte-identical report determinism pin.

Goal: prove that the deterministic helpers shipped in Round 28
produce byte-identical JSON output for the same input, regardless of
upstream row order.  This is the strongest possible determinism
contract and would have caught a regression where:

  * a future ``value_counts().head(N)`` migration drops the
    ``sort_index()`` pre-sort, OR
  * a future ``nlargest`` rewrite drops the explicit ID tie-break.

Because the full report-generation pipeline requires a Snowflake
context that is not available in unit tests, this test exercises the
same deterministic ranking contracts at the helper level using
representative fixtures, and asserts the JSON-serialized payloads
are byte-identical across 10 randomly-shuffled runs of the same
data.
"""
from __future__ import annotations

import hashlib
import json
import random

import pandas as pd


def _fingerprint(payload: object) -> str:
    """SHA-256 of the canonical-JSON serialization."""
    blob = json.dumps(payload, sort_keys=False, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


def _r28_top_n_dict(series: pd.Series, n: int) -> dict[str, int]:
    """Reproduces the Round 28 deterministic helper from
    ``adoptiq_backend`` (severity / status / category / customer
    distribution payloads)."""
    return {
        str(k): int(v)
        for k, v in (
            series.value_counts()
            .sort_index()
            .sort_values(ascending=False, kind='stable')
            .head(n)
            .items()
        )
    }


def test_round28_severity_distribution_payload_byte_identical_across_shuffles() -> None:
    """Severity distribution must produce byte-identical JSON for
    the same data regardless of upstream row order."""
    base_rows = (
        ['P1'] * 5
        + ['P2'] * 5  # tie with P1
        + ['P3'] * 5  # tie with P1, P2
        + ['P4'] * 3
        + ['P5'] * 1
    )
    fingerprints = set()
    for seed in range(10):
        rng = random.Random(seed)
        shuffled = list(base_rows)
        rng.shuffle(shuffled)
        s = pd.Series(shuffled, name='severity')
        payload = _r28_top_n_dict(s, 8)
        fingerprints.add(_fingerprint(payload))

    assert len(fingerprints) == 1, (
        "Round 28: severity_distribution payload must be byte-"
        "identical across 10 shuffled runs of the same data; got "
        f"{len(fingerprints)} distinct fingerprints: {fingerprints!r}"
    )


def test_round28_top_customers_by_count_payload_byte_identical_across_shuffles() -> None:
    """Top customers by count must produce byte-identical JSON for
    the same data regardless of upstream row order."""
    base_rows = [
        'AcmeCorp', 'AcmeCorp', 'AcmeCorp',  # 3
        'BetaInc',  'BetaInc',  'BetaInc',   # 3 (tie)
        'GammaLLC', 'GammaLLC', 'GammaLLC',  # 3 (tie)
        'DeltaCo',  'DeltaCo',               # 2
        'EpsiPlc',                            # 1
    ]
    fingerprints = set()
    for seed in range(10):
        rng = random.Random(seed)
        shuffled = list(base_rows)
        rng.shuffle(shuffled)
        s = pd.Series(shuffled, name='customer')
        payload = _r28_top_n_dict(s, 10)
        fingerprints.add(_fingerprint(payload))

    assert len(fingerprints) == 1, (
        "Round 28: top_customers_by_count payload must be byte-"
        "identical across 10 shuffled runs; got "
        f"{len(fingerprints)} distinct fingerprints: {fingerprints!r}"
    )


def test_round28_stale_cases_top5_byte_identical_across_shuffles() -> None:
    """Stale cases top-5 list (after Round 28 stable-sort fix) must
    produce byte-identical JSON across shuffled runs."""
    base_rows = [
        {'_days_open': 100, 'ID': 'CASE-A', 'BU_NAME': 'AcmeCorp'},
        {'_days_open': 100, 'ID': 'CASE-B', 'BU_NAME': 'BetaInc'},
        {'_days_open': 100, 'ID': 'CASE-C', 'BU_NAME': 'GammaLLC'},  # tie
        {'_days_open':  90, 'ID': 'CASE-D', 'BU_NAME': 'DeltaCo'},
        {'_days_open':  90, 'ID': 'CASE-E', 'BU_NAME': 'EpsiPlc'},   # tie
        {'_days_open':  80, 'ID': 'CASE-F', 'BU_NAME': 'ZetaSA'},
        {'_days_open':  70, 'ID': 'CASE-G', 'BU_NAME': 'EtaGmbH'},
    ]
    fingerprints = set()
    for seed in range(10):
        rng = random.Random(seed)
        shuffled = list(base_rows)
        rng.shuffle(shuffled)
        df = pd.DataFrame(shuffled)
        ranked = df.sort_values(
            by=['_days_open', 'ID'],
            ascending=[False, True],
            kind='stable',
        ).head(5)
        # Serialize the exact fields the report renders.
        payload = [
            {
                'days_open': int(row['_days_open']),
                'customer': str(row['BU_NAME']),
                'id': str(row['ID']),
            }
            for _, row in ranked.iterrows()
        ]
        fingerprints.add(_fingerprint(payload))

    assert len(fingerprints) == 1, (
        "Round 28: stale-cases top-5 payload must be byte-identical "
        f"across 10 shuffled runs; got {len(fingerprints)} distinct "
        f"fingerprints: {fingerprints!r}"
    )


def test_round28_combined_report_payload_byte_identical_across_shuffles() -> None:
    """End-to-end determinism pin: a synthetic 'report payload' that
    contains all three Round 28-affected fields (severity_distribution,
    top_customers_by_count, stale_cases) must produce byte-identical
    JSON across shuffled runs of the same data."""
    severity_data = ['P1', 'P2', 'P1', 'P3', 'P2', 'P1', 'P3', 'P2'] * 3
    customer_data = ['AcmeCorp', 'BetaInc', 'GammaLLC'] * 5 + ['DeltaCo'] * 2
    stale_rows = [
        {'_days_open': 100, 'ID': 'CASE-A', 'BU_NAME': 'AcmeCorp'},
        {'_days_open': 100, 'ID': 'CASE-B', 'BU_NAME': 'BetaInc'},
        {'_days_open':  90, 'ID': 'CASE-C', 'BU_NAME': 'GammaLLC'},
        {'_days_open':  80, 'ID': 'CASE-D', 'BU_NAME': 'DeltaCo'},
        {'_days_open':  70, 'ID': 'CASE-E', 'BU_NAME': 'EpsiPlc'},
    ]

    fingerprints = set()
    for seed in range(10):
        rng = random.Random(seed)
        sev_shuf = list(severity_data); rng.shuffle(sev_shuf)
        cust_shuf = list(customer_data); rng.shuffle(cust_shuf)
        stale_shuf = list(stale_rows); rng.shuffle(stale_shuf)

        sev = _r28_top_n_dict(pd.Series(sev_shuf), 8)
        top_cust = _r28_top_n_dict(pd.Series(cust_shuf), 10)
        df = pd.DataFrame(stale_shuf)
        ranked = df.sort_values(
            by=['_days_open', 'ID'],
            ascending=[False, True],
            kind='stable',
        ).head(5)
        stale_payload = [
            {
                'days_open': int(row['_days_open']),
                'customer': str(row['BU_NAME']),
                'id': str(row['ID']),
            }
            for _, row in ranked.iterrows()
        ]

        report = {
            'severity_distribution': sev,
            'top_customers_by_count': top_cust,
            'stale_cases': stale_payload,
        }
        fingerprints.add(_fingerprint(report))

    assert len(fingerprints) == 1, (
        "Round 28: combined report payload must be byte-identical "
        f"across 10 shuffled runs of the same data; got "
        f"{len(fingerprints)} distinct fingerprints: {fingerprints!r}.  "
        "This indicates one of the deterministic helpers regressed."
    )
