"""Round 43 / Phase 6 regression test.

Pin ``report_consistency.validate_report_consistency`` to emit a structured
``[CONSISTENCY] PM drift key=<key> portfolio=<v> canonical=<v>`` log line
BEFORE every ``errors.append("Portfolio metric mismatch: ...")`` call.

Pre-fix the operator only had the opaque ``Portfolio metric mismatch:
total_barriers does not match normalized adoption barriers.`` error message
and had to do log archaeology to discover ``portfolio=72 canonical=68``.
Round 43 / Phase 6 makes the next failure one
``grep '[CONSISTENCY] PM drift'`` away from the failing key + both sides
of the comparison.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_total_barriers_drift_emits_structured_log(caplog) -> None:
    """When ``portfolio_metrics["total_barriers"]`` mismatches
    ``count_total_barriers(ab_df)``, the validator MUST emit a structured
    log line carrying both values.
    """
    from report_consistency import validate_report_consistency

    ab_df = pd.DataFrame({
        "ID": [f"AB-{i:03d}" for i in range(5)],
        "customer_name": [f"Customer {i}" for i in range(5)],
    })
    csone_df = pd.DataFrame({"customer_name": [f"Customer {i}" for i in range(3)]})

    pm = {
        "total_customers": 5,
        "total_barriers": 99,  # intentional drift -- canonical is 5
        "total_cases": 3,
        "bems_count": 0,
    }
    with caplog.at_level(logging.ERROR, logger="report_consistency"):
        validate_report_consistency(
            ab_df,
            csone_df,
            portfolio_metrics=pm,
            customer_universe=[f"Customer {i}" for i in range(5)],
        )

    drift_lines = [
        rec for rec in caplog.records
        if "PM drift" in rec.getMessage() and "total_barriers" in rec.getMessage()
    ]
    assert drift_lines, (
        "Round 43 / Phase 6: validator MUST emit a structured "
        "'[CONSISTENCY] PM drift key=total_barriers ...' log line when "
        "total_barriers drifts."
    )
    msg = drift_lines[0].getMessage()
    assert "portfolio=99" in msg, f"log must include portfolio side: {msg!r}"
    assert "canonical=5" in msg, f"log must include canonical side: {msg!r}"


def test_total_customers_drift_emits_structured_log(caplog) -> None:
    """Same contract for ``total_customers`` (Round 25 / Phase A
    cross-format parity gate, where Round 42 / Phase 1 introduced the
    'Word headline must mirror Excel Summary row' enforcement).
    """
    from report_consistency import validate_report_consistency

    ab_df = pd.DataFrame({
        "ID": [f"AB-{i:03d}" for i in range(5)],
        "customer_name": [f"Customer {i}" for i in range(5)],
    })
    csone_df = pd.DataFrame()

    pm = {
        "total_customers": 999,  # intentional drift -- canonical is 5
        "total_barriers": 5,
        "total_cases": 0,
        "bems_count": 0,
    }
    with caplog.at_level(logging.ERROR, logger="report_consistency"):
        validate_report_consistency(
            ab_df,
            csone_df,
            portfolio_metrics=pm,
            customer_universe=[f"Customer {i}" for i in range(5)],
        )

    drift_lines = [
        rec for rec in caplog.records
        if "PM drift" in rec.getMessage() and "total_customers" in rec.getMessage()
    ]
    assert drift_lines, (
        "Round 43 / Phase 6: validator MUST emit a structured "
        "'[CONSISTENCY] PM drift key=total_customers ...' log line when "
        "total_customers drifts."
    )
    msg = drift_lines[0].getMessage()
    assert "portfolio=999" in msg
    assert "canonical=5" in msg


def test_no_drift_log_when_metrics_agree(caplog) -> None:
    """When portfolio_metrics agrees with the canonical helpers, NO drift
    log line should be emitted (so the noise floor stays low).
    """
    import canonical_metrics as cm
    from report_consistency import validate_report_consistency

    ab_df = pd.DataFrame({
        "ID": [f"AB-{i:03d}" for i in range(5)],
        "customer_name": [f"Customer {i}" for i in range(5)],
    })
    csone_df = pd.DataFrame()
    pm = cm.build_portfolio_metrics(ab_df=ab_df, csone_df=csone_df)
    pm["total_customers"] = cm.count_customers(ab_df=ab_df, csone_df=csone_df)

    with caplog.at_level(logging.ERROR, logger="report_consistency"):
        validate_report_consistency(
            ab_df,
            csone_df,
            portfolio_metrics=pm,
            customer_universe=[f"Customer {i}" for i in range(5)],
        )

    drift_lines = [rec for rec in caplog.records if "PM drift" in rec.getMessage()]
    assert not drift_lines, (
        "Round 43 / Phase 6: validator must NOT emit drift log lines when "
        f"the metrics agree -- got {[l.getMessage() for l in drift_lines]}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
