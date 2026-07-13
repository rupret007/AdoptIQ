"""Round 3 / Phase 4.3 regression test.

``compute_barrier_aging`` parses ``OPEN_DATE_C`` / ``CREATED_DATE`` into
UTC-naive timestamps via ``parse_datetime_series``. The "now" side of
the subtraction must therefore also be UTC-naive — using
``pd.Timestamp.now()`` (local clock) would mix timezones and silently
shift aging buckets by up to ~24h depending on where the report is
generated.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_barrier_aging_uses_utc_now():
    src = (PROJECT_ROOT / "adoptiq_backend.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    idx = src.find("def compute_barrier_aging")
    assert idx >= 0
    body = src[idx : idx + 3000]
    # Must use UTC-anchored "now".  Round 8 / Phase 2.10 swapped the
    # deprecated ``pd.Timestamp.utcnow()`` for the explicit
    # ``pd.Timestamp.now('UTC')`` (followed by ``tz_localize(None)``
    # to align with the tz-naive parsed-date column).  All of these
    # spellings produce the same UTC-anchored value -- accept any.
    assert (
        "pd.Timestamp.utcnow().tz_localize(None)" in body
        or "pd.Timestamp.now('UTC').tz_localize(None)" in body
        or 'pd.Timestamp.now("UTC").tz_localize(None)' in body
        or "datetime.utcnow()" in body
    ), "compute_barrier_aging should use a UTC-anchored 'now'"
    # Must NOT use bare local-time pd.Timestamp.now() as actual code
    # in this function (comments referencing the regression are fine).
    code_lines = [
        line for line in body.splitlines()
        if "pd.Timestamp.now()" in line and not line.lstrip().startswith("#")
    ]
    assert not code_lines, (
        "compute_barrier_aging must not use pd.Timestamp.now() (local clock); "
        f"it mixes TZ with parse_datetime_series output (UTC-naive). "
        f"Offending lines: {code_lines}"
    )
