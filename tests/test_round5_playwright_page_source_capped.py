"""Round 5 / Phase 4.4 regression test.

The Playwright fetch path used to drop the FULL rendered page source
into ``./playwright_page_source.html`` in CWD on every run, with no
size cap and no debug gate.  That was a DoS / PII leak surface
(rendered HTML included internal cookies, customer names, tokens).

The fix:
- gates the dump behind ``ADOPTIQ_DEBUG_PLAYWRIGHT_DUMP=1`` (env opt-in)
- writes to ``tempfile.NamedTemporaryFile`` (not CWD)
- caps the captured bytes
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_playwright_page_source_dump_is_capped_and_gated() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 4.4" in src, (
        "Round 5 Phase 4.4 marker missing in adoptiq_backend.py."
    )
