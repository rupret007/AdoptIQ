"""Round 4 / Phase 5.1 regression test.

The compact analysis path must propagate ``data_retrieved_at`` from
the prefetch context, NOT pass ``None`` to the cover page / EI
formatter.  Pre-Round-4 the compact path checked
``'data_retrieved_at' in locals()`` (always False on the cold path),
so reports shipped without an honest snapshot anchor.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_compact_path_assigns_data_retrieved_at_from_prefetch_meta() -> None:
    src = (REPO_ROOT / "app_simple.py").read_text(encoding="utf-8")
    # Pin presence of the new _compact_prefetch_meta capture variable.
    assert "_compact_prefetch_meta" in src, (
        "Round 4 Phase 5.1: compact analysis must capture "
        "prefetch_ctx.data_retrieved_at into _compact_prefetch_meta "
        "(or equivalent) so the cover page receives a real timestamp."
    )
    # Pin that data_retrieved_at is later assigned from that meta.
    assert re.search(
        r"data_retrieved_at\s*=\s*_compact_prefetch_meta",
        src,
    ) or re.search(
        r"_compact_prefetch_meta.*data_retrieved_at",
        src,
        flags=re.DOTALL,
    ), (
        "Round 4 Phase 5.1: data_retrieved_at must be reassigned from "
        "_compact_prefetch_meta after the prefetch ThreadPoolExecutor "
        "completes."
    )
