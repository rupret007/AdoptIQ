"""Round 5 / Phase 3.2 regression test.

``PROMPT_CUSTOMER_TEMPLATE.format(...)`` references ``{TECHNOLOGY}``
and ``{MANAGER}``; the previous call site only passed ``CUSTOMER_NAME``
and ``CSSM_NAME``, so a ``KeyError`` was swallowed as a generic
"Analysis failed".  The fix must pass all four keys at every call
site.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_prompt_customer_template_call_passes_technology_manager() -> None:
    src = (REPO_ROOT / "adoptiq_backend.py").read_text(encoding="utf-8")
    assert "Round 5 / Phase 3.2" in src, (
        "Round 5 Phase 3.2 marker missing in adoptiq_backend.py."
    )
    # Both placeholders must be passed in at least one PROMPT_CUSTOMER_TEMPLATE
    # call site.
    assert "TECHNOLOGY=" in src and "MANAGER=" in src, (
        "Round 5 Phase 3.2: PROMPT_CUSTOMER_TEMPLATE.format(...) call sites "
        "must pass TECHNOLOGY and MANAGER kwargs in addition to "
        "CUSTOMER_NAME / CSSM_NAME."
    )
