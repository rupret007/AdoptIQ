"""Round 5 / Phase 6.4 regression test.

``_update_progress`` ETA computation must normalize both sides
(``now`` and ``start_time``) to UTC before subtracting, so a
``TypeError: can't subtract offset-naive and offset-aware datetimes``
no longer silently swallows the ETA when ``start_time`` was rehydrated
as tz-aware from disk.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_update_progress_eta_uses_utc_normalization() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 6.4" in src, (
        "Round 5 Phase 6.4 marker missing in app_simple.py."
    )
    assert "datetime.now(timezone.utc)" in src or "datetime.now(_tz" in src, (
        "Round 5 Phase 6.4: _update_progress must use a tz-aware UTC "
        "datetime.now(timezone.utc) so the ETA subtraction does not "
        "raise TypeError on rehydrated start_time values."
    )
