"""Round 6 / Phase 6.17 regression test.

Persisted status timestamps must use the canonical UTC ISO-Z helper
(``_now_utc_iso_z``).
"""
from __future__ import annotations
from source_shape_utils import assert_in_source
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_status_timestamps_utc_iso_z() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert_in_source(src, "Round 6 / Phase 6.17", label='src')
    assert_in_source(src, "_now_utc_iso_z", label='src')
    assert "datetime.now().isoformat()" not in src, (
        "Naive ``datetime.now().isoformat()`` regressed; must use "
        "_now_utc_iso_z()."
    )
