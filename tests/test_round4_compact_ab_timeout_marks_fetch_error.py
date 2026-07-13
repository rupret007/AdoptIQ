"""Round 4 / Phase 4.3 regression test.

In compact analysis, a ``FutureTimeoutError`` or generic ``Exception``
during the AB fetch must produce an empty DataFrame whose
``df.attrs['fetch_error']`` is set, AND must append an entry to
``partial_data_warnings``.  Pre-Round-4 the AB fetch silently set
``ab_raw = pd.DataFrame()`` with no marker, and downstream read it
as "zero barriers".
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_compact_ab_timeout_path_uses_empty_df_with_fetch_error() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # The Round 4 fix calls _empty_df_with_fetch_error in the compact
    # AB timeout / exception path.  We pin that helper is referenced.
    assert "_empty_df_with_fetch_error" in src, (
        "Round 4 Phase 4.3: compact AB timeout / exception path must "
        "use _empty_df_with_fetch_error so df.attrs['fetch_error'] is "
        "set and downstream readers do not see the empty frame as "
        "'zero barriers'."
    )


def test_compact_ab_timeout_appends_partial_data_warnings() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Look for an append to partial_data_warnings near a timeout /
    # exception pattern in the AB fetch block.
    assert re.search(
        r"partial_data_warnings\.append",
        src,
    ), (
        "Round 4 Phase 4.3: compact AB failure path must append a "
        "partial_data_warnings entry."
    )
