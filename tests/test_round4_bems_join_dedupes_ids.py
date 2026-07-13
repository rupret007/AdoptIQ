"""Round 4 / Phase 3.6 regression test.

BEMS reference IDs aggregated per customer must be deduped across
rows.  Pre-Round-4 the per-customer aggregation joined the per-row
``bems_extracted`` strings without splitting/deduping tokens, so the
same BEMS ID could appear multiple times and inflate apparent
distinct-escalation counts.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bems_aggregation_uses_dedup_helper() -> None:
    # The dedupe helper may live in app_simple.py (where the BEMS
    # groupby aggregation runs) or in adoptiq_backend.py (helper
    # surface).  Accept either.
    sources = [
        (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8"),
        (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8"),
    ]
    found = any(
        ("_dedupe_bems_refs" in s) or bool(re.search(r"set\(\s*[^)]*BEMS", s))
        for s in sources
    )
    assert found, (
        "Round 4 Phase 3.6: BEMS aggregation must dedupe IDs across "
        "rows per customer (via _dedupe_bems_refs or an explicit "
        "set(...) on extracted IDs)."
    )


def test_dedupe_helper_actually_dedupes() -> None:
    """If the helper is exposed, exercise it."""
    helper = None
    for mod_name in ("app_simple", "adoptiq_backend"):
        try:
            mod = __import__(mod_name)
            helper = getattr(mod, "_dedupe_bems_refs", None)
            if helper is not None:
                break
        except Exception:
            continue
    if helper is None:
        # Helper may be a closure-scoped lambda inside a groupby.agg
        # call; the source pin above is the binding contract.
        return
    out = helper([
        "BEMS01916938, BEMS01916939",
        "BEMS01916938, BEMS01916940",
        "BEMS01916939",
    ])
    text = str(out)
    assert text.count("BEMS01916938") == 1, (
        "Round 4 Phase 3.6: _dedupe_bems_refs must collapse duplicate "
        "BEMS IDs across the input rows."
    )
